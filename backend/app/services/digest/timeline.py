"""digest/timeline.py:时间线模板。

调 LLM 生成 timeline markdown → 解析 ## 事件说明 → 评分。
"""

from __future__ import annotations

from pathlib import Path

from app.services.digest.builder import (
    call_llm_markdown,
    collect_keywords_for_digest,
    gather_context,
    render_timeline_prompt,
)
from app.services.digest.parsing import (
    extract_citations,
    find_section,
    parse_timeline_events,
    split_sections,
)
from app.services.digest.scoring import score_timeline


async def compose_timeline(
    *,
    topic: str,
    wiki_root: Path,
) -> dict:
    """生成 timeline markdown 并解析。

    Returns:
        {
            "raw_markdown": str,
            "sections": [{heading, content}],
            "events": [{date, event, sources}],
            "citations": [slug, ...],
            "score": {events_count, sparse, gap_flag, ...},
        }
    """
    keywords = collect_keywords_for_digest(topic, [])
    context_pages = await gather_context(keywords=keywords, wiki_root=wiki_root)
    prompt = render_timeline_prompt(topic=topic, context_pages=context_pages)
    md = await call_llm_markdown(prompt)

    sections = split_sections(md)
    sec = find_section(sections, "事件说明")
    events: list[dict] = []
    if sec is not None:
        events = parse_timeline_events(sec["content"])
    if not events:
        # 兜底:全文扫
        events = parse_timeline_events(md)

    score = score_timeline(events=events)
    citations = extract_citations(md)

    return {
        "raw_markdown": md,
        "sections": sections,
        "events": events,
        "citations": citations,
        "score": score,
        "keywords": keywords,
    }
