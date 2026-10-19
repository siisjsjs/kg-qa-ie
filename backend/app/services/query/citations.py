"""Resolve stable citation slugs to user-facing Wiki metadata."""

from __future__ import annotations

import re
from pathlib import Path

from app.services.wiki.frontmatter_io import parse_text
from app.services.wiki.fs_io import safe_join

_KIND_DIRS = {
    "entity": "entities",
    "topic": "topics",
    "source": "sources",
    "query": "queries",
    "synthesis": "synthesis",
    "comparison": "comparisons",
}
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")


def _fallback_title(slug: str) -> str:
    title = _DATE_PREFIX_RE.sub("", slug).replace("-", " ").strip()
    return title or "未命名来源"


def resolve_citation(
    slug: str,
    *,
    wiki_root: Path,
    hint_kind: str | None = None,
) -> dict:
    """Return display metadata while keeping ``slug`` as the stable identity."""
    slug = str(slug or "").strip()
    if not slug:
        return {"slug": "", "title": "未命名来源", "kind": hint_kind or ""}

    kinds = list(_KIND_DIRS)
    if hint_kind in _KIND_DIRS:
        kinds.remove(hint_kind)
        kinds.insert(0, hint_kind)
    for kind in kinds:
        try:
            path = safe_join(Path(wiki_root), "wiki", _KIND_DIRS[kind], f"{slug}.md")
        except ValueError:
            break
        if not path.exists():
            continue
        title = _fallback_title(slug)
        resolved_kind = kind
        try:
            fm, _ = parse_text(path.read_text(encoding="utf-8"))
            title = str(fm.get("title") or title).strip() or title
            fm_kind = str(fm.get("kind") or "").strip()
            if fm_kind:
                resolved_kind = fm_kind
        except (OSError, ValueError):
            pass
        return {"slug": slug, "title": title, "kind": resolved_kind}
    return {"slug": slug, "title": _fallback_title(slug), "kind": hint_kind or ""}


def build_citation_details(
    slugs: list[str],
    *,
    wiki_root: Path,
    context_pages: list[dict] | None = None,
) -> list[dict]:
    """Resolve unique slugs in answer order, preferring already-read context metadata."""
    context = {
        str(page.get("slug") or ""): {
            "slug": str(page.get("slug") or ""),
            "title": str(page.get("title") or "").strip(),
            "kind": str(page.get("kind") or "").strip(),
        }
        for page in (context_pages or [])
        if page.get("slug")
    }
    details: list[dict] = []
    seen: set[str] = set()
    for raw_slug in slugs:
        slug = str(raw_slug or "").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        item = context.get(slug)
        if item and item["title"]:
            details.append(item)
        else:
            details.append(
                resolve_citation(
                    slug,
                    wiki_root=wiki_root,
                    hint_kind=item.get("kind") if item else None,
                )
            )
    return details


_QUOTE_CHARS = 200


def build_citation_details_v2(
    refs: list[dict],
    *,
    evidence_pool,
    wiki_root: Path,
) -> list[dict]:
    """chunk 引用协议的引用详情(改造计划 §6.3)。

    refs 来自 validation.extract_citation_refs:
      chunk 引用 → {type:"chunk", slug, title, kind, heading, line_start,
                     line_end, quote(chunk 原文前 200 字), ref}
      页面引用   → 现有 resolve_citation 逻辑({type:"page", ...})
      web 引用   → {type:"web", url, title, quote, ref}
    """
    details: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        raw = str(ref.get("raw") or "")
        if not raw or raw in seen:
            continue
        seen.add(raw)
        rtype = ref.get("type")
        if rtype == "web":
            item = evidence_pool.resolve(ref.get("ref") or "")
            if item is None:
                continue
            details.append(
                {
                    "type": "web",
                    "slug": f"web#{item['ref']}",
                    "ref": item["ref"],
                    "title": item.get("title") or item.get("url") or "",
                    "kind": "web",
                    "url": item.get("url"),
                    "quote": (item.get("content") or "")[:_QUOTE_CHARS],
                }
            )
            continue
        if rtype == "chunk":
            item = evidence_pool.resolve(ref.get("ref") or "")
            if item is not None:
                details.append(
                    {
                        "type": "chunk",
                        "slug": str(item.get("slug") or ref.get("slug") or ""),
                        "ref": item["ref"],
                        "title": str(item.get("title") or item.get("slug") or ""),
                        "kind": str(item.get("kind") or "") or None,
                        "heading": item.get("heading") or "",
                        "line_start": item.get("line_start"),
                        "line_end": item.get("line_end"),
                        "quote": (item.get("text") or "")[:_QUOTE_CHARS],
                    }
                )
                continue
            # 编号解析不到(清洗后理论上不会发生):按页面引用兜底
        slug = str(ref.get("slug") or "")
        if not slug:
            continue
        resolved = resolve_citation(slug, wiki_root=wiki_root)
        details.append({"type": "page", **resolved})
    return details


def enrich_citation_meta(meta: dict, *, wiki_root: Path) -> dict:
    """Upgrade legacy message metadata whose citation title was the slug.

    注意:必须保留条目原有的全部字段(url / ref / type / quote / heading 等)。
    早前实现把条目重写成只有 {slug,title,kind},导致 web 引用的 url、chunk
    引用的 ref 在历史会话里全部丢失——前端角标 data-url 变空、点击无反应,
    "网络来源"列表也渲染不出来。
    """
    raw_citations = meta.get("citations")
    if not isinstance(raw_citations, list):
        return meta
    enriched: list[dict] = []
    for raw in raw_citations:
        if isinstance(raw, str):
            enriched.append(resolve_citation(raw, wiki_root=wiki_root))
            continue
        if not isinstance(raw, dict):
            continue
        slug = str(raw.get("slug") or "").strip()
        if not slug:
            continue
        item = dict(raw)  # 保留全部字段,只在缺失时补 title / kind
        title = str(item.get("title") or "").strip()
        kind = str(item.get("kind") or "").strip()
        if not title or title == slug or not kind:
            resolved = resolve_citation(slug, wiki_root=wiki_root, hint_kind=kind or None)
            if not title or title == slug:
                item["title"] = resolved["title"]
            if not kind:
                item["kind"] = resolved["kind"]
        enriched.append(item)
    return {**meta, "citations": enriched}


__all__ = [
    "build_citation_details",
    "build_citation_details_v2",
    "enrich_citation_meta",
    "resolve_citation",
]
