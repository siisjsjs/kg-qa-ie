"""query/agent.py:Agentic RAG 编排器(改造计划 §6.1)。

原生 function calling 的 ReAct 循环,最多 2 轮工具调用,第 3 轮强制纯作答。

第一轮 content 处理策略(§6.1 + §14 实测折中):
  content 先缓冲;出现首个 tool_call 分片 → 缓冲内容转为"思考旁白"(tool_start
  的 reason),不进正文;缓冲超过阈值仍无工具信号 → 开始直接流出(保首字延迟);
  已流出后才出现 tool_calls → 已流出的文字保留在正文,不再清空(用户可见内容
  不能被吞掉),旁白只来自未流出的缓冲部分。

点名专业图时传入 required_first="generate_chart":首轮 tool_choice 强制该工具,
且不把缓冲当正文提前冲出——等工具(同步出图)返回后再写带占位的答案。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path

from loguru import logger

from app.services.llm import ChatMessage, get_llm_provider
from app.services.llm.base import ToolCall
from app.services.query.evidence_pool import EvidencePool
from app.services.tools.base import TOOL_UNAVAILABLE_RESULT, Tool

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

# 缓冲阈值:多数模型要么直接调工具(此前只说一两句话),要么直接作答。
# 超过该字数还没有工具信号,基本可断定是直答,开始流出保住首字延迟。
_NARRATION_BUFFER_CHARS = 200
# 最多 3 轮工具;第 4 轮 tool_choice="none" 强制纯作答。
# 轮次从 2 提到 3:常见链路是 检索(1) → 模板推荐(2) → 服务端出图(3),
# 2 轮时出图轮被掐掉,模型在纯文本回合输出 DSML 假调用(<invoke>)污染正文
# 且图表参数不完整(values 空)。第 4 轮强制纯作答仍是防死循环兜底。
MAX_TOOL_ROUNDS = 3


@lru_cache(maxsize=1)
def _system_template() -> str:
    return (PROMPTS_DIR / "agent_system.md").read_text(encoding="utf-8")


def _build_system(summary: str | None) -> str:
    text = _system_template()
    if summary and summary.strip():
        text += f"\n\n# 更早历史的压缩摘要(背景参考,非检索结果)\n\n{summary.strip()}"
    return text


def _build_user_message(question: str, evidence: EvidencePool, tool_hints: str | None = None) -> str:
    text = (
        f"## 用户问题\n\n{question.strip()}\n\n"
        f"## 编号证据\n\n{evidence.render_for_prompt()}"
    )
    if tool_hints and tool_hints.strip():
        text += f"\n\n## 绘图平台提示\n\n{tool_hints.strip()}"
    return text


def _forced_tool_choice(active_tools: dict[str, Tool], required_first: str | None) -> str | dict:
    if required_first and required_first in active_tools:
        return {"type": "function", "function": {"name": required_first}}
    return "auto"


def _parse_args(call: ToolCall) -> dict:
    try:
        obj = json.loads(call.arguments or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


async def _execute_tool(tool: Tool | None, call: ToolCall, ctx: dict) -> tuple[dict, bool]:
    """执行单个工具调用,返回 (给模型的结果, 是否失败)。永不抛错。"""
    if tool is None:
        return dict(TOOL_UNAVAILABLE_RESULT), True
    try:
        result = await asyncio.wait_for(tool.run(_parse_args(call), ctx), timeout=tool.timeout)
        return result, False
    except asyncio.TimeoutError:
        logger.warning("tool timeout: {} (>{}s)", call.name, tool.timeout)
        return dict(TOOL_UNAVAILABLE_RESULT), True
    except Exception as exc:  # noqa: BLE001
        logger.warning("tool failed: {} err={!r}", call.name, exc)
        return dict(TOOL_UNAVAILABLE_RESULT), True


async def run_agent_stream(
    *,
    question: str,
    history: list[dict] | None,
    summary: str | None,
    evidence: EvidencePool,
    tools: list[Tool],
    ctx: dict,
    llm_stats: dict | None = None,
    tool_hints: str | None = None,
    required_first: str | None = None,
) -> AsyncIterator[dict]:
    """Agent 循环,yield 与 SSE 对齐的内部事件:

      {"type": "token", "text"}
      {"type": "replace_answer", "text"}
      {"type": "tool_start", "tool", "args", "reason"}
      {"type": "tool_result", "tool", "ok", "summary"}
      {"type": "sse", "event", "data"}          # 工具产生的领域事件(chart_templates 等)
      {"type": "agent_done", "answer"}
    """
    provider = get_llm_provider()
    messages: list[ChatMessage] = [ChatMessage(role="system", content=_build_system(summary))]
    for h in history or []:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append(ChatMessage(role=role, content=content))
    messages.append(
        ChatMessage(role="user", content=_build_user_message(question, evidence, tool_hints))
    )

    active_tools: dict[str, Tool] = {t.name: t for t in tools}
    tool_rounds = 0
    answer = ""

    while True:
        allow_tools = bool(active_tools) and tool_rounds < MAX_TOOL_ROUNDS
        specs = [t.spec() for t in active_tools.values()] if allow_tools else None

        round_stats: dict = {}
        buffered: list[str] = []
        flushed: list[str] = []
        narration_mode = False  # 收到工具信号后,后续 content 全部算旁白
        calls: list[ToolCall] = []

        hold_for_required = bool(
            specs and required_first and tool_rounds == 0 and required_first in active_tools
        )
        if specs:
            choice = (
                _forced_tool_choice(active_tools, required_first)
                if tool_rounds == 0
                else "auto"
            )
            iterator = provider.chat_tools(
                messages, tools=specs, tool_choice=choice, stats=round_stats
            )
        else:
            # 无可用工具或已达轮次上限:纯流式作答(不带 tools 参数,兼容所有服务)
            iterator = _plain_stream(provider, messages, round_stats)

        async for event in iterator:
            etype = event.get("type")
            if etype == "token":
                text = event["text"]
                if narration_mode:
                    buffered.append(text)
                    continue
                if flushed:
                    flushed.append(text)
                    yield {"type": "token", "text": text}
                    continue
                buffered.append(text)
                # 点名出图首轮先等工具;不要把「怎么画」长文提前冲成正文
                if hold_for_required:
                    continue
                if sum(len(t) for t in buffered) > _NARRATION_BUFFER_CHARS:
                    # 无工具信号且已超阈值 → 判定直答,整体放行并转直接流出
                    joined = "".join(buffered)
                    buffered.clear()
                    flushed.append(joined)
                    yield {"type": "token", "text": joined}
            elif etype == "tool_call_begin":
                narration_mode = True
                # 已流出的文字保留在正文(不再发 replace_answer 清空):
                # 用户已经看到的"半截答案"不能被吞掉,工具调用后继续往后写。
                # 未流出的 buffered 部分仍转为思考旁白(tool_start.reason)。
            elif etype == "tool_calls":
                calls = event["calls"]
            elif etype == "finish":
                if llm_stats is not None:
                    llm_stats.update(round_stats)

        if not calls:
            # 无工具调用 → 本轮 content 就是正文
            if buffered:
                joined = "".join(buffered)
                flushed.append(joined)
                yield {"type": "token", "text": joined}
            answer = "".join(flushed)
            break

        # ---- 有工具调用:本轮 content 是思考旁白 ----
        narration = "".join(buffered).strip()
        tool_rounds += 1
        messages.append(
            ChatMessage(role="assistant", content=narration, tool_calls=calls)
        )

        for call in calls:
            yield {
                "type": "tool_start",
                "tool": call.name,
                "args": _parse_args(call),
                "reason": narration,
            }
        results = await asyncio.gather(
            *(_execute_tool(active_tools.get(call.name), call, ctx) for call in calls)
        )
        for call, (result, failed) in zip(calls, results, strict=False):
            messages.append(
                ChatMessage(
                    role="tool",
                    content=json.dumps(result, ensure_ascii=False),
                    tool_call_id=call.id,
                )
            )
            if failed:
                # 运行层降级:同一工具单次回答内失败 1 次即从后续轮次移除
                active_tools.pop(call.name, None)
            yield {
                "type": "tool_result",
                "tool": call.name,
                "ok": not failed,
                "summary": _summarize_result(result),
            }
        # 工具产生的领域事件(chart_templates / web_citations / image_pending)
        for evt in ctx.pop("events", []):
            yield {"type": "sse", "event": evt["event"], "data": evt["data"]}

    yield {"type": "agent_done", "answer": answer}


async def _plain_stream(provider, messages: list[ChatMessage], stats: dict):
    """无工具时的纯流式作答,包装成与 chat_tools 相同的事件形状。"""
    iterator = await provider.chat(messages, stream=True, stats=stats)
    async for token in iterator:
        yield {"type": "token", "text": token}
    yield {"type": "finish", "finish_reason": stats.get("finish_reason")}


def _summarize_result(result: dict) -> str:
    if "error" in result:
        return str(result["error"])[:120]
    if "result" in result:
        return str(result["result"]).split("\n", 1)[0][:120]
    if result.get("status") == "pending":
        return "图片后台生成中"
    return str(result)[:120]
