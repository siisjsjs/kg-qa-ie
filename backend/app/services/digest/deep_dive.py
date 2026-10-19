"""digest/deep_dive.py:5 个 H2 深度报告模板。

调 LLM 生成 markdown → 解析 H2 → 检查必备章节缺失 → 抽 citations + followups。
"""

from __future__ import annotations

from pathlib import Path

from app.services.digest.builder import (
    alias_table_for,
    call_llm_markdown,
    collect_keywords_for_digest,
    gather_context,
    render_deep_dive_prompt,
)
from app.services.digest.parsing import (
    extract_citations,
    find_section,
    split_sections,
)
from app.services.digest.scoring import (
    DEEP_DIVE_REQUIRED_SECTIONS,
    score_deep_dive,
)


def _extract_followups(sections: list[dict]) -> list[str]:
    """从 ## 尚待解决的问题 段抽出 - 项作为 followups。"""
    sec = find_section(sections, "尚待解决")
    if sec is None:
        return []
    items: list[str] = []
    for raw in (sec["content"] or "").splitlines():
        line = raw.strip()
        if line.startswith(("- ", "* ")):
            items.append(line[2:].strip())
        elif line.startswith(("1.", "2.", "3.", "4.", "5.")):
            items.append(line.split(".", 1)[1].strip())
    return [x for x in items if x]


async def compose_deep_dive(
    *,
    topic: str,
    wiki_root: Path,
    domain_ie: bool = False,  # noqa: ARG001 (P5+ 留口)
) -> dict:
    """生成 deep-dive markdown 并解析,返回结构化结果。

    Returns:
        {
            "raw_markdown": str,
            "sections": [{heading, content}],
            "citations": [slug, ...],
            "followups": [str, ...],
            "score": {sections_missing, fill_rate?, gap_flag},
        }
    """
    keywords = collect_keywords_for_digest(topic, [])
    context_pages = await gather_context(keywords=keywords, wiki_root=wiki_root)
    prompt = render_deep_dive_prompt(topic=topic, context_pages=context_pages)
    md = await call_llm_markdown(prompt)
    sections = split_sections(md)
    citations = extract_citations(md)
    followups = _extract_followups(sections)
    score = score_deep_dive(sections=sections)

    return {
        "raw_markdown": md,
        "sections": sections,
        "citations": citations,
        "followups": followups,
        "score": score,
        "required_sections": list(DEEP_DIVE_REQUIRED_SECTIONS),
        "keywords": keywords,
    }
