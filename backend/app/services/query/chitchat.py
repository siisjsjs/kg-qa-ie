"""query/chitchat.py:闲聊 / 自我介绍流式回答。

只在 router 判定 chitchat 时调用。不读 wiki、不抽引用、不评分、不持久化。
与 composer.compose_stream 接口对齐(异步生成器 yield token),让 SSE handler 用同样的循环消费。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from app.services.llm import ChatMessage, get_llm_provider

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
CHITCHAT_TEMPLATE = (PROMPTS_DIR / "chitchat.md").read_text(encoding="utf-8")


def _render_history(history: list[dict] | None) -> str:
    if not history:
        return "(无)"
    lines: list[str] = []
    for h in history:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if not content or role not in ("user", "assistant"):
            continue
        speaker = "用户" if role == "user" else "助手"
        lines.append(f"{speaker}: {content}")
    return "\n".join(lines) or "(无)"


def build_prompt(
    *,
    question: str,
    history: list[dict] | None,
    summary: str | None,
) -> str:
    return (
        CHITCHAT_TEMPLATE
        .replace("{question}", question.strip())
        .replace("{history}", _render_history(history))
        .replace("{summary}", (summary or "").strip() or "(无)")
    )


async def compose_stream(
    *,
    question: str,
    history: list[dict] | None = None,
    summary: str | None = None,
) -> AsyncIterator[str]:
    """流式生成闲聊答案。"""
    prompt = build_prompt(question=question, history=history, summary=summary)
    provider = get_llm_provider()
    sys_text = (
        "你是工业工程知识库系统的随身小助手「小 IE」。这一轮是闲聊或自我介绍,"
        "严格按 prompt 中的语气和约束作答,不要查 wiki、不要写双链引用。"
    )
    if summary:
        sys_text += f"\n\n以下是更早历史的压缩摘要,作为背景参考:\n{summary}"
    messages: list[ChatMessage] = [ChatMessage(role="system", content=sys_text)]
    for h in history or []:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append(ChatMessage(role=role, content=content))
    messages.append(ChatMessage(role="user", content=prompt))
    iterator = await provider.chat(messages, stream=True, temperature=0.7)
    async for token in iterator:  # type: ignore[union-attr]
        yield token
