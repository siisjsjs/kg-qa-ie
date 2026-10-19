"""wiki/frontmatter_io 单元测试。"""

from __future__ import annotations

from pathlib import Path

from app.services.wiki.frontmatter_io import merge_frontmatter, parse_file, parse_text, serialize


SAMPLE = """---
title: 价值流图
slug: 价值流图
kind: entity
tags:
  - 精益生产
  - 工业工程
sources:
  - 2026-05-19-易树平基础工业工程
version: 1
---

# 价值流图

正文内容
"""


def test_parse_text_roundtrip():
    fm, body = parse_text(SAMPLE)
    assert fm["title"] == "价值流图"
    assert fm["kind"] == "entity"
    assert fm["tags"] == ["精益生产", "工业工程"]
    assert "正文内容" in body


def test_parse_file(tmp_path: Path):
    p = tmp_path / "x.md"
    p.write_text(SAMPLE, encoding="utf-8")
    fm, body = parse_file(p)
    assert fm["slug"] == "价值流图"
    assert "正文内容" in body


def test_serialize_field_order():
    fm = {
        "version": 2,
        "tags": ["a"],
        "title": "x",
        "kind": "entity",
        "extra_z": "z",
        "extra_a": "a",
    }
    out = serialize(fm, "body")
    assert out.index("title:") < out.index("version:")
    assert out.index("kind:") < out.index("version:")
    assert out.index("extra_a:") < out.index("extra_z:")
    assert out.endswith("body\n")


def test_serialize_chinese_unicode():
    out = serialize({"title": "工效学"}, "正文")
    assert "工效学" in out
    assert "正文" in out


def test_merge_frontmatter_list_union():
    old = {"tags": ["a", "b"], "sources": ["s1"], "version": 3, "title": "old"}
    new = {"tags": ["b", "c"], "sources": ["s2"], "title": "new"}
    merged = merge_frontmatter(old, new)
    assert merged["tags"] == ["a", "b", "c"]
    assert merged["sources"] == ["s1", "s2"]
    assert merged["title"] == "new"
    assert merged["version"] == 4
    assert "updated" in merged
    assert "created" in merged


def test_merge_frontmatter_no_bump():
    old = {"version": 5}
    new = {"title": "x"}
    merged = merge_frontmatter(old, new, bump_version=False)
    assert merged["version"] == 5
