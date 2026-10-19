"""wiki/link_resolver 单元测试。"""

from __future__ import annotations

from pathlib import Path

from app.services.wiki.link_resolver import (
    aggregate_edges,
    autolink_entities,
    extract_links,
    extract_links_with_meta,
    find_backlinks,
    find_broken_links,
    rebuild_links_index,
    slugs_referenced_by_source,
)


def test_extract_links_simple():
    links = extract_links("see [[价值流图]] and [[VSM|价值流图]]")
    assert links == [("价值流图", None), ("VSM", "价值流图")]


def test_extract_links_with_meta():
    body = "用 [[VSM]] <!-- type: 实现 --> 来识别 [[浪费]] <!-- confidence: EXTRACTED -->"
    out = extract_links_with_meta(body)
    by_target = {e["target"]: e for e in out}
    assert by_target["VSM"]["rel_type"] == "实现"
    assert by_target["浪费"]["confidence"] == "EXTRACTED"


def test_extract_links_aggregates_occurrences():
    body = "[[A]] 然后又是 [[A]]"
    out = extract_links_with_meta(body)
    assert len(out) == 1
    assert out[0]["occurrences"] == 2


def _write_page(root: Path, kind: str, slug: str, body: str, fm: dict | None = None) -> None:
    page = root / "wiki" / kind / f"{slug}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    fm_block = ""
    if fm:
        import yaml
        fm_block = "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n\n"
    page.write_text(fm_block + body, encoding="utf-8")


def test_rebuild_links_index(tmp_path: Path):
    _write_page(tmp_path, "entities", "VSM", "属于 [[精益生产]] 工具")
    _write_page(tmp_path, "entities", "精益生产", "包括 [[VSM]] 和 [[5S]]")
    edges = rebuild_links_index(tmp_path)
    pairs = {(e["src_slug"], e["dst_slug"]) for e in edges}
    assert ("VSM", "精益生产") in pairs
    assert ("精益生产", "VSM") in pairs
    assert ("精益生产", "5S") in pairs


def test_find_backlinks(tmp_path: Path):
    _write_page(tmp_path, "entities", "A", "ref [[X]]")
    _write_page(tmp_path, "entities", "B", "also [[X]]")
    _write_page(tmp_path, "entities", "C", "no ref")
    _write_page(tmp_path, "entities", "X", "self")
    bl = find_backlinks("X", tmp_path)
    assert set(bl) == {"A", "B"}


def test_find_broken_links(tmp_path: Path):
    _write_page(tmp_path, "entities", "A", "ref [[X]] 和 [[Y]]")
    _write_page(tmp_path, "entities", "X", "ok")
    broken = find_broken_links(tmp_path)
    targets = {b["dst_slug"] for b in broken}
    assert "Y" in targets
    assert "X" not in targets


def test_autolink_entities_first_occurrence_only():
    body = "我用 VSM 分析,后面 VSM 不再补;价值流图 也是 VSM 的别名"
    out = autolink_entities(body, alias_groups=[["VSM", "价值流图"]])
    # 第一次出现 VSM 被包,后续 VSM 不重复
    assert "[[VSM]]" in out
    assert out.count("[[VSM]]") == 1


def test_autolink_skips_existing_wikilinks():
    body = "已经有 [[VSM]] 了"
    out = autolink_entities(body, alias_groups=[["VSM"]])
    # 不应产生 [[[[VSM]]]]
    assert "[[[[" not in out


def test_autolink_longer_term_first():
    # "价值流图" 应优先于 "价值",避免错误匹配
    body = "价值流图 是核心"
    out = autolink_entities(body, alias_groups=[["价值流图"]])
    assert "[[价值流图]]" in out


def test_slugs_referenced_by_source(tmp_path: Path):
    _write_page(tmp_path, "entities", "A", "正文", fm={"sources": ["src1", "src2"]})
    _write_page(tmp_path, "entities", "B", "正文", fm={"sources": ["src1"]})
    _write_page(tmp_path, "entities", "C", "正文", fm={"sources": ["src9"]})
    assert set(slugs_referenced_by_source("src1", tmp_path)) == {"A", "B"}


def test_aggregate_edges():
    edges = [
        {"src_slug": "X", "dst_slug": "Y", "link_type": "实现", "occurrences": 1, "confidence": "EXTRACTED"},
        {"src_slug": "X", "dst_slug": "Y", "link_type": "实现", "occurrences": 2, "confidence": None},
        {"src_slug": "X", "dst_slug": "Y", "link_type": "依赖", "occurrences": 1, "confidence": None},
    ]
    out = aggregate_edges(edges)
    by_type = {e["link_type"]: e for e in out}
    assert by_type["实现"]["occurrences"] == 3
    assert by_type["实现"]["confidence"] == "EXTRACTED"
    assert by_type["依赖"]["occurrences"] == 1
