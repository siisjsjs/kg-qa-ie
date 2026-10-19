"""query/page_reader.py:读 grep 候选页生成可塞进 prompt 的精简上下文。

策略(规划文档 §3.3 step 5):
  - 单页字符 ≤ long_threshold(默认 2000)→ 全文塞
  - 超过 → 只读 frontmatter + 前 head_chars 字符 + 命中段落前后 surrounding_lines 行

字符预算(query.page_max_chars / query.page_max_sections / query.context_total_chars)
优先级:system_config > 环境变量 > 代码默认值;多页检索走 read_pages_for_context
共享一个总预算,而不是每页各自截断在同一个上限。
"""

from __future__ import annotations

import os
from pathlib import Path
import re

from app.core.config import settings
from app.services.wiki.frontmatter_io import parse_text
from app.services.wiki.fs_io import safe_join

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

# context 大小同时决定 prompt 处理时间和推理量,而且是超线性的:实测 60k 字符
# context → 37k prompt tokens → TTFT 42~49s / 总 67s;24k 字符 → 14k tokens →
# TTFT 2.9s / 总 8.1s。8000/28000 是在「对比题材料够用」和「首字延迟可接受」之间取的点。
_DEFAULT_PAGE_MAX_CHARS = 8000
_DEFAULT_MAX_SECTIONS = 8
_DEFAULT_TOTAL_CHARS = 28000

_MIN_SECTION_CHARS = 80
_MIN_PAGE_CHARS = 1200
# 省略标记本身也占预算,截断时预留固定宽度,保证产出不超过调用方给的上限
_ELISION_RESERVE = 40


def _read_int_config(
    key: str, env: str, default: int, *, lo: int, hi: int
) -> int:
    """按 system_config > 环境变量/.env > 默认值取整数,并夹到 [lo, hi]。"""
    raw: str | None = None
    try:
        from app.services.admin import config_store

        raw = config_store.get_sync(key, default="") or None
    except Exception:  # noqa: BLE001
        raw = None
    if not raw:
        settings_value = getattr(settings, env.lower(), None)
        raw = str(settings_value) if settings_value not in (None, "") else os.environ.get(env)
    if raw:
        try:
            return max(lo, min(int(str(raw).strip()), hi))
        except ValueError:
            pass
    return default


def page_max_chars() -> int:
    return _read_int_config(
        "query.page_max_chars", "QUERY_PAGE_MAX_CHARS", _DEFAULT_PAGE_MAX_CHARS,
        lo=500, hi=400_000,
    )


def page_max_sections() -> int:
    return _read_int_config(
        "query.page_max_sections", "QUERY_PAGE_MAX_SECTIONS", _DEFAULT_MAX_SECTIONS,
        lo=1, hi=200,
    )


def context_total_chars() -> int:
    return _read_int_config(
        "query.context_total_chars", "QUERY_CONTEXT_TOTAL_CHARS", _DEFAULT_TOTAL_CHARS,
        lo=1000, hi=2_000_000,
    )


def _cut_at_paragraph(text: str, limit: int) -> str:
    """在 limit 之前最近的空行(退而求其次:行尾)处切,避免把句子切一半。"""
    head = text[:limit]
    for sep in ("\n\n", "\n"):
        idx = head.rfind(sep)
        if idx >= limit // 2:
            return head[:idx].rstrip()
    return head.rstrip()


def _query_terms(question: str, keywords: list[str]) -> list[str]:
    terms = [x.strip().lower() for x in keywords if x and x.strip()]
    terms.extend(
        x.lower() for x in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9-]+", question)
    )
    return list(dict.fromkeys(terms))


def _split_sections(body: str) -> list[dict]:
    lines = body.splitlines()
    sections: list[dict] = []
    current = {"heading": "Overview", "level": 0, "start_line": 1, "lines": []}
    for line_no, line in enumerate(lines, start=1):
        match = _HEADING_RE.match(line)
        if match:
            if current["lines"]:
                sections.append(current)
            current = {
                "heading": match.group(2).strip(),
                "level": len(match.group(1)),
                "start_line": line_no,
                "lines": [line],
            }
        else:
            current["lines"].append(line)
    if current["lines"]:
        sections.append(current)
    return sections


