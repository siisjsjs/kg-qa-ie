"""query/persistor.py:写问答持久化页。

按问题类型分目录(P6+):
  - comparison           → wiki/comparisons/   frontmatter.kind = 'comparison'
  - timeline / deep_dive → wiki/synthesis/     frontmatter.kind = 'synthesis'
  - concept / method / general → wiki/queries/  frontmatter.kind = 'query'

frontmatter 里同时记 `question_kind` 字段(原始 6 类),用于后续过滤/统计。

判断:
  - force=True / cited_sources>=3 → 写
  - force=False → 不写
  - force=None(默认) → 由调用方决策(P2 暂用 cited_sources>=3 启发)
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from app.services.wiki.frontmatter_io import serialize
from app.services.wiki.fs_io import atomic_write, safe_join, slugify

# 6 类 question_kind → (落地子目录, frontmatter.kind)
_KIND_ROUTING: dict[str, tuple[str, str]] = {
    "comparison": ("comparisons", "comparison"),
    "timeline": ("synthesis", "synthesis"),
    "deep_dive": ("synthesis", "synthesis"),
    "concept": ("queries", "query"),
    "method": ("queries", "query"),
    "general": ("queries", "query"),
}


def resolve_target(question_kind: str | None) -> tuple[str, str]:
    """根据 question_kind 返回 (子目录名, frontmatter.kind)。未知 kind 默认 queries。"""
    return _KIND_ROUTING.get(question_kind or "general", ("queries", "query"))


def make_query_slug(*, question: str, date: str | None = None, hash_len: int = 8) -> str:
    """`{YYYY-MM-DD}-{slugified-title}-{md5_short}`,避免相同问法重名。"""
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    title = slugify(question, max_len=40)
    digest = hashlib.md5(question.encode("utf-8")).hexdigest()[:hash_len]
    return f"{date_str}-{title}-{digest}"


def _citation_token(item) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return ""
    if item.get("type") == "web" and item.get("ref"):
        return f"web#{item['ref']}"
    slug = str(item.get("slug") or "").strip()
    ref = str(item.get("ref") or "").strip()
    if slug and ref and item.get("type") == "chunk":
        return f"{slug}#{ref}"
    return slug


def render_query_md(
    *,
    title_question: str,
    slug: str,
    answer: str,
    citations: list,
    quality: dict | None = None,
    domain_ie: bool = False,
    user_id: int | None = None,
    now_iso: str | None = None,
    fm_kind: str = "query",
    question_kind: str | None = None,
    session_id: int | None = None,
    images: list[dict] | None = None,
    web_citations: list[dict] | None = None,
    citation_details: list[dict] | None = None,
) -> str:
    now = now_iso or datetime.now(timezone.utc).isoformat()
    slug_list = [
        c if isinstance(c, str) else str((c or {}).get("slug") or "")
        for c in citations
    ]
    slug_list = [s for s in slug_list if s and not str(s).startswith("web#")]
    fm = {
        "title": title_question[:80],
        "slug": slug,
        "kind": fm_kind,
        "user_question": title_question,
        "domain_ie": bool(domain_ie),
        "citations": list(dict.fromkeys(slug_list)),
        "answer_quality": (quality or {}).get("score"),
        "tier": (quality or {}).get("tier"),
        "gap_flag": (quality or {}).get("gap_flag", False),
        "created": now,
        "updated": now,
        "version": 1,
    }
    if question_kind:
        fm["question_kind"] = question_kind
    if user_id is not None:
        fm["asked_by"] = user_id
    if session_id is not None:
        fm["session_id"] = session_id
    if citation_details:
        fm["citation_details"] = citation_details
    if web_citations:
        fm["web_citations"] = web_citations
    if images:
        fm["images"] = images
    display_tokens = []
    if citation_details:
        display_tokens = [_citation_token(c) for c in citation_details]
        display_tokens = [t for t in display_tokens if t]
    if not display_tokens:
        display_tokens = list(dict.fromkeys(slug_list))
    cite_block = (
        "\n".join(f"- [[{c}]]" for c in display_tokens) if display_tokens else "_无_"
    )
    body = (
        f"## 用户问题\n\n{title_question}\n\n"
        f"## 答案\n\n{answer.strip()}\n\n"
        f"## 引用\n\n{cite_block}\n"
    )
    return serialize(fm, body)


async def maybe_persist(
    *,
    question: str,
    answer: str,
    citations: list,
    wiki_root: Path,
    quality: dict | None = None,
    domain_ie: bool = False,
    force: bool | None = None,
    user_id: int | None = None,
    question_kind: str | None = None,
    session_id: int | None = None,
    images: list[dict] | None = None,
    web_citations: list[dict] | None = None,
    citation_details: list[dict] | None = None,
) -> str | None:
    """根据策略决定是否写持久化页,返回 slug 或 None。

    question_kind 为 6 类之一(comparison/timeline/deep_dive/concept/method/general),
    决定落地目录与 frontmatter.kind。
    """
    slug_keys = []
    for c in citations or []:
        if isinstance(c, str) and c:
            slug_keys.append(c)
        elif isinstance(c, dict) and c.get("slug") and not str(c.get("slug")).startswith("web#"):
            slug_keys.append(str(c["slug"]))
    cited = len(set(slug_keys))
    if force is False:
        return None
    if force is None and cited < 3:
        return None
    # force=True 或 cited>=3
    subdir, fm_kind = resolve_target(question_kind)
    slug = make_query_slug(question=question)
    md = render_query_md(
        title_question=question,
        slug=slug,
        answer=answer,
        citations=list(dict.fromkeys(slug_keys)),
        quality=quality,
        domain_ie=domain_ie,
        user_id=user_id,
        fm_kind=fm_kind,
        question_kind=question_kind,
        session_id=session_id,
        images=images,
        web_citations=web_citations,
        citation_details=citation_details,
    )
    dest = safe_join(Path(wiki_root), "wiki", subdir, f"{slug}.md")
    await atomic_write(dest, md)
    return slug
