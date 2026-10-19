"""wiki/fs_io 单元测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.wiki.fs_io import atomic_write, read_text, safe_join, slugify


def test_safe_join_under_root(tmp_path: Path):
    p = safe_join(tmp_path, "wiki", "entities", "x.md")
    assert p.is_relative_to(tmp_path.resolve())


def test_safe_join_rejects_escape(tmp_path: Path):
    with pytest.raises(ValueError):
        safe_join(tmp_path, "..", "etc", "passwd")


def test_safe_join_rejects_absolute(tmp_path: Path):
    with pytest.raises(ValueError):
        safe_join(tmp_path, "/abs/path")


def test_slugify_chinese_kept():
    assert slugify("价值流图") == "价值流图"
    assert slugify("精益生产 与 6 sigma") == "精益生产-与-6-sigma"


def test_slugify_strips_invalid_chars():
    s = slugify('a/b<c>d:e"f|g?h*i')
    for bad in '/<>:"|?*':
        assert bad not in s


def test_slugify_ascii_uses_python_slugify():
    assert slugify("Value Stream Mapping") == "Value-Stream-Mapping"


def test_slugify_empty_raises():
    with pytest.raises(ValueError):
        slugify("")
    with pytest.raises(ValueError):
        slugify("   ")


def test_slugify_max_len():
    s = slugify("a" * 200, max_len=20)
    assert len(s) <= 20


def test_atomic_write_and_read(tmp_path: Path):
    p = tmp_path / "sub" / "x.md"
    asyncio.run(atomic_write(p, "你好\nworld\n"))
    assert p.exists()
    assert asyncio.run(read_text(p)) == "你好\nworld\n"
    assert not (tmp_path / "sub" / "x.md.tmp").exists()


def test_atomic_write_overwrites(tmp_path: Path):
    p = tmp_path / "x.md"
    asyncio.run(atomic_write(p, "v1"))
    asyncio.run(atomic_write(p, "v2"))
    assert asyncio.run(read_text(p)) == "v2"
