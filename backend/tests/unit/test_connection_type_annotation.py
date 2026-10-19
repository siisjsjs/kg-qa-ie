"""单测:step1.connections → 正文 [[X]] 关系类型注释。"""

from __future__ import annotations

from app.services.ingestion.pipeline import (
    _annotate_connection_types,
    _build_connection_type_map,
)


def test_build_map_skips_void_and_self_loops():
    conns = [
        {"src": "A", "dst": "B", "rel_type": "实现"},
        {"src": "C", "dst": "D", "rel_type": "相关"},   # 无信息量,跳过
        {"src": "E", "dst": "E", "rel_type": "依赖"},   # 自环,跳过
        {"src": "F", "dst": "", "rel_type": "对比"},     # 缺端,跳过
    ]
    m = _build_connection_type_map(conns, alias_to_canonical={})
    assert m == {("A", "B"): "实现"}


def test_build_map_keeps_direction():
    """src→dst 与 dst→src 是两条不同的键,方向必须保留。"""
    conns = [{"src": "机器干扰宽放", "dst": "操作宽放", "rel_type": "属于"}]
    m = _build_connection_type_map(conns, alias_to_canonical={})
    assert m == {("机器干扰宽放", "操作宽放"): "属于"}
    # 反方向不应存在
    assert ("操作宽放", "机器干扰宽放") not in m


def test_build_map_applies_alias_canonical():
    conns = [{"src": "ergonomics", "dst": "人因工程", "rel_type": "依赖"}]
    alias = {"ergonomics": "工效学"}
    m = _build_connection_type_map(conns, alias_to_canonical=alias)
    assert m == {("工效学", "人因工程"): "依赖"}


def test_annotate_adds_type_comment_to_entity_body():
    files = {
        "wiki/entities/工效学.md": "正文提到 [[人因工程]] 很关键。\n",
        "wiki/sources/s1.md": "素材引用 [[人因工程]]。\n",  # source 不参与
    }
    conns = [{"src": "工效学", "dst": "人因工程", "rel_type": "依赖"}]
    out, applied = _annotate_connection_types(files, connections=conns, alias_to_canonical={})
    assert applied == 1
    assert "[[人因工程]] <!-- type: 依赖 --> <!-- confidence: INFERRED -->" in out["wiki/entities/工效学.md"]
    # source 文件不动
    assert out["wiki/sources/s1.md"] == files["wiki/sources/s1.md"]


def test_annotate_does_not_double_annotate():
    files = {
        "wiki/entities/工效学.md": "已有注释 [[人因工程]] <!-- type: 实现 --> 不该被覆盖。\n",
    }
    conns = [{"src": "工效学", "dst": "人因工程", "rel_type": "依赖"}]
    out, applied = _annotate_connection_types(files, connections=conns, alias_to_canonical={})
    assert applied == 0
    assert out["wiki/entities/工效学.md"] == files["wiki/entities/工效学.md"]


def test_annotate_skips_links_without_connection():
    files = {"wiki/entities/工效学.md": "提到 [[无关概念]]。\n"}
    conns = [{"src": "工效学", "dst": "人因工程", "rel_type": "依赖"}]
    out, applied = _annotate_connection_types(files, connections=conns, alias_to_canonical={})
    assert applied == 0
    assert out["wiki/entities/工效学.md"] == files["wiki/entities/工效学.md"]
