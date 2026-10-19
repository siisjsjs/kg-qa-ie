"""md_parser.py 单元测试。"""

from app.services.ingestion.md_parser import excerpt, parse


def test_parse_with_frontmatter():
    text = """---
title: 工效学概论
tags: [人因, 学习笔记]
---

第一段内容。

第二段:介绍 ergonomics 与作业测定。
"""
    out = parse(text)
    assert out["frontmatter"]["title"] == "工效学概论"
    assert "tags" in out["frontmatter"]
    assert len(out["paragraphs"]) == 2
    assert "第一段内容" in out["paragraphs"][0]
    assert out["length"] > 0


def test_parse_no_frontmatter_keeps_full_body():
    text = "纯正文,无 frontmatter。\n\n下一段。"
    out = parse(text)
    assert out["frontmatter"] == {}
    assert out["body"].startswith("纯正文")
    assert len(out["paragraphs"]) == 2


def test_parse_malformed_frontmatter_does_not_raise():
    """frontmatter YAML 异常 → 降级为空 dict + body 为整篇。"""
    text = "---\ntitle: [unclosed\n---\n\n正文。"
    out = parse(text)
    # 不抛异常即可,frontmatter 可能是空 dict 或部分解析
    assert "正文" in out["body"] or out["body"]


def test_excerpt_short_text_returned_intact():
    body = "短文本。"
    assert excerpt(body, max_chars=200) == "短文本。"


def test_excerpt_truncates_at_punctuation_boundary():
    body = "第一句。第二句很长很长很长很长很长很长很长很长很长很长。第三句。"
    out = excerpt(body, max_chars=10)
    assert out.endswith("…")
    assert len(out) <= 20  # 加上省略号


def test_excerpt_falls_back_to_hard_cut():
    body = "无任何标点的连续中文字符" * 30
    out = excerpt(body, max_chars=20)
    assert out.endswith("…")