def _select_sections_impl(
    body: str,
    *,
    question: str,
    keywords: list[str],
    hit_lines: list[int],
    max_chars: int,
    max_sections: int,
) -> tuple[str, list[dict], bool]:
    """内部版:额外返回 dropped(是否真的丢了内容),供 truncated 判定使用。"""
    terms = _query_terms(question, keywords)
    hit_set = set(hit_lines)
    ranked: list[tuple[float, int, dict]] = []
    sections = _split_sections(body)
    for idx, section in enumerate(sections):
        text = "\n".join(section["lines"]).strip()
        lower = text.lower()
        heading = section["heading"].lower()
        term_hits = sum(lower.count(term) for term in terms)
        heading_hits = sum(1 for term in terms if term in heading)
        start = section["start_line"]
        end = start + len(section["lines"]) - 1
        line_hits = sum(1 for line in hit_set if start <= line <= end)
        # Keep the introductory section useful, but never let it dominate evidence.
        score = term_hits + 4 * heading_hits + 5 * line_hits + (0.25 if idx == 0 else 0)
        ranked.append((score, idx, {**section, "text": text, "score": score, "end_line": end}))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    chosen: list[dict] = []
    used = 0
    dropped = False
    for score, _, section in ranked:
        if len(chosen) >= max_sections or (score <= 0 and chosen):
            dropped = True
            break
        text = section["text"]
        remaining = max_chars - used
        if remaining <= _MIN_SECTION_CHARS:
            dropped = True
            break
        if len(text) > remaining:
            kept = _cut_at_paragraph(text, max(1, remaining - _ELISION_RESERVE))
            if len(kept) < _MIN_SECTION_CHARS:
                dropped = True
                break
            omitted = len(text) - len(kept)
            text = f"{kept}\n\n[本节后续约 {omitted} 字已省略]"
            dropped = True
        chosen.append({**section, "text": text})
        used += len(text) + 2
    chosen.sort(key=lambda section: section["start_line"])
    excerpt = "\n\n".join(section["text"] for section in chosen).strip()
    metadata = [
        {
            "heading": section["heading"],
            "start_line": section["start_line"],
            "end_line": section["end_line"],
            "score": section["score"],
        }
        for section in chosen
    ]
    if len(chosen) < len(sections):
        dropped = True
    return excerpt, metadata, dropped


def select_relevant_sections(
    body: str,
    *,
    question: str,
    keywords: list[str],
    hit_lines: list[int],
    max_chars: int | None = None,
    max_sections: int | None = None,
) -> tuple[str, list[dict]]:
    """Rank Markdown sections and pack the best evidence into a character budget."""
    excerpt, metadata, _ = _select_sections_impl(
        body,
        question=question,
        keywords=keywords,
        hit_lines=hit_lines,
        max_chars=page_max_chars() if max_chars is None else max_chars,
        max_sections=page_max_sections() if max_sections is None else max_sections,
    )
    return excerpt, metadata


def _resolve_page_path(slug: str, wiki_root: Path, hint_kind: str | None = None) -> Path | None:
    """按 hint_kind 优先在对应目录查;没找到则在 wiki/ 下扫所有子目录。"""
    wiki_root = Path(wiki_root)
    if hint_kind:
        candidate = safe_join(wiki_root, "wiki", _kind_dir(hint_kind), f"{slug}.md")
        if candidate.exists():
            return candidate
    # 兜底全扫
    for sub in ("entities", "topics", "sources", "queries", "synthesis", "comparisons"):
        p = safe_join(wiki_root, "wiki", sub, f"{slug}.md")
        if p.exists():
            return p
    return None


def _kind_dir(kind: str) -> str:
    return {
        "entity": "entities",
        "topic": "topics",
        "source": "sources",
        "query": "queries",
        "synthesis": "synthesis",
        "comparison": "comparisons",
    }.get(kind, kind)


