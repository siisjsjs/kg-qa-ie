"""digest/comparison.py:对比表(IE 模式 6 维)。

调 LLM 生成对比表 markdown → 解析 ## 对比表 段下 markdown table →
计算 fill_rate + 抽空格 → 评分。
"""

from __future__ import annotations

from pathlib import Path

from app.services.digest.builder import (
    call_llm_markdown,
    collect_keywords_for_digest,
    gather_context,
    render_comparison_prompt,
)
from app.services.digest.parsing import (
    extract_citations,
    find_section,
    parse_markdown_table,
    split_sections,
)
from app.services.digest.scoring import score_comparison

IE_COMPARISON_DIMENSIONS = [
    "核心观点",
    "适用场景",
    "量化指标",
    "实施成本",
    "典型陷阱",
    "来源素材",
]


def extract_comparison_table(md: str) -> tuple[list[list[str]], list[str], list[str]]:
    """从对比 markdown 抽对比表。

    Returns:
        (table_rows_excl_header, dimensions, header_objects)
        - table_rows_excl_header:不含表头的数据行,首列是维度名
        - dimensions:数据行首列(维度名)列表
        - header_objects:表头去掉首列后的对象名列表
    """
    sections = split_sections(md)
    sec = find_section(sections, "对比表") or find_section(sections, "比较表")
    if sec is None:
        return [], [], []
    rows = parse_markdown_table(sec["content"])
    if len(rows) < 2:
        return [], [], []
    header = rows[0]
    data = rows[1:]
    if not header or len(header) < 2:
        return [], [], []
    objects = [h.strip() for h in header[1:]]
    dimensions = [r[0].strip() if r else "" for r in data]
    return data, dimensions, objects


async def compose_comparison(
    *,
    topic: str,
    objects: list[str],
    wiki_root: Path,
    domain_ie: bool = True,  # noqa: ARG001
) -> dict:
    """生成 comparison markdown 并解析。

    Returns:
        {
            "raw_markdown": str,
            "sections": [{heading, content}],
            "table": [[cell, ...], ...],         # 不含表头
            "dimensions": [str, ...],
            "objects": [str, ...],
            "citations": [slug, ...],
            "empty_cells": [(row_dim, col_obj), ...],
            "score": {fill_rate, sections_missing, gap_flag, ...},
        }
    """
    if not objects:
        # 也允许从 topic 中抽对比对(简单按"对比 X 和 Y"切)
        objects = _infer_objects_from_topic(topic)

    keywords = collect_keywords_for_digest(topic, objects)
    context_pages = await gather_context(keywords=keywords, wiki_root=wiki_root)
    prompt = render_comparison_prompt(objects=objects, context_pages=context_pages)
    md = await call_llm_markdown(prompt)

    sections = split_sections(md)
    table, dimensions, header_objects = extract_comparison_table(md)
    score = score_comparison(
        table=table,
        dimensions=dimensions or IE_COMPARISON_DIMENSIONS,
        objects=header_objects or objects,
    )
    citations = extract_citations(md)

    return {
        "raw_markdown": md,
        "sections": sections,
        "table": table,
        "dimensions": dimensions,
        "objects": header_objects or objects,
        "citations": citations,
        "empty_cells": score.get("empty_cells", []),
        "score": score,
        "keywords": keywords,
    }


def _infer_objects_from_topic(topic: str) -> list[str]:
    """从 topic 简单切对比对,如"对比 精益生产 和 六西格玛"→ ['精益生产', '六西格玛']。"""
    s = topic
    for word in ("对比", "比较", "区别", "异同"):
        s = s.replace(word, " ")
    parts: list[str] = []
    for sep in ("和", "与", "vs", "VS", "、", ","):
        if sep in s:
            parts = [p.strip() for p in s.split(sep) if p.strip()]
            break
    if not parts:
        parts = [p.strip() for p in s.split() if p.strip()]
    return [p for p in parts if 1 <= len(p) <= 30][:4]
