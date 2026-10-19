"""虚空连接清理:别名召回 + LLM 凭上下文生成实体页。

阶段:
  1. scan_void_links:扫一遍 wiki,把 broken_link 分两类
     - aliasable:近似匹配命中现有页 → 提议加 alias
     - fillable:没近似匹配 → 候选 LLM 生成
  2. run_alias_proposals:把选定的 alias 提议写入 .wiki-schema.md 的 pending 区
     (admin 在 SchemaEditor 二次确认后才会真正生效)
  3. fill_void_entity:LLM 凭 ref 上下文生成 entity 页 → 直写 wiki/entities/{slug}.md
     frontmatter 标 auto_filled=true + confidence=low 便于后续追溯。

设计原则:
  - **不批量自动**:scan 列出候选,admin 选什么才执行什么
  - **不编造**:LLM prompt 明确要求只用提供的 wiki 上下文证据,不足时如实写"待补充"
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.ingestion.validator import is_dirty_entity_name
from app.services.llm import ChatMessage, get_llm_provider
from app.services.llm.base import EXTRACTION_MAX_TOKENS
from app.services.wiki.frontmatter_io import parse_text, serialize
from app.services.wiki.fs_io import atomic_write, safe_join
from app.services.wiki.link_resolver import (
    aggregate_edges,
    extract_links_with_meta,
    iter_wiki_pages,
    rebuild_links_index,
)

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

# 文件名禁用字符(与 fs_io._INVALID_CHARS 对齐);含此类字符的 void target 不可落盘
_INVALID_FS_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


# ---- 别名近似匹配 ----------------------------------------------------------

# 这些后缀剥离再比较:六西格玛管理 → 六西格玛(命中 六西格玛管理概述/六西格玛角色体系)
_STRIP_SUFFIXES = ["概述", "介绍", "总论", "导论", "方法", "管理", "系统", "技术", "理论", "分析"]
_BRACKET_RE = re.compile(r"[（(][^）)]*[)）]")


def _normalize(s: str) -> str:
    """归一化:lower + 去括号补充 + 去空白。"""
    s = _BRACKET_RE.sub("", s)
    s = s.replace(" ", "").replace("　", "")
    return s.lower()


def _strip_suffix(s: str) -> str:
    """去掉一个常见后缀,无则原样返回。"""
    for sfx in _STRIP_SUFFIXES:
        if s.endswith(sfx) and len(s) > len(sfx) + 1:
            return s[: -len(sfx)]
    return s


def _score_alias_candidate(target: str, candidate: str) -> float:
    """计算 broken target 与候选 slug 的近似度。

    取以下规则的 max:
    1. 归一化(去括号/lower)字面相等 → 0.95
    2. 一方含括号、剥离后字面相等 → 0.9
    3. 后缀剥离后字面相等 → 0.85
    4. 去后缀后子串关系且长度比 ≥ 0.6 → 0.7
    5. 子串关系且长度比 ≥ 0.6 → 0.65(不及 0.7 阈值,会被丢弃)
    """
    if not target or not candidate or target == candidate:
        return 0.0  # 同 slug 不需要 alias

    nt = _normalize(target)
    nc = _normalize(candidate)
    if not nt or not nc:
        return 0.0
    if nt == nc:
        return 0.95

    # 括号变体:其中一个原形带括号
    if "（" in target or "(" in target or "（" in candidate or "(" in candidate:
        if nt == nc:
            return 0.9

    # 后缀剥离
    nt_s = _strip_suffix(nt)
    nc_s = _strip_suffix(nc)
    if nt_s and nt_s == nc_s:
        return 0.85
    if nt_s and (nt_s == nc or nt == nc_s):
        return 0.85

    # 子串关系
    if len(nt) >= 2 and len(nc) >= 2:
        if nt in nc and len(nt) / len(nc) >= 0.6:
            return 0.7
        if nc in nt and len(nc) / len(nt) >= 0.6:
            return 0.7
    return 0.0


def _propose_alias(broken_target: str, existing_slugs: list[str]) -> dict | None:
    """给 broken_target 找一个最优 alias 候选。返回 {canonical, score} 或 None。"""
    best = None
    best_score = 0.0
    for slug in existing_slugs:
        sc = _score_alias_candidate(broken_target, slug)
        if sc > best_score:
            best_score = sc
            best = slug
    if best and best_score >= 0.7:
        return {"canonical": best, "score": round(best_score, 3)}
    return None


# ---- broken_link 收集 + 分类 ----------------------------------------------


def scan_void_links(*, wiki_root: Path) -> dict[str, Any]:
    """扫描所有 broken link,按可处理路径分类。

    Returns:
        {
            'aliasable': [{broken_target, suggested_canonical, score, ref_count, src_slugs}],
            'fillable':  [{broken_target, ref_count, src_slugs}],
            'total_broken_targets': int,
            'total_broken_refs': int,
        }
    """
    wiki_root = Path(wiki_root).resolve()
    pages = iter_wiki_pages(wiki_root)
    existing_slugs = sorted(p.stem for p in pages)
    existing_set = set(existing_slugs)

    raw_edges = rebuild_links_index(wiki_root)
    # 按 dst 聚合 ref_count + 收集 src
    by_target: dict[str, dict[str, Any]] = defaultdict(lambda: {"ref_count": 0, "src_slugs": []})
    for e in raw_edges:
        dst = e["dst_slug"]
        if dst in existing_set:
            continue
        by_target[dst]["ref_count"] += int(e.get("occurrences") or 1)
        src = e["src_slug"]
        if src not in by_target[dst]["src_slugs"]:
            by_target[dst]["src_slugs"].append(src)

    aliasable: list[dict] = []
    fillable: list[dict] = []
    for tgt, info in by_target.items():
        proposal = _propose_alias(tgt, existing_slugs)
        item = {
            "broken_target": tgt,
            "ref_count": info["ref_count"],
            "src_slugs": info["src_slugs"][:8],  # 限 8 个,前端不挤
        }
        if proposal:
            item["suggested_canonical"] = proposal["canonical"]
            item["score"] = proposal["score"]
            aliasable.append(item)
        else:
            fillable.append(item)

    aliasable.sort(key=lambda x: x["ref_count"], reverse=True)
    fillable.sort(key=lambda x: x["ref_count"], reverse=True)
    return {
        "aliasable": aliasable,
        "fillable": fillable,
        "total_broken_targets": len(by_target),
        "total_broken_refs": sum(v["ref_count"] for v in by_target.values()),
    }


# ---- LLM 凭上下文生成 entity 页 -------------------------------------------


def _gather_ref_context(wiki_root: Path, target: str, max_chars: int = 6000) -> str:
    """收集所有引用 [[target]] 的 wiki 页里相关段落作为上下文。

    简单实现:全文搜 [[target]],抓出现行前后各 1 行作为 snippet,按页整合。
    """
    pat = re.compile(r"\[\[" + re.escape(target) + r"(?:#[^\]]*)?\]\]")
    parts: list[str] = []
    used = 0
    for p in iter_wiki_pages(wiki_root):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if not pat.search(text):
            continue
        # 抽出含 [[target]] 的段落 (前后各取一段)
        snippets: list[str] = []
        lines = text.splitlines()
        hit_idx = [i for i, ln in enumerate(lines) if pat.search(ln)]
        if not hit_idx:
            continue
        for i in hit_idx[:3]:  # 每页最多 3 处
            lo, hi = max(0, i - 2), min(len(lines), i + 3)
            snippets.append("\n".join(lines[lo:hi]))
        page_block = f"### 来自 [[{p.stem}]]\n" + "\n\n".join(snippets)
        if used + len(page_block) > max_chars:
            break
        parts.append(page_block)
        used += len(page_block)
    return "\n\n---\n\n".join(parts) if parts else "(没有上下文,信息严重不足)"


def _slug_to_safe_filename(slug: str) -> str:
    """slug 已经是中文/英文混合的人类可读 id;直接做文件名,只清理少量危险字符。"""
    s = slug.replace("/", "_").replace("\\", "_").strip()
    return s or "unnamed"


def _collect_referrer_sources(wiki_root: Path, target: str) -> list[str]:
    """收集所有引用 [[target]] 的页面的 sources 并集 → 作为兜底实体继承的 source。

    - 普通页(entity/topic)用其 frontmatter.sources;
    - 引用方本身是 source 章节页时,把该 source slug 自身计入。
    去重保序。
    """
    pat = re.compile(r"\[\[" + re.escape(target) + r"(?:[#|][^\]]*)?\]\]")
    out: list[str] = []
    seen: set[str] = set()
    for p in iter_wiki_pages(wiki_root):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        if not pat.search(text):
            continue
        is_source_page = p.parent.name == "sources"
        if is_source_page and p.stem not in seen:
            seen.add(p.stem)
            out.append(p.stem)
        try:
            fm, _ = parse_text(text)
        except Exception:  # noqa: BLE001
            continue
        for s in fm.get("sources") or []:
            s = str(s).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def _context_to_brief(context: str, *, max_chars: int = 300) -> str:
    """把 ref_context 压成一句近似 brief,供 merger embedding 相似度比对用。"""
    lines = [
        ln.strip()
        for ln in context.splitlines()
        if ln.strip() and not ln.startswith("### 来自") and ln.strip() != "---"
    ]
    return " ".join(lines)[:max_chars]


async def fill_void_entity(*, wiki_root: Path, slug: str) -> dict[str, Any]:
    """虚空实体兜底:先过 merger 判定能否归并到已有实体,否则 LLM 凭上下文建页。

    流程:
      1. 命中已有实体(字面/别名/embedding 相似)→ **不新建**,返回 merged_into,
         由调用方把 [[slug]] 改写成 [[merged_into]]。
      2. 别名指向尚未建页的 canonical → 在 canonical slug 下建页,并 merged_into=canonical
         让原 [[slug]] 也归一过去。
      3. 真正新建 → **强制写 sources**(继承所有引用此 slug 页面的 source 并集),
         标 auto_filled=true + confidence=low 便于追溯。

    Returns:
        {ok, written, body_chars, error, merged_into?, action?}
    """
    wiki_root = Path(wiki_root).resolve()

    # 原 slug 已存在?跳过
    target_path = safe_join(wiki_root, "wiki", "entities", f"{_slug_to_safe_filename(slug)}.md")
    if target_path.exists():
        return {"ok": False, "written": None, "body_chars": 0, "error": "already exists"}

    context = _gather_ref_context(wiki_root, slug)
    if context.startswith("(没有上下文"):
        return {"ok": False, "written": None, "body_chars": 0, "error": "no_context: 没有任何 wiki 页引用此 slug,无法生成"}

    # ---- merger 判定:命中已有实体则不新建,改为归并 ----
    from app.services.ingestion.merger import load_alias_groups, merge_or_create_entity

    try:
        matched_slug, action, _meta = await merge_or_create_entity(
            candidate_name=slug,
            candidate_brief=_context_to_brief(context),
            wiki_root=wiki_root,
            alias_groups=load_alias_groups(wiki_root),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[void_fill] merger 判定失败,降级直接建页: {e}")
        matched_slug, action = slug, "created"

    if action in ("merged", "merged_via_alias", "merged_via_similarity") and matched_slug != slug:
        # 命中已有实体 → 不新建,告诉调用方把 [[slug]] 归一到 [[matched_slug]]
        logger.info(f"[void_fill] {slug!r} → 归并到已有实体 {matched_slug!r}({action})")
        return {
            "ok": False, "written": None, "body_chars": 0, "error": None,
            "merged_into": matched_slug, "action": action,
        }

    # action ∈ {created, created_no_embedding, suggested_merge};alias_new 时
    # matched_slug 是 canonical(尚未建页)→ 在 canonical 下建页并归一原 slug。
    final_slug = matched_slug or slug
    final_path = safe_join(wiki_root, "wiki", "entities", f"{_slug_to_safe_filename(final_slug)}.md")
    if final_path.exists():
        # canonical 已存在 → 不新建,归一过去
        return {
            "ok": False, "written": None, "body_chars": 0, "error": None,
            "merged_into": final_slug, "action": "merged",
        }

    template_path = PROMPTS_DIR / "void_fill_entity.md"
    if not template_path.exists():
        return {"ok": False, "written": None, "body_chars": 0, "error": f"prompt template missing: {template_path}"}
    template = template_path.read_text(encoding="utf-8")

    today = date.today().isoformat()
    prompt = (
        template
        .replace("{slug}", final_slug)
        .replace("{title}", final_slug)
        .replace("{today}", today)
        .replace("{ref_context}", context)
    )
    messages = [
        ChatMessage(role="system", content="你是工业工程领域的知识工程师。严格按要求输出结构化 markdown,不能编造。"),
        ChatMessage(role="user", content=prompt),
    ]

    provider = get_llm_provider()
    # 死链清理判断依赖模型推理,显式恢复(reasoning_effort=None 移除默认的 none)
    raw = await provider.chat(messages, stream=False, reasoning_effort=None, max_tokens=EXTRACTION_MAX_TOKENS)
    md = raw if isinstance(raw, str) else ""
    md = md.strip()
    # LLM 可能用 ```markdown 包一层,剥掉
    md = re.sub(r"^```(?:markdown|md)?\s*\n", "", md)
    md = re.sub(r"\n```\s*$", "", md)
    if not md or "---" not in md:
        return {"ok": False, "written": None, "body_chars": len(md), "error": "LLM 输出无 frontmatter"}

    # 解析 + 强制注入 auto_filled / confidence / kind / version / created / sources
    try:
        fm, body = parse_text(md)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "written": None, "body_chars": len(md), "error": f"frontmatter 解析失败: {e}"}

    fm["slug"] = final_slug
    fm.setdefault("title", final_slug)
    fm["kind"] = "entity"  # 强制 entity(后续若发现是 topic 由人工改)
    fm["auto_filled"] = True
    fm["confidence"] = "low"
    fm.setdefault("version", 1)
    fm.setdefault("created", today)
    fm["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    # 强制写 sources:继承所有引用此 slug 页面的 source 并集(原 slug 视角去找引用方)
    inherited = _collect_referrer_sources(wiki_root, slug)
    existing_src = [str(s).strip() for s in (fm.get("sources") or []) if str(s).strip()]
    merged_src: list[str] = []
    for s in existing_src + inherited:
        if s and s not in merged_src:
            merged_src.append(s)
    fm["sources"] = merged_src

    final_md = serialize(fm, body)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    await atomic_write(final_path, final_md)
    rel = final_path.relative_to(wiki_root).as_posix()
    logger.info(f"[void_fill] wrote {rel} ({len(final_md)} chars, sources={merged_src})")
    result: dict[str, Any] = {"ok": True, "written": rel, "body_chars": len(final_md), "error": None}
    if final_slug != slug:
        # 在 canonical 下建的页 → 原 [[slug]] 需要归一到 canonical
        result["merged_into"] = final_slug
        result["action"] = "created"
    return result


# ---- strip:把 [[broken_target]] 改成纯文本 -------------------------------


async def strip_void_link_refs(
    *, wiki_root: Path, broken_targets: list[str]
) -> dict[str, Any]:
    """把 wiki 文件里所有指向 broken_target 的 [[X]] / [[X#anchor]] / [[X|alias]]
    改写成纯文本 X(或 alias)。这样 lint 就扫不出 broken_link。

    注意:只改 body,不改 frontmatter(那里用的是 yaml list,不是 wikilink 语法)。
    """
    wiki_root = Path(wiki_root).resolve()
    if not broken_targets:
        return {"pages_rewritten": 0, "refs_stripped": 0, "targets": 0}

    # 用 set 提速
    target_set = set(broken_targets)
    pages_rewritten = 0
    refs_stripped = 0
    written_paths: list[str] = []

    # 通用 [[X]] / [[X#anchor]] / [[X|display]] 匹配
    link_pat = re.compile(r"\[\[([^\[\]]+?)\]\]")

    wiki_dir = wiki_root / "wiki"
    if not wiki_dir.exists():
        return {"pages_rewritten": 0, "refs_stripped": 0, "targets": len(broken_targets)}

    for sub in ("entities", "topics", "sources", "queries", "synthesis", "comparisons", "_growth"):
        d = wiki_dir / sub
        if not d.exists():
            continue
        # rglob 递归扫:有些 LLM 生成的页会落在嵌套子目录里(例 entities/ISO/TS16949.md)
        for p in sorted(d.rglob("*.md")):
            try:
                text = p.read_text(encoding="utf-8")
                fm, body = parse_text(text)
            except Exception:  # noqa: BLE001
                continue

            counter = {"n": 0}

            def _repl(m, _ctx=counter):
                inner = m.group(1).strip()
                # 解出目标 / anchor / display
                display = inner
                target_full = inner  # 含 anchor 的完整目标
                if "|" in inner:
                    target_full, display = inner.split("|", 1)
                    target_full = target_full.strip()
                    display = display.strip()
                # 不拆 anchor:scan 那边 dst_slug 是含 anchor 的整体
                # 也额外尝试拆掉 anchor 的页面部分(双重保险)
                target_page = target_full.split("#", 1)[0].strip() if "#" in target_full else target_full
                if target_full in target_set or target_page in target_set:
                    _ctx["n"] += 1
                    # 用 display(或 target 本身)替代,丢弃双链括号
                    # 不带 anchor 因为目标本就不存在
                    return display if display != inner else target_page
                return m.group(0)

            new_body = link_pat.sub(_repl, body)
            if counter["n"] > 0:
                pages_rewritten += 1
                refs_stripped += counter["n"]
                await atomic_write(p, serialize(fm, new_body))
                written_paths.append(f"wiki/{sub}/{p.name}")

    logger.info(
        f"[void_strip] pages_rewritten={pages_rewritten} refs_stripped={refs_stripped}"
    )

    # 同步改动到 wiki_links DB(去掉的边要从表里删)
    if written_paths:
        try:
            from app.core.db import session_scope
            from app.services.ingestion.pipeline import _sync_db_indices
            with session_scope() as db:
                _sync_db_indices(db, wiki_root=wiki_root, written_paths=written_paths)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[void_strip] DB sync failed: {e}")

    return {
        "pages_rewritten": pages_rewritten,
        "refs_stripped": refs_stripped,
        "targets": len(broken_targets),
    }


# ---- 后处理:别名 normalize + DB 同步 + 图谱重建 ---------------------------


_INNER_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]]+?))?\]\]")


async def repair_inline_comment_links(*, wiki_root: Path) -> dict[str, Any]:
    """把被 LLM 错塞进 [[...]] 内部的 HTML 注释搬到括号外,还原干净双链。

    形如 `[[工业工程<!-- type: 相关 --> <!-- confidence: EXTRACTED -->]]`
    会被改写成 `[[工业工程]] <!-- type: 相关 --> <!-- confidence: EXTRACTED -->`。
    这样 target 干净可解析(链接不再虚空),同时保留关系类型注释不丢信息。

    Returns: {pages_rewritten, links_fixed}
    """
    wiki_root = Path(wiki_root).resolve()
    wiki_dir = wiki_root / "wiki"
    if not wiki_dir.exists():
        return {"pages_rewritten": 0, "links_fixed": 0}

    pages_rewritten = 0
    links_fixed = 0
    written_paths: list[str] = []

    for sub in ("entities", "topics", "sources", "queries", "synthesis", "comparisons", "_growth"):
        d = wiki_dir / sub
        if not d.exists():
            continue
        for p in sorted(d.rglob("*.md")):
            try:
                text = p.read_text(encoding="utf-8")
                fm, body = parse_text(text)
            except Exception:  # noqa: BLE001
                continue
            counter = {"n": 0}

            def _repl(m, _ctx=counter):
                inner = m.group(1)
                alias = m.group(2)
                if "<!--" not in inner:
                    return m.group(0)
                comments = "".join(_INNER_COMMENT_RE.findall(inner))
                clean = _INNER_COMMENT_RE.sub("", inner).strip()
                if not clean:
                    return m.group(0)
                _ctx["n"] += 1
                link = f"[[{clean}|{alias.strip()}]]" if alias else f"[[{clean}]]"
                return f"{link} {comments}" if comments else link

            new_body = _WIKILINK_RE.sub(_repl, body)
            if counter["n"] > 0:
                pages_rewritten += 1
                links_fixed += counter["n"]
                await atomic_write(p, serialize(fm, new_body))
                written_paths.append(f"wiki/{sub}/{p.name}")

    if written_paths:
        try:
            from app.core.db import session_scope
            from app.services.ingestion.pipeline import _sync_db_indices
            with session_scope() as db:
                _sync_db_indices(db, wiki_root=wiki_root, written_paths=written_paths)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[void_repair] DB sync failed: {e}")

    logger.info(
        f"[void_repair] inline-comment links fixed: pages={pages_rewritten} links={links_fixed}"
    )
    return {"pages_rewritten": pages_rewritten, "links_fixed": links_fixed}


async def apply_alias_rewrites(
    *, wiki_root: Path, mapping: dict[str, str]
) -> dict[str, Any]:
    """把 wiki 正文里的 [[broken]] 直接改写成 [[canonical]](broken→现有页的近似命中)。

    与 _full_wiki_alias_normalize 的区别:那个只认 .wiki-schema.md 里登记的别名;
    这里直接吃 scan 给出的 aliasable 提议(broken_target → suggested_canonical),
    无需人工先把别名写进 schema,实现"近似命中自动归一"。

    Returns: {pages_rewritten, links_rewritten}
    """
    wiki_root = Path(wiki_root).resolve()
    if not mapping:
        return {"pages_rewritten": 0, "links_rewritten": 0}

    wiki_dir = wiki_root / "wiki"
    if not wiki_dir.exists():
        return {"pages_rewritten": 0, "links_rewritten": 0}

    pages_rewritten = 0
    links_rewritten = 0
    written_paths: list[str] = []

    for sub in ("entities", "topics", "sources", "queries", "synthesis", "comparisons", "_growth"):
        d = wiki_dir / sub
        if not d.exists():
            continue
        for p in sorted(d.rglob("*.md")):
            try:
                text = p.read_text(encoding="utf-8")
                fm, body = parse_text(text)
            except Exception:  # noqa: BLE001
                continue
            counter = {"n": 0}

            def _repl(m, _ctx=counter):
                inner = m.group(1).strip()
                alias = m.group(2)
                tgt, anchor = (inner.split("#", 1) + [""])[:2]
                tgt = tgt.strip()
                canon = mapping.get(tgt)
                if not canon or canon == tgt:
                    return m.group(0)
                _ctx["n"] += 1
                anchor_s = f"#{anchor.strip()}" if anchor else ""
                if alias:
                    return f"[[{canon}{anchor_s}|{alias.strip()}]]"
                return f"[[{canon}{anchor_s}]]"

            new_body = _WIKILINK_RE.sub(_repl, body)
            if counter["n"] > 0:
                pages_rewritten += 1
                links_rewritten += counter["n"]
                await atomic_write(p, serialize(fm, new_body))
                written_paths.append(f"wiki/{sub}/{p.name}")

    if written_paths:
        try:
            from app.core.db import session_scope
            from app.services.ingestion.pipeline import _sync_db_indices
            with session_scope() as db:
                _sync_db_indices(db, wiki_root=wiki_root, written_paths=written_paths)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[void_alias] DB sync failed: {e}")

    logger.info(
        f"[void_alias] alias rewrites applied: pages={pages_rewritten} links={links_rewritten}"
    )
    return {"pages_rewritten": pages_rewritten, "links_rewritten": links_rewritten}


async def apply_post_run_sync(
    *, wiki_root: Path, written_pages: list[str] | None = None
) -> dict[str, Any]:
    """void_links/run 收尾:让本次的 alias 与新页面真正生效。

    1. 跑全 wiki 的 alias normalize:把 wiki 文件里出现的 [[别名]] 改写成 [[canonical]]
       (这一步内部已经会 sync 改动到 wiki_pages / wiki_links DB)
    2. 对 LLM 新生成的页面单独 sync_db_indices(因为它们没经过 normalize 改写也得入索引)
    3. 重建 graph-data.json,这样图谱页能立刻看到新节点

    所有步骤都是 best-effort:任一失败都仅记 warning,不阻塞其它步骤。

    Returns:
        {alias_normalized, new_pages_synced, graph_rebuilt, errors: [...]}
    """
    wiki_root = Path(wiki_root).resolve()
    result: dict[str, Any] = {
        "alias_normalized": False,
        "new_pages_synced": 0,
        "graph_rebuilt": False,
        "errors": [],
    }

    # 1. alias normalize (内部如果改了文件会 sync DB)
    try:
        from app.services.ingestion.pipeline import _full_wiki_alias_normalize
        await _full_wiki_alias_normalize(wiki_root=wiki_root)
        result["alias_normalized"] = True
        logger.info("[void_post_sync] alias normalize done")
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"alias_normalize: {e}")
        logger.warning(f"[void_post_sync] alias_normalize failed: {e}")

    # 2. LLM 新生成的页:_sync_db_indices(独立事务,失败不阻塞)
    if written_pages:
        try:
            from app.core.db import session_scope
            from app.services.ingestion.pipeline import _sync_db_indices
            with session_scope() as db:
                stat = _sync_db_indices(db, wiki_root=wiki_root, written_paths=written_pages)
                result["new_pages_synced"] = stat.get("pages_upserted", 0)
            logger.info(
                f"[void_post_sync] DB synced {result['new_pages_synced']} new pages"
            )
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"db_sync: {e}")
            logger.warning(f"[void_post_sync] db_sync failed: {e}")

    # 3. 重建 graph-data.json
    try:
        from app.services.graph.builder import (
            build_graph_data,
            write_graph_data_json,
        )
        payload = await build_graph_data(wiki_root=wiki_root)
        await write_graph_data_json(wiki_root=wiki_root, payload=payload)
        result["graph_rebuilt"] = True
        result["graph_nodes"] = len(payload.get("nodes") or [])
        result["graph_edges"] = len(payload.get("edges") or [])
        logger.info(
            f"[void_post_sync] graph rebuilt nodes={result['graph_nodes']} "
            f"edges={result['graph_edges']}"
        )
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"graph_rebuild: {e}")
        logger.warning(f"[void_post_sync] graph_rebuild failed: {e}")

    return result


# ---- 顶层:零虚空链接自动化 ------------------------------------------------


async def ensure_zero_void_links(
    *,
    wiki_root: Path,
    fill_limit: int = 60,
    strip_residual: bool = True,
) -> dict[str, Any]:
    """一站式清理,目标:扫描结束时 broken link = 0。

    管线(每步幂等,失败仅告警不中断):
      0. repair_inline_comment_links:把 [[X<!--...-->]] 内部注释搬出括号 →
         之前因注释残渣"假性虚空"的链接立刻解析为现有页。
      1. apply_alias_rewrites:把 scan 的 aliasable(近似命中现有页,score≥0.7)
         正文 [[broken]] → [[canonical]],无需人工先登记 schema。
      2. fill_void_entity × N:对剩余 fillable(无近似匹配)逐个 LLM 凭上下文建页。
      3. strip_void_link_refs:仍然 broken 的(LLM 没上下文/失败的)降级为纯文本,
         确保最终扫描 0 虚空。strip_residual=False 时跳过(调试用)。

    Returns:
        {repaired, alias_applied, filled, stripped, before, after, rounds}
    """
    wiki_root = Path(wiki_root).resolve()
    out: dict[str, Any] = {
        "repaired": 0,
        "alias_applied": 0,
        "filled": [],
        "stripped": 0,
        "before": 0,
        "after": 0,
        "rounds": 0,
    }

    before = scan_void_links(wiki_root=wiki_root)
    out["before"] = before.get("total_broken_targets", 0)

    # Step 0:修复内联注释污染的链接
    try:
        rep = await repair_inline_comment_links(wiki_root=wiki_root)
        out["repaired"] = rep.get("links_fixed", 0)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ensure_zero_void] repair failed: {e}")

    # Step 1:aliasable 近似归一
    try:
        scan = scan_void_links(wiki_root=wiki_root)
        alias_map = {
            it["broken_target"]: it["suggested_canonical"]
            for it in (scan.get("aliasable") or [])
            if it.get("suggested_canonical")
        }
        if alias_map:
            ar = await apply_alias_rewrites(wiki_root=wiki_root, mapping=alias_map)
            out["alias_applied"] = ar.get("links_rewritten", 0)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ensure_zero_void] alias rewrite failed: {e}")

    # Step 2:fillable 逐个 LLM 建页(最多两轮,发现二级 broken)
    budget = max(0, fill_limit)
    filled: list[str] = []
    written_rel: list[str] = []
    merge_map: dict[str, str] = {}  # broken_target → 命中/新建的 canonical slug,稍后归一
    for _round in range(2):
        out["rounds"] = _round + 1
        if budget <= 0:
            break
        scan = scan_void_links(wiki_root=wiki_root)
        fillable = scan.get("fillable") or []
        if not fillable:
            break
        progressed = False
        for item in fillable:
            if budget <= 0:
                break
            target = item.get("broken_target")
            if not target or "<!--" in target:
                continue
            # 含文件系统禁用字符的 target(O*NET 等)无法落盘成 {slug}.md,
            # 且正文链接也清不成同名 → 直接跳过填充,留给 Step 3 strip 成纯文本。
            if _INVALID_FS_CHARS.search(target):
                continue
            # 源 slug/文件名格式(YYYY-MM-DD-xxx)的伪实体:不是工业工程概念,
            # 不该为它建占位页,跳过 fill 留给 Step 3 strip 成纯文本。
            # (与 validator.is_dirty_entity_name 同源判定,作为 step1 过滤之外的第二道防线)
            if is_dirty_entity_name(target):
                logger.info(f"[ensure_zero_void] skip dirty source-slug target {target!r}, leave to strip")
                continue
            try:
                res = await fill_void_entity(wiki_root=wiki_root, slug=target)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[ensure_zero_void] fill {target!r} failed: {e}")
                continue
            merged_into = res.get("merged_into")
            if merged_into and merged_into != target:
                # merger 命中已有实体 / 在 canonical 下建页 → 把 [[target]] 归一过去
                merge_map[target] = merged_into
                progressed = True
            if res.get("ok"):
                filled.append(target)
                if res.get("written"):
                    written_rel.append(res["written"])
                budget -= 1
                progressed = True
        if not progressed:
            break
    out["filled"] = filled

    # merger 命中/canonical 新建 → 把 [[broken]] 正文双链归一到 canonical(消虚空)
    if merge_map:
        try:
            mr = await apply_alias_rewrites(wiki_root=wiki_root, mapping=merge_map)
            out["alias_applied"] = out.get("alias_applied", 0) + mr.get("links_rewritten", 0)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ensure_zero_void] merge-map rewrite failed: {e}")

    # 新建页可能含 [[别名]] / 触发二级链接 → normalize + DB 同步
    if filled:
        try:
            from app.services.ingestion.pipeline import _full_wiki_alias_normalize
            await _full_wiki_alias_normalize(wiki_root=wiki_root)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ensure_zero_void] post-fill normalize failed: {e}")
        if written_rel:
            try:
                from app.core.db import session_scope
                from app.services.ingestion.pipeline import _sync_db_indices
                with session_scope() as db:
                    _sync_db_indices(db, wiki_root=wiki_root, written_paths=written_rel)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[ensure_zero_void] post-fill db sync failed: {e}")

    # Step 3:仍 broken 的全部 strip 成纯文本,保证最终 0 虚空
    if strip_residual:
        scan = scan_void_links(wiki_root=wiki_root)
        residual = [
            it["broken_target"]
            for it in (scan.get("aliasable") or []) + (scan.get("fillable") or [])
            if it.get("broken_target")
        ]
        if residual:
            try:
                st = await strip_void_link_refs(wiki_root=wiki_root, broken_targets=residual)
                out["stripped"] = st.get("refs_stripped", 0)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[ensure_zero_void] strip failed: {e}")

    after = scan_void_links(wiki_root=wiki_root)
    out["after"] = after.get("total_broken_targets", 0)
    logger.info(
        f"[ensure_zero_void] before={out['before']} after={out['after']} "
        f"repaired={out['repaired']} alias={out['alias_applied']} "
        f"filled={len(out['filled'])} stripped={out['stripped']}"
    )
    return out
