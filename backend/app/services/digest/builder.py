"""digest/builder.py:digest 三模板的 LLM 调用与上下文准备。

把 query/grep + page_reader 的能力复用过来,准备 context_pages,然后调 LLM 得到 markdown。
"""

from __future__ import annotations

from pathlib import Path

from app.services.ingestion.merger import load_alias_groups
from app.services.llm import ChatMessage, get_llm_provider
from app.services.query.composer import render_alias_table, render_context
from app.services.query.grep import grep_wiki
from app.services.query.page_reader import read_pages_for_context

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

DIGEST_DEEP_DIVE = (PROMPTS_DIR / "digest_deep_dive.md").read_text(encoding="utf-8")
DIGEST_COMPARISON = (PROMPTS_DIR / "digest_comparison.md").read_text(encoding="utf-8")
DIGEST_TIMELINE = (PROMPTS_DIR / "digest_timeline.md").read_text(encoding="utf-8")


async def gather_context(
    *,
    keywords: list[str],
    wiki_root: Path,
    top_k: int = 8,
) -> list[dict]:
    """根据关键词在 wiki/ 下检索 top-k 候选页,并按规则读出精简上下文。"""
    candidates = await grep_wiki(keywords=keywords, wiki_root=wiki_root, top_k=top_k)
    # 逐页各自截断时 8 页一起能顶到总预算之上,且截法是"页头 + 命中行",
    # 与 keywords 无关的小节照收。走共享预算读,按 keywords 选相关小节。
    return await read_pages_for_context(
        candidates,
        wiki_root=wiki_root,
        question=" ".join(keywords),
        keywords=keywords,
    )


def render_deep_dive_prompt(*, topic: str, context_pages: list[dict]) -> str:
    return (
        DIGEST_DEEP_DIVE.replace("{topic}", topic)
        .replace("{context_pages}", render_context(context_pages))
    )


def render_comparison_prompt(*, objects: list[str], context_pages: list[dict]) -> str:
    obj_list = "\n".join(f"- {o}" for o in objects) if objects else "(未指定对象)"
    return (
        DIGEST_COMPARISON.replace("{objects}", obj_list)
        .replace("{context_pages}", render_context(context_pages))
    )


def render_timeline_prompt(*, topic: str, context_pages: list[dict]) -> str:
    return (
        DIGEST_TIMELINE.replace("{topic}", topic)
        .replace("{context_pages}", render_context(context_pages))
    )


async def call_llm_markdown(prompt: str, *, system: str | None = None) -> str:
    """单次非流式调用 LLM,返回 markdown 文本。"""
    provider = get_llm_provider()
    msgs = [
        ChatMessage(
            role="system",
            content=system
            or "你是工业工程知识库的综合分析助手,严格按用户要求的格式与节标题组织内容,使用中文。",
        ),
        ChatMessage(role="user", content=prompt),
    ]
    raw = await provider.chat(msgs, stream=False)
    return raw if isinstance(raw, str) else ""


def collect_keywords_for_digest(topic: str, objects: list[str] | None) -> list[str]:
    """从 topic + objects 生成关键词列表。"""
    seen: set[str] = set()
    out: list[str] = []
    for src in [topic, *(objects or [])]:
        for tok in (src or "").replace(",", " ").replace("、", " ").split():
            tok = tok.strip()
            if tok and tok not in seen:
                seen.add(tok)
                out.append(tok)
        if src and src.strip() not in seen:
            seen.add(src.strip())
            out.append(src.strip())
    return out


def alias_table_for(wiki_root: Path) -> list[list[str]]:
    return load_alias_groups(Path(wiki_root))


__all__ = [
    "alias_table_for",
    "call_llm_markdown",
    "collect_keywords_for_digest",
    "gather_context",
    "render_alias_table",
    "render_comparison_prompt",
    "render_deep_dive_prompt",
    "render_timeline_prompt",
]
