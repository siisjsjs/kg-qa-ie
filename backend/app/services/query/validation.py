"""query/validation.py:chunk 引用协议的答案校验与清洗(改造计划 §6.3)。

引用形态:
  [[slug#En]]  — 知识库 chunk 引用
  [[web#Wn]]   — 网络来源引用
  [[slug]]     — 页面级引用(兼容降级)

清洗规则:
  - En/Wn 编号不存在于证据池 → 降级为 [[slug]](slug 在库里存在时)或删除
  - En 存在但 slug 写错 → 纠正为证据池里的真实 slug
  - 页面级 slug 不存在 → 回磁盘找一次(safe_slug 防伪造),找不到删除
  - 删除后整行只剩列表符号 → 连行删除(复用旧 evidence 的策略)
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.query.evidence_pool import EvidencePool
from app.services.wiki.fs_io import safe_join, safe_slug

CITATION_TOKEN_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\[\]]+?)?\]\]")
# 模型偶尔把引用包进行内代码:`[[slug#E3]]` → Markdown 渲染成原样文本。
# 清洗第一步先剥反引号,让 token 回到可渲染/可校验的裸形态。
_CODE_WRAPPED_CITATION_RE = re.compile(r"`+\s*(\[\[[^\[\]`]+\]\])\s*`+")
_REF_RE = re.compile(r"^[EW]\d+$", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?%?")
# 去掉无效引用后,整行只剩列表符号/编号/标点 → 视为空行删掉。
_LIST_RESIDUE_RE = re.compile(r"[\s>*+\-\d.)、,，;；:：·]*")
_KIND_DIRS = ("entities", "topics", "sources", "queries", "synthesis", "comparisons")


def parse_citation_token(token: str) -> dict:
    """把 [[...]] 内部 token 解析成 {type, slug, ref}。

    type ∈ {"chunk", "web", "page"};非法形态归为 page(slug=原文)交给存在性校验。
    """
    token = (token or "").strip()
    if "#" in token:
        left, right = token.split("#", 1)
        left, right = left.strip(), right.strip()
        if _REF_RE.match(right):
            ref = right.upper()
            if left.lower() == "web" or ref.startswith("W"):
                return {"type": "web", "slug": None, "ref": ref}
            return {"type": "chunk", "slug": left, "ref": ref}
        # '#' 后不是编号(如 [[slug#章节]]):当页面引用,丢锚点
        return {"type": "page", "slug": left, "ref": None}
    # 裸编号 [[E4]] / [[W2]](模型偷懒漏写 slug):按证据引用处理,
    # slug 留空由清洗阶段从证据池反查补全,而不是当成页面 "E4" 误删。
    if _REF_RE.match(token):
        ref = token.upper()
        if ref.startswith("W"):
            return {"type": "web", "slug": None, "ref": ref}
        return {"type": "chunk", "slug": None, "ref": ref}
    return {"type": "page", "slug": token, "ref": None}


def extract_citation_refs(answer: str) -> list[dict]:
    """从答案抽结构化引用,按 raw token 去重保序。"""
    seen: set[str] = set()
    out: list[dict] = []
    for m in CITATION_TOKEN_RE.finditer(answer or ""):
        raw = m.group(1).strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        out.append({"raw": raw, **parse_citation_token(raw)})
    return out


def extract_cited_slugs(answer: str) -> list[str]:
    """答案中引用到的 KB 页面 slug(chunk + page 引用,去重保序;web 不算)。"""
    out: list[str] = []
    seen: set[str] = set()
    for ref in extract_citation_refs(answer):
        slug = ref.get("slug")
        if ref["type"] in ("chunk", "page") and slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def _slug_on_disk(slug: str, wiki_root: Path) -> bool:
    """slug 是否真实存在于 wiki(safe_slug 归一不等 → 判伪造直接拒)。"""
    if not slug or safe_slug(slug) != slug:
        return False
    for sub in _KIND_DIRS:
        try:
            p = safe_join(Path(wiki_root), "wiki", sub, f"{slug}.md")
        except ValueError:
            return False
        if p.exists():
            return True
    return False


def validate_and_clean_answer_v2(
    answer: str,
    evidence: EvidencePool,
    *,
    wiki_root: Path,
) -> tuple[str, dict]:
    """快速后校验,不做二次模型调用。返回 (清洗后答案, validation dict)。"""
    answer = _CODE_WRAPPED_CITATION_RE.sub(r"\1", answer or "")
    refs = extract_citation_refs(answer)
    replacements: dict[str, str | None] = {}  # raw → 新 token 内容(None=删除)
    invalid: list[str] = []
    recovered: list[str] = []

    for ref in refs:
        raw = ref["raw"]
        if ref["type"] == "web":
            item = evidence.resolve(ref["ref"])
            if item is None:
                invalid.append(raw)
                replacements[raw] = None
            elif raw != f"web#{ref['ref']}":
                replacements[raw] = f"web#{ref['ref']}"
            continue
        if ref["type"] == "chunk":
            item = evidence.resolve(ref["ref"])
            if item is not None:
                true_slug = str(item.get("slug") or "")
                if true_slug and true_slug != ref["slug"]:
                    # 编号对、slug 写错 → 纠正
                    replacements[raw] = f"{true_slug}#{ref['ref']}"
                continue
            # 编号不存在 → 降级为页面引用(slug 真实存在时),否则删除
            if ref["slug"] and _slug_on_disk(ref["slug"], wiki_root):
                replacements[raw] = ref["slug"]
                recovered.append(ref["slug"])
            else:
                invalid.append(raw)
                replacements[raw] = None
            continue
        # page 引用:证据池里出现过该 slug 视为合法;否则回磁盘找一次
        if ref["slug"] and (
            evidence.slug_exists_in_pool(ref["slug"]) or _slug_on_disk(ref["slug"], wiki_root)
        ):
            if not evidence.slug_exists_in_pool(ref["slug"]):
                recovered.append(ref["slug"])
            if raw != ref["slug"]:
                replacements[raw] = ref["slug"]
            continue
        invalid.append(raw)
        replacements[raw] = None

    kept_lines: list[str] = []
    for line in (answer or "").split("\n"):
        if not line.strip():
            kept_lines.append(line)
            continue
        had_removal = False

        def _sub(m: re.Match[str]) -> str:
            nonlocal had_removal
            raw = m.group(1).strip()
            if raw not in replacements:
                return m.group(0)
            new = replacements[raw]
            if new is None:
                had_removal = True
                return ""
            return f"[[{new}]]"

        stripped = CITATION_TOKEN_RE.sub(_sub, line)
        if had_removal:
            # 删除后整行只剩列表符号/标点 → 连行删掉,不留裸残渣
            if _LIST_RESIDUE_RE.fullmatch(stripped):
                continue
            stripped = re.sub(r"[ \t]{2,}", " ", stripped).rstrip()
            if not line[:1].isspace():
                stripped = stripped.lstrip()
        kept_lines.append(stripped)
    cleaned = "\n".join(kept_lines)

    # ---- 数字支撑校验 ----
    corpus = "\n".join(
        [it.get("text") or "" for it in evidence.kb_items]
        + [it.get("content") or "" for it in evidence.web_items]
    )
    answer_wo_list_numbers = re.sub(r"(?m)^\s*\d+[.)、]\s*", "", cleaned)
    # 行号引用里的数字不算论断数字
    answer_wo_tokens = CITATION_TOKEN_RE.sub("", answer_wo_list_numbers)
    unsupported_numbers = sorted({
        token
        for token in _NUMBER_RE.findall(answer_wo_tokens)
        if token not in corpus and token not in {"1", "2", "3"}
    })

    # ---- 段落级引用覆盖 ----
    paragraphs = [
        p.strip()
        for p in re.split(r"\n\s*\n", cleaned)
        if len(p.strip()) >= 40
        and not p.lstrip().startswith(("#", "```"))
        and "基于通识" not in p
    ]
    cited_paragraphs = sum(1 for p in paragraphs if CITATION_TOKEN_RE.search(p))
    coverage = round(cited_paragraphs / len(paragraphs), 4) if paragraphs else 1.0

    validation = {
        "citations": [r["raw"] for r in refs],
        "invalid_citations": invalid,
        "recovered_citations": list(dict.fromkeys(recovered)),
        "unsupported_numbers": unsupported_numbers,
        "claim_paragraphs": len(paragraphs),
        "cited_paragraphs": cited_paragraphs,
        "paragraph_citation_coverage": coverage,
        "passed": not invalid and not unsupported_numbers,
        "revised": cleaned != (answer or ""),
    }
    return cleaned, validation
