from __future__ import annotations

import pytest

from app.services.query.hybrid import (
    expand_search_terms,
    fuse_and_rerank,
    hybrid_search,
    last_retrieval_stats,
    new_retrieval_stats,
)


def test_fuse_and_rerank_combines_lexical_and_semantic_chunks():
    lexical = [
        {
            "chunk_id": "a::00", "slug": "a", "title": "Alpha method", "kind": "entity",
            "heading": "", "text": "alpha text", "line_start": 1, "line_end": 3,
            "lexical_score": 100, "hit_count": 2,
        },
        {
            "chunk_id": "b::01", "slug": "b", "title": "Other", "kind": "topic",
            "heading": "步骤", "text": "b text", "line_start": 4, "line_end": 9,
            "lexical_score": 20, "hit_count": 1,
        },
    ]
    semantic = [
        {
            "chunk_id": "b::01", "slug": "b", "title": "Other", "kind": "topic",
            "heading": "步骤", "text": "b text", "semantic_score": 0.95,
        },
        {
            "chunk_id": "c::00", "slug": "c", "title": "Alpha case", "kind": "source",
            "heading": "", "text": "c text", "semantic_score": 0.8,
        },
    ]
    out = fuse_and_rerank(
        question="Alpha method", keywords=["Alpha"], lexical=lexical, semantic=semantic, top_k=5
    )
    assert {row["chunk_id"] for row in out} == {"a::00", "b::01", "c::00"}
    both = next(row for row in out if row["chunk_id"] == "b::01")
    assert both["retrieval_sources"] == ["lexical", "semantic"]
    assert all("rerank_score" in row for row in out)
    assert all(row.get("text") for row in out)


def test_fuse_and_rerank_caps_chunks_per_page():
    lexical = [
        {
            "chunk_id": f"a::{i:02d}", "slug": "a", "title": "Alpha", "kind": "entity",
            "heading": f"h{i}", "text": f"t{i}", "line_start": i * 10, "line_end": i * 10 + 5,
            "lexical_score": 100 - i, "hit_count": 1,
        }
        for i in range(5)
    ] + [
        {
            "chunk_id": "b::00", "slug": "b", "title": "Beta", "kind": "topic",
            "heading": "", "text": "b", "line_start": 1, "line_end": 2,
            "lexical_score": 1, "hit_count": 1,
        }
    ]
    out = fuse_and_rerank(
        question="Alpha", keywords=["Alpha"], lexical=lexical, semantic=[], top_k=10
    )
    a_chunks = [row for row in out if row["slug"] == "a"]
    assert len(a_chunks) == 3  # 每页最多 3 个 chunk
    assert any(row["slug"] == "b" for row in out)


def test_fuse_and_rerank_tolerates_missing_chunk_id():
    semantic = [{"slug": "page", "title": "Page", "kind": "topic", "semantic_score": 0.9}]
    out = fuse_and_rerank(
        question="Page", keywords=["Page"], lexical=[], semantic=semantic, top_k=5
    )
    assert out[0]["chunk_id"] == "page::00"


def test_expand_search_terms_removes_question_wrappers():
    terms = expand_search_terms("TPM实施有哪些步骤", ["TPM", "实施有哪些步骤"])
    assert "实施" in terms
    assert "步骤" in terms


@pytest.mark.asyncio
async def test_hybrid_search_reports_semantic_failure_and_still_returns_lexical(tmp_path):
    page = tmp_path / "wiki" / "entities" / "lexical.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntitle: Alpha\nkind: entity\n---\n\nAlpha body", encoding="utf-8")

    async def broken_searcher(question: str, top_k: int):
        raise RuntimeError("embedding api down")

    stats = new_retrieval_stats()
    out = await hybrid_search(
        question="Alpha", keywords=["Alpha"], wiki_root=tmp_path, top_k=5,
        semantic_searcher=broken_searcher, stats=stats,
    )
    assert [row["slug"] for row in out] == ["lexical"]
    assert out[0]["chunk_id"] == "lexical::00"
    assert out[0]["retrieval_sources"] == ["lexical"]
    assert out[0]["text"] == "Alpha body"
    assert out[0]["line_start"] == 1
    assert stats["semantic_ok"] is False
    assert stats["semantic_degraded"] is True
    assert stats["semantic_reason"] == "exception"
    assert "embedding api down" in str(stats["semantic_error"])
    assert last_retrieval_stats()["semantic_degraded"] is True


@pytest.mark.asyncio
async def test_hybrid_search_marks_healthy_semantic_leg_not_degraded(tmp_path):
    page = tmp_path / "wiki" / "topics" / "semantic.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntitle: Semantic\nkind: topic\n---\n\nRelated", encoding="utf-8")

    async def searcher(question: str, top_k: int):
        return [{
            "chunk_id": "semantic::00", "slug": "semantic", "title": "Semantic",
            "kind": "topic", "heading": "", "text": "Related", "semantic_score": 0.9,
        }]

    stats = new_retrieval_stats()
    await hybrid_search(
        question="Semantic", keywords=["Semantic"], wiki_root=tmp_path, top_k=5,
        semantic_searcher=searcher, stats=stats,
    )
    assert stats["semantic_degraded"] is False
    assert stats["semantic_count"] == 1


@pytest.mark.asyncio
async def test_hybrid_search_uses_semantic_candidate(tmp_path):
    page = tmp_path / "wiki" / "entities" / "lexical.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntitle: Alpha\nkind: entity\n---\n\nAlpha body", encoding="utf-8")
    semantic_page = tmp_path / "wiki" / "topics" / "semantic.md"
    semantic_page.parent.mkdir(parents=True)
    semantic_page.write_text("---\ntitle: Semantic match\nkind: topic\n---\n\nRelated", encoding="utf-8")

    async def semantic_searcher(question: str, top_k: int):
        return [{
            "chunk_id": "semantic::00", "slug": "semantic", "title": "Semantic match",
            "kind": "topic", "heading": "", "text": "Related", "semantic_score": 0.99,
        }]

    out = await hybrid_search(
        question="Alpha", keywords=["Alpha"], wiki_root=tmp_path, top_k=5,
        semantic_searcher=semantic_searcher,
    )
    assert {row["slug"] for row in out} == {"lexical", "semantic"}


@pytest.mark.asyncio
async def test_hybrid_lexical_hits_map_to_correct_chunk(tmp_path):
    """grep 命中行应落到所属 chunk(行号坐标系对齐)。"""
    page = tmp_path / "wiki" / "topics" / "dmaic.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\ntitle: DMAIC\nkind: topic\n---\n\n"
        "总览介绍。\n"
        "\n"
        "## 历史背景\n"
        "很老的背景。\n"
        "\n"
        "## 实施步骤\n"
        "第一步定义 targetkw 问题。\n"
        "第二步测量 targetkw 过程。\n",
        encoding="utf-8",
    )

    out = await hybrid_search(
        question="targetkw", keywords=["targetkw"], wiki_root=tmp_path, top_k=5,
    )
    assert out, "应有词法命中"
    top = out[0]
    assert top["slug"] == "dmaic"
    assert top["heading"] == "实施步骤"
    assert "targetkw" in top["text"]
