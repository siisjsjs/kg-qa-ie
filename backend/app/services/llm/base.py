"""LLM provider 抽象基类。

所有 provider 必须实现：
  - chat(messages, stream=False, **params) -> str | AsyncIterator[str]
  - test_connection() -> tuple[bool, str]  # (ok, sample_or_error)

支持 function calling 的 provider 另需实现：
  - chat_tools(messages, tools=..., **params) -> AsyncIterator[dict]  # 结构化流式事件
"""

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from loguru import logger

# max_tokens<=0 时用的显式上限。省略该字段等于把上限交给服务端默认值,而推理模型的
# reasoning_tokens 与正文共用这一个预算且波动极大(同一 prompt 实测 159~5117),
# 一旦推理冲高就在句子中间截断。给一个足够大的显式值,行为才可预测。
FALLBACK_MAX_TOKENS = 24576

# 提取/知识构建类调用的输出上限。用户要求"质量优先、不设成本约束,跳到上限"——
# 实测 deepseek-v4-flash 对 max_tokens 合法范围 [1, 393216],393216 接受、393217 拒绝。
# 取天花板 393216。仅提取流水线显式引用此常量,问答(QA) 仍走 FALLBACK_MAX_TOKENS,不受影响。
EXTRACTION_MAX_TOKENS = 393216


@dataclass
class ToolCall:
    """一次工具调用（与 OpenAI 协议对齐,arguments 为 JSON 字符串）。"""

    id: str
    name: str
    arguments: str


@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCall] | None = None  # assistant 消息携带
    tool_call_id: str | None = None  # tool 消息携带


def messages_to_payload(messages: list[ChatMessage]) -> list[dict]:
    """ChatMessage 列表 → OpenAI Chat Completions messages 数组。

    assistant 带 tool_calls 时输出 OpenAI 格式的 tool_calls 数组；
    tool 消息输出 {"role":"tool","tool_call_id",...}。
    """
    out: list[dict] = []
    for m in messages:
        item: dict = {"role": m.role, "content": m.content}
        if m.role == "assistant" and m.tool_calls:
            item["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in m.tool_calls
            ]
        if m.role == "tool":
            item["tool_call_id"] = m.tool_call_id or ""
        out.append(item)
    return out


@dataclass
class _ToolCallSlot:
    id: str = ""
    name: str = ""
    arguments: str = ""


class ToolCallAccumulator:
    """按 delta.tool_calls 的 index 增量拼装工具调用。

    容忍 DeepSeek 偶发的空片段、缺 index、乱序 index（见改造计划 §14）。
    """

    def __init__(self) -> None:
        self._slots: dict[int, _ToolCallSlot] = {}
        self._last_index: int = 0

    def feed(self, delta_tool_calls: list | None) -> None:
        for frag in delta_tool_calls or []:
            if not isinstance(frag, dict):
                continue
            idx = frag.get("index")
            if idx is None:
                idx = self._last_index
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                idx = self._last_index
            self._last_index = idx
            slot = self._slots.setdefault(idx, _ToolCallSlot())
            if frag.get("id"):
                slot.id = str(frag["id"])
            fn = frag.get("function") or {}
            if fn.get("name"):
                slot.name += str(fn["name"])
            if fn.get("arguments"):
                slot.arguments += str(fn["arguments"])

    def build(self) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for idx in sorted(self._slots):
            slot = self._slots[idx]
            if not slot.name:
                continue  # 没有函数名的残片直接丢弃
            calls.append(
                ToolCall(
                    id=slot.id or f"call_{idx}",
                    name=slot.name,
                    arguments=slot.arguments or "{}",
                )
            )
        return calls


async def parse_tools_sse(
    lines: AsyncIterator[str],
    *,
    stats: dict | None = None,
) -> AsyncIterator[dict]:
    """解析 OpenAI 兼容 SSE 行流为结构化事件（deepseek/openai_compat 共享）。

    yield:
      {"type": "token", "text": str}                    # 正文增量
      {"type": "tool_call_begin"}                       # 首个 tool_call 分片到达(早期信号)
      {"type": "tool_calls", "calls": [ToolCall, ...]}  # 完整拼装后一次性给出
      {"type": "finish", "finish_reason": str|None}
    """
    acc = ToolCallAccumulator()
    finish_reason: str | None = None
    began = False
    async for line in lines:
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        choice = (chunk.get("choices") or [{}])[0]
        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]
        if chunk.get("usage") and stats is not None:
            stats["usage"] = chunk["usage"]
        delta = choice.get("delta") or {}
        if delta.get("tool_calls"):
            if not began:
                began = True
                yield {"type": "tool_call_begin"}
            acc.feed(delta["tool_calls"])
        text = delta.get("content")
        if text:
            yield {"type": "token", "text": text}
    calls = acc.build()
    if calls:
        yield {"type": "tool_calls", "calls": calls}
    yield {"type": "finish", "finish_reason": finish_reason}


class LLMProvider(ABC):
    """LLM provider 统一接口。"""

    name: str = "base"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        extra_params: dict | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_params = extra_params or {}

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        stream: bool = False,
        **params,
    ) -> str | AsyncIterator[str]:
        """对话补全。stream=True 时返回 token 异步迭代器。"""

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        """连通性测试：返回 (ok, sample_output_or_error_message)。"""

    async def chat_tools(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict],
        **params,
    ) -> AsyncIterator[dict]:
        """带 function calling 的流式对话（仅流式,无非流式场景）。

        yield 结构化事件:
          {"type": "token", "text": str}                    # 正文增量
          {"type": "tool_calls", "calls": [ToolCall, ...]}  # 完整拼装后一次性给出
          {"type": "finish", "finish_reason": str|None}
        """
        raise NotImplementedError(f"{self.name} provider 不支持 function calling")
        yield {}  # pragma: no cover — 使本方法成为 async generator,统一 async for 用法

    def resolve_max_tokens(self, params: dict) -> int:
        """max_tokens <= 0 表示「不要人为压低上限」,但不能真的省略该字段。"""
        mt = params.get("max_tokens", self.max_tokens)
        return mt if mt and mt > 0 else FALLBACK_MAX_TOKENS

    def _record_finish(
        self,
        finish_reason: str | None,
        stats: dict | None,
        max_tokens: int,
    ) -> None:
        """流结束后落账 finish_reason。

        finish_reason == "length" 说明正文在 max_tokens 处被硬切,答案是残的。
        以前没人读这个字段,截断因此无声无息,只能靠用户发现句子断在一半。
        """
        truncated = finish_reason == "length"
        if stats is not None:
            stats["finish_reason"] = finish_reason
            stats["truncated"] = truncated
        if truncated:
            logger.warning(
                "LLM output truncated at max_tokens={} (provider={}, model={})",
                max_tokens,
                self.name,
                self.model,
            )
