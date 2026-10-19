"""digest 路由、解析、评分模块单元测试 — P4。"""

from __future__ import annotations

from app.services.digest.parsing import (
    extract_citations,
    find_section,
    is_blank_cell,
    parse_markdown_table,
    parse_timeline_events,
    split_sections,
)
from app.services.digest.router import detect_mode
from app.services.digest.scoring import (
    DEEP_DIVE_REQUIRED_SECTIONS,
    score_comparison,
    score_deep_dive,
    score_timeline,
)


# ===== router =====
def test_detect_mode_comparison_priority():
    assert detect_mode("对比 精益生产 和 六西格玛") == "comparison"
    assert detect_mode("VSM 与 看板的区别") == "comparison"
    # comparison 优先于 timeline
    assert detect_mode("精益生产 vs 六西格玛 的发展史") == "comparison"


def test_detect_mode_timeline():
    assert detect_mode("工业工程的发展史") == "timeline"
    assert detect_mode("精益生产时间线") == "timeline"


def test_detect_mode_deep_dive_default():
    assert detect_mode("深度分析 VSM") == "deep_dive"
    assert detect_mode("综述 工效学") == "deep_dive"
    # 没命中关键词也兜底为 deep_dive
    assert detect_mode("VSM") == "deep_dive"


# ===== parsing =====
def test_split_sections_basic():
    md = "前言文字\n\n## A\n\nA 内容\n\n## B\n\nB 内容\n"
    out = split_sections(md)
    assert len(out) == 3
    assert out[0]["heading"] == ""
    assert out[0]["content"] == "前言文字"
    assert out[1]["heading"] == "A"
    assert "A 内容" in out[1]["content"]


def test_split_sections_no_h2():
    md = "纯文本\n没有 H2"
    out = split_sections(md)
    assert len(out) == 1
    assert out[0]["heading"] == ""


def test_find_section_substring_match():
    sections = [
        {"heading": "背景概述", "content": "x"},
        {"heading": "尚待解决的问题", "content": "y"},
    ]
    sec = find_section(sections, "尚待解决")
    assert sec is not None
    assert sec["content"] == "y"


def test_extract_citations_filters_template_placeholders():
    md = "见 [[VSM]] 与 [[页面A]] 与 [[精益生产]]"
    out = extract_citations(md)
    assert out == ["VSM", "精益生产"]


def test_parse_markdown_table_basic():
    md = """## 对比表
| 维度 | A | B |
|------|---|---|
| 核心 | x | y |
| 适用 | 1 | 2 |
"""
    rows = parse_markdown_table(md)
    assert rows[0] == ["维度", "A", "B"]
    assert rows[1] == ["核心", "x", "y"]
    assert rows[2] == ["适用", "1", "2"]


def test_is_blank_cell_variants():
    assert is_blank_cell("")
    assert is_blank_cell("  ")
    assert is_blank_cell("-")
    assert is_blank_cell("无")
    assert is_blank_cell("待补充")
    assert is_blank_cell("> 待补充:补一些资料")
    assert not is_blank_cell("具体步骤")


def test_parse_timeline_events_basic():
    md = """## 事件说明
- 1947 工业工程协会成立:标志性起点(来源:[[页面X]])
- 1990 精益生产概念提出:从丰田推广(来源:[[精益生产]])
- 2000 六西格玛黑带认证流行
"""
    events = parse_timeline_events(md)
    assert len(events) >= 3
    assert events[0]["date"] == "1947"
    assert "[[精益生产]]" in events[1]["event"]


# ===== scoring: deep_dive =====
def test_score_deep_dive_all_present():
    sections = [
        {"heading": h, "content": "正文段落,内容大致超过 20 字才算实质内容"}
        for h in DEEP_DIVE_REQUIRED_SECTIONS
    ]
    out = score_deep_dive(sections=sections)
    assert out["sections_missing"] == []
    assert out["fill_rate"] == 1.0
    assert out["gap_flag"] is False


def test_score_deep_dive_missing_two_sections_triggers_gap():
    sections = [
        {"heading": "背景概述", "content": "正文段落,内容大致超过 20 字"},
        {"heading": "核心观点", "content": "正文段落,内容大致超过 20 字"},
        {"heading": "知识脉络", "content": "正文段落,内容大致超过 20 字"},
    ]
    out = score_deep_dive(sections=sections)
    # 缺 不同视角对比 + 尚待解决的问题
    assert "不同视角对比" in out["sections_missing"]
    assert "尚待解决的问题" in out["sections_missing"]
    assert out["gap_flag"] is True
    assert out["fill_rate"] < 1.0


def test_score_deep_dive_placeholder_content_counts_as_missing():
    sections = [
        {"heading": h, "content": "> 待补充:此处缺资料"}
        for h in DEEP_DIVE_REQUIRED_SECTIONS
    ]
    out = score_deep_dive(sections=sections)
    assert len(out["placeholder_sections"]) == 5
    assert out["gap_flag"] is True


# ===== scoring: comparison =====
def test_score_comparison_full_table():
    table = [
        ["核心观点", "x", "y"],
        ["适用场景", "a", "b"],
        ["量化指标", "1", "2"],
    ]
    out = score_comparison(
        table=table, dimensions=["核心观点", "适用场景", "量化指标"], objects=["A", "B"]
    )
    assert out["fill_rate"] == 1.0
    assert out["gap_flag"] is False
    assert out["empty_cells"] == []
    assert out["filled_cells"] == 6


def test_score_comparison_with_blanks():
    table = [
        ["核心观点", "x", ""],
        ["适用场景", "-", "b"],
    ]
    out = score_comparison(
        table=table, dimensions=["核心观点", "适用场景"], objects=["A", "B"]
    )
    # 4 格,2 空,fill_rate=0.5
    assert out["filled_cells"] == 2
    assert out["fill_rate"] == 0.5
    assert out["gap_flag"] is True
    cells = set(out["empty_cells"])
    assert ("核心观点", "B") in cells
    assert ("适用场景", "A") in cells


def test_score_comparison_empty_table():
    out = score_comparison(table=[], dimensions=[], objects=[])
    assert out["gap_flag"] is True
    assert out["filled_cells"] == 0


# ===== scoring: timeline =====
def test_score_timeline_3_plus_events_no_gap():
    events = [
        {"date": "1947", "event": "x"},
        {"date": "1990", "event": "y"},
        {"date": "2000", "event": "z"},
        {"date": "2010", "event": "w"},
    ]
    out = score_timeline(events=events)
    assert out["events_count"] == 4
    assert out["sparse"] is False
    assert out["gap_flag"] is False
    # 1947→2010 跨 63 年 ≈ 6.3 decades, 4/6.3 ~ 0.63
    assert out["events_per_decade"] > 0


def test_score_timeline_few_events_triggers_gap():
    events = [{"date": "1990", "event": "x"}]
    out = score_timeline(events=events)
    assert out["events_count"] == 1
    assert out["sparse"] is True
    assert out["gap_flag"] is True


def test_score_timeline_no_events():
    out = score_timeline(events=[])
    assert out["events_count"] == 0
    assert out["events_per_decade"] == 0.0
    assert out["gap_flag"] is True
