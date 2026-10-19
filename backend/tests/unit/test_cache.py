"""wiki/cache 单元测试。"""

from __future__ import annotations

from pathlib import Path

from app.services.wiki.cache import (
    CACHE_FILENAME,
    compute_hash,
    evict,
    evict_by_source_slug,
    is_hit,
    record,
)


def test_compute_hash_str_and_bytes_match():
    a = compute_hash("hello")
    b = compute_hash(b"hello")
    assert a == b
    assert len(a) == 64


def test_record_and_hit(tmp_path: Path):
    h = compute_hash("md content")
    assert is_hit(tmp_path, h) is None
    record(tmp_path, content_hash=h, source_slug="2026-05-19-x", entities=["A", "B"])
    rec = is_hit(tmp_path, h)
    assert rec is not None
    assert rec["source_slug"] == "2026-05-19-x"
    assert rec["entities"] == ["A", "B"]
    assert "ingested_at" in rec
    assert (tmp_path / CACHE_FILENAME).exists()


def test_evict(tmp_path: Path):
    h = compute_hash("a")
    record(tmp_path, content_hash=h, source_slug="src")
    assert evict(tmp_path, h) is True
    assert is_hit(tmp_path, h) is None
    assert evict(tmp_path, h) is False


def test_evict_by_source_slug(tmp_path: Path):
    record(tmp_path, content_hash=compute_hash("a"), source_slug="src1")
    record(tmp_path, content_hash=compute_hash("b"), source_slug="src1")
    record(tmp_path, content_hash=compute_hash("c"), source_slug="src2")
    assert evict_by_source_slug(tmp_path, "src1") == 2
    assert is_hit(tmp_path, compute_hash("c")) is not None


def test_corrupted_cache_falls_back_to_empty(tmp_path: Path):
    (tmp_path / CACHE_FILENAME).write_text("not json", encoding="utf-8")
    assert is_hit(tmp_path, "anything") is None
    record(tmp_path, content_hash="h", source_slug="x")
    assert is_hit(tmp_path, "h") is not None
