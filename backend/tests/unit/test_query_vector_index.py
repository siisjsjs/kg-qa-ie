from __future__ import annotations

import pytest

from app.services.query import vector_index as module


class _Provider:
    async def embed(self, texts):
        return [[float(index), 1.0] for index, _ in enumerate(texts)]


class _Store:
    existing = {}
    upserts = []
    deletes = []

    def __init__(self, **kwargs):
        pass

    async def list_records(self, **kwargs):
        return dict(self.existing)

    async def upsert(self, ids, embeddings, metadatas, documents=None):
        self.upserts.append((ids, metadatas, documents))

    async def delete(self, ids):
        self.deletes.append(ids)


@pytest.mark.asyncio
async def test_incremental_chunk_sync_indexes_sources_and_removes_stale(tmp_path, monkeypatch):
    wiki_root = tmp_path / "root"
    entity = wiki_root / "wiki" / "entities" / "alpha.md"
    entity.parent.mkdir(parents=True)
    entity.write_text("---\ntitle: Alpha\nkind: entity\n---\n\n## Method\nUseful body", encoding="utf-8")
    generated = wiki_root / "wiki" / "queries" / "answer.md"
    generated.parent.mkdir(parents=True)
    generated.write_text("---\ntitle: Answer\nkind: query\n---\n\nGenerated", encoding="utf-8")

    _Store.existing = {
        "stale::00": {"slug": "stale", "page_hash": "old", "content_hash": "old"}
    }
    _Store.upserts = []
    _Store.deletes = []
    monkeypatch.setattr(module, "WikiVectorStore", _Store)
    monkeypatch.setattr(module, "get_embedding_provider", lambda: _Provider())
    monkeypatch.setattr(module.settings, "cache_dir", str(tmp_path / "cache"))

    result = await module.sync_query_vector_index(wiki_root=wiki_root, batch_size=8)

    assert result == {
        "page_count": 1,
        "chunk_count": 1,
        "upserted": 1,
        "upserted_chunks": 1,
        "deleted": 1,
        "unchanged": 0,
    }
    # alpha.md 以 "## Method" 开头,无引言块 → 单 chunk
    ids = _Store.upserts[0][0]
    assert all(cid.startswith("alpha::") for cid in ids)
    metadatas = _Store.upserts[0][1]
    assert metadatas[0]["slug"] == "alpha"
    assert "page_hash" in metadatas[0]
    assert metadatas[0]["line_start"] >= 1
    # documents 存 chunk 原文
    assert any("Useful body" in (doc or "") for doc in _Store.upserts[0][2])
    assert _Store.deletes == [["stale::00"]]
    assert (tmp_path / "cache" / "query_chunk_index.json").exists()


@pytest.mark.asyncio
async def test_changed_page_deletes_old_chunks_before_upsert(tmp_path, monkeypatch):
    wiki_root = tmp_path / "root"
    entity = wiki_root / "wiki" / "entities" / "alpha.md"
    entity.parent.mkdir(parents=True)
    entity.write_text("---\ntitle: Alpha\nkind: entity\n---\n\nnew body", encoding="utf-8")

    # 现存 alpha 有 2 个旧 chunk 且 page_hash 不同 → 应先删旧 chunk 再插新
    _Store.existing = {
        "alpha::00": {"slug": "alpha", "page_hash": "outdated"},
        "alpha::01": {"slug": "alpha", "page_hash": "outdated"},
    }
    _Store.upserts = []
    _Store.deletes = []
    monkeypatch.setattr(module, "WikiVectorStore", _Store)
    monkeypatch.setattr(module, "get_embedding_provider", lambda: _Provider())
    monkeypatch.setattr(module.settings, "cache_dir", str(tmp_path / "cache"))

    result = await module.sync_query_vector_index(wiki_root=wiki_root)

    assert result["upserted"] == 1
    assert ["alpha::00", "alpha::01"] in _Store.deletes
    assert _Store.upserts[0][0] == ["alpha::00"]