async def read_for_context(
    *,
    slug: str,
    hit_lines: list[int],
    wiki_root: Path,
    long_threshold: int = 2000,
    head_chars: int = 500,
    surrounding_lines: int = 3,
    hint_kind: str | None = None,
    question: str = "",
    keywords: list[str] | None = None,
    max_context_chars: int | None = None,
    max_sections: int | None = None,
) -> dict | None:
    """返回 {slug, title, kind, frontmatter, body_excerpt, total_chars, truncated}。

    页面不存在返回 None。
    """
    page_path = _resolve_page_path(slug, wiki_root, hint_kind)
    if page_path is None:
        return None

    text = page_path.read_text(encoding="utf-8")
    fm, body = parse_text(text)
    total = len(body)

    # Preserve the legacy full-page behavior when no query context is supplied.
    if total <= long_threshold and not question and not keywords:
        return {
            "slug": slug,
            "title": fm.get("title") or slug,
            "kind": fm.get("kind"),
            "frontmatter": fm,
            "body_excerpt": body.strip("\n"),
            "total_chars": total,
            "truncated": False,
            "selected_sections": [],
        }

    if question or keywords:
        budget = page_max_chars() if max_context_chars is None else max_context_chars
        if total <= budget:
            # 整页塞得下就别过选择器,免得掉无 term 命中的小节
            return {
                "slug": slug,
                "title": fm.get("title") or slug,
                "kind": fm.get("kind"),
                "frontmatter": fm,
                "body_excerpt": body.strip("\n"),
                "total_chars": total,
                "truncated": False,
                "selected_sections": [],
            }
        excerpt, selected_sections, dropped = _select_sections_impl(
            body,
            question=question,
            keywords=keywords or [],
            hit_lines=hit_lines,
            max_chars=budget,
            max_sections=page_max_sections() if max_sections is None else max_sections,
        )
        return {
            "slug": slug,
            "title": fm.get("title") or slug,
            "kind": fm.get("kind"),
            "frontmatter": fm,
            "body_excerpt": excerpt,
            "total_chars": total,
            "truncated": dropped or len(excerpt) < len(body.strip()),
            "selected_sections": selected_sections,
        }

    # 超长 → 头 head_chars + 命中段落前后行
    head = body[:head_chars]
    lines = body.splitlines()
    selected: list[str] = []
    selected_set: set[int] = set()
    for ln in hit_lines:
        for i in range(max(0, ln - 1 - surrounding_lines), min(len(lines), ln + surrounding_lines)):
            if i not in selected_set:
                selected_set.add(i)
                selected.append(f"L{i + 1}: {lines[i]}")

    excerpt = head.rstrip()
    if selected:
        excerpt += "\n\n…\n\n## 命中段落\n\n" + "\n".join(selected)

    return {
        "slug": slug,
        "title": fm.get("title") or slug,
        "kind": fm.get("kind"),
        "frontmatter": fm,
        "body_excerpt": excerpt,
        "total_chars": total,
        "truncated": True,
        "selected_sections": [],
    }


async def read_pages_for_context(
    candidates: list[dict],
    *,
    wiki_root: Path,
    question: str = "",
    keywords: list[str] | None = None,
    total_chars: int | None = None,
    per_page_max_chars: int | None = None,
    **kwargs,
) -> list[dict]:
    """多页共享一个总预算读上下文,返回与 read_for_context 同结构的页 dict 列表。

    分配规则:总预算按页数均分成每页初始额度(不低于 _MIN_PAGE_CHARS、不高于单页上限),
    实际用不满的页把剩余额度让给后面的页,因此短页多时长页能拿到更多空间。
    candidates 已按相关度排序,预算不够再给一页 _MIN_PAGE_CHARS 时直接丢掉剩下的尾部页:
    每页都削到下限会让 30 页 × 1200 把 28000 顶到 34000,且每页都短到没有论证价值。
    """
    budget = context_total_chars() if total_chars is None else total_chars
    ceiling = page_max_chars() if per_page_max_chars is None else per_page_max_chars
    pages: list[dict] = []
    remaining_pages = len(candidates)
    for candidate in candidates:
        if remaining_pages <= 0 or budget < _MIN_PAGE_CHARS:
            break
        share = max(_MIN_PAGE_CHARS, min(ceiling, budget // remaining_pages))
        page = await read_for_context(
            slug=candidate["slug"],
            hit_lines=candidate.get("hit_lines") or [],
            wiki_root=wiki_root,
            hint_kind=candidate.get("kind"),
            question=question,
            keywords=keywords,
            max_context_chars=share,
            **kwargs,
        )
        remaining_pages -= 1
        if page is None:
            continue
        budget = max(0, budget - len(page.get("body_excerpt") or ""))
        pages.append(page)
    return pages
