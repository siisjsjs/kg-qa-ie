from __future__ import annotations

from app.services.query.citations import (
    build_citation_details,
    enrich_citation_meta,
    resolve_citation,
)


def _seed(root, directory: str, slug: str, title: str, kind: str) -> None:
    path = root / "wiki" / directory / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {title}\nslug: {slug}\nkind: {kind}\n---\n\n正文\n",
        encoding="utf-8",
    )


def test_resolve_citation_uses_frontmatter_title_and_keeps_slug(tmp_path):
    slug = "2026-06-11-第十五章-面向中国制造-2025-的工业工程"
    title = "第十五章 面向中国制造2025的工业工程"
    _seed(tmp_path, "sources", slug, title, "source")
    assert resolve_citation(slug, wiki_root=tmp_path) == {
        "slug": slug,
        "title": title,
        "kind": "source",
    }


def test_build_details_prefers_context_and_deduplicates(tmp_path):
    details = build_citation_details(
        ["智能制造", "智能制造"],
        wiki_root=tmp_path,
        context_pages=[
            {"slug": "智能制造", "title": "智能制造", "kind": "entity"}
        ],
    )
    assert details == [
        {"slug": "智能制造", "title": "智能制造", "kind": "entity"}
    ]


def test_enrich_legacy_meta_replaces_slug_title(tmp_path):
    slug = "2026-06-11-第十五章-面向中国制造-2025-的工业工程"
    title = "第十五章 面向中国制造2025的工业工程"
    _seed(tmp_path, "sources", slug, title, "source")
    meta = enrich_citation_meta(
        {"citations": [{"slug": slug, "title": slug}]},
        wiki_root=tmp_path,
    )
    assert meta["citations"] == [
        {"slug": slug, "title": title, "kind": "source"}
    ]


def test_missing_page_uses_readable_fallback_without_date(tmp_path):
    detail = resolve_citation(
        "2026-06-11-未知-来源",
        wiki_root=tmp_path,
    )
    assert detail["title"] == "未知 来源"
    assert detail["slug"] == "2026-06-11-未知-来源"