@pytest.mark.asyncio
async def test_unchanged_page_skips_embedding(tmp_path, monkeypatch):
    wiki_root = tmp_path / "root"
    entity = wiki_root / "wiki" / "entities" / "alpha.md"
    entity.parent.mkdir(parents=True)
    entity.write_text("---\ntitle: Alpha\nkind: entity\n---\n\nsame body", encoding="utf-8")

    from app.services.query.chunker import chunk_page, page_content_hash

    page_hash = page_content_hash(chunk_page(entity))
    _Store.existing = {"alpha::00": {"slug": "alpha", "page_hash": page_hash}}
    _Store.upserts = []
    _Store.deletes = []
    monkeypatch.setattr(module, "WikiVectorStore", _Store)
    monkeypatch.setattr(module, "get_embedding_provider", lambda: _Provider())
    monkeypatch.setattr(module.settings, "cache_dir", str(tmp_path / "cache"))

    result = await module.sync_query_vector_index(wiki_root=wiki_root)

    assert result["upserted"] == 0
    assert result["unchanged"] == 1
    assert _Store.upserts == []


def _make_page(wiki_root, sub, slug):
    page = wiki_root / "wiki" / sub / f"{slug}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"---\ntitle: {slug}\nkind: entity\n---\n\nbody", encoding="utf-8")
    return page


def test_index_freshness_reports_missing_marker_as_stale(tmp_path, monkeypatch):
    wiki_root = tmp_path / "root"
    _make_page(wiki_root, "entities", "alpha")
    monkeypatch.setattr(module.settings, "cache_dir", str(tmp_path / "cache"))

    out = module.index_freshness(wiki_root)

    assert out["marker_present"] is False
    assert out["stale"] is True
    assert out["disk_page_count"] == 1
    assert out["indexed_page_count"] is None


def test_index_freshness_counts_delta_and_duplicate_slugs(tmp_path, monkeypatch):
    wiki_root = tmp_path / "root"
    _make_page(wiki_root, "entities", "alpha")
    _make_page(wiki_root, "topics", "alpha")  # 同 slug 落在两个目录,向量库折叠成一组
    _make_page(wiki_root, "entities", "beta")
    monkeypatch.setattr(module.settings, "cache_dir", str(tmp_path / "cache"))
    module._write_marker(wiki_root.resolve(), 1, 3)

    out = module.index_freshness(wiki_root)

    assert out["disk_file_count"] == 3
    assert out["disk_page_count"] == 2
    assert out["duplicate_slug_count"] == 1
    assert out["indexed_page_count"] == 1
    assert out["indexed_chunk_count"] == 3
    assert out["page_count_delta"] == 1
    assert out["root_matches_marker"] is True
    assert out["stale"] is True


@pytest.mark.asyncio
async def test_refresh_if_stale_skips_sync_when_index_is_fresh(tmp_path, monkeypatch):
    wiki_root = tmp_path / "root"
    _make_page(wiki_root, "entities", "alpha")
    monkeypatch.setattr(module.settings, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(
        module, "index_freshness", lambda root: {"stale": False, "pending_page_count": 0}
    )
    calls: list[dict] = []

    async def fake_sync(*, wiki_root, batch_size=32):
        calls.append({"root": str(wiki_root)})
        return {}

    monkeypatch.setattr(module, "sync_query_vector_index", fake_sync)

    assert await module.refresh_if_stale(wiki_root=wiki_root) is None
    assert calls == []


@pytest.mark.asyncio
async def test_refresh_if_stale_triggers_sync_when_disk_moved_ahead(tmp_path, monkeypatch):
    wiki_root = tmp_path / "root"
    _make_page(wiki_root, "entities", "alpha")
    monkeypatch.setattr(module.settings, "cache_dir", str(tmp_path / "cache"))
    calls: list[dict] = []

    async def fake_sync(*, wiki_root, batch_size=32):
        calls.append({"root": str(wiki_root)})
        return {"page_count": 1, "upserted": 1, "deleted": 0, "unchanged": 0}

    monkeypatch.setattr(module, "sync_query_vector_index", fake_sync)

    out = await module.refresh_if_stale(wiki_root=wiki_root)

    assert out == {"page_count": 1, "upserted": 1, "deleted": 0, "unchanged": 0}
    assert len(calls) == 1
