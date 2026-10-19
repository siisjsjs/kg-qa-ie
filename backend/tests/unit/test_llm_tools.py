"""LLM function calling 层单元测试（改造计划 Phase 1）。

覆盖:
  - parse_tools_sse: 纯文本回答不产生 tool_calls 事件
  - parse_tools_sse: 多工具并行调用的 arguments 增量拼装
  - ToolCallAccumulator: 容忍空片段、缺 index、乱序 index
  - messages_to_payload: assistant tool_calls / tool 消息序列化为 OpenAI 格式
  - DeepSeekProvider.chat_tools: payload 带 tools/tool_choice,事件序列正确
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from app.services.llm.base import (
    ChatMessage,
    ToolCall,
    ToolCallAccumulator,
    messages_to_payload,
    parse_tools_sse,
)
from app.services.llm.deepseek import DeepSeekProvider


async def _lines(items: list[str]) -> AsyncIterator[str]:
    for it in items:
        yield it


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}"


def _delta_chunk(delta: dict, finish_reason: str | None = None) -> str:
    return _sse({"choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]})


@pytest.mark.asyncio
async def test_parse_pure_text_no_tool_calls():
    lines = [
        _delta_chunk({"content": "你"}),
        _delta_chunk({"content": "好"}),
        _delta_chunk({}, finish_reason="stop"),
        "data: [DONE]",
    ]
    events = [e async for e in parse_tools_sse(_lines(lines))]
    assert [e["type"] for e in events] == ["token", "token", "finish"]
    assert events[0]["text"] == "你"
    assert events[-1]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_parse_multi_tool_incremental_arguments():
    """两个工具并行调用,arguments 分片到达,应按 index 各自拼装完整。"""
    lines = [
        _delta_chunk({"content": "我先查一下。"}),
        _delta_chunk({"tool_calls": [
            {"index": 0, "id": "call_a", "function": {"name": "search_kb", "arguments": ""}},
        ]}),
        _delta_chunk({"tool_calls": [
            {"index": 0, "function": {"arguments": '{"query": "帕累'}},
            {"index": 1, "id": "call_b", "function": {"name": "web_search", "arguments": '{"que'}},
        ]}),
        _delta_chunk({"tool_calls": [
            {"index": 0, "function": {"arguments": '托图"}'}},
            {"index": 1, "function": {"arguments": 'ry": "2025 trends"}'}},
        ]}),
        _delta_chunk({}, finish_reason="tool_calls"),
        "data: [DONE]",
    ]
    events = [e async for e in parse_tools_sse(_lines(lines))]
    assert [e["type"] for e in events] == ["token", "tool_call_begin", "tool_calls", "finish"]
    calls = events[2]["calls"]
    assert len(calls) == 2
    assert calls[0] == ToolCall(id="call_a", name="search_kb", arguments='{"query": "帕累托图"}')
    assert calls[1] == ToolCall(id="call_b", name="web_search", arguments='{"query": "2025 trends"}')
    assert json.loads(calls[0].arguments) == {"query": "帕累托图"}
    assert events[3]["finish_reason"] == "tool_calls"


def test_accumulator_tolerates_bad_fragments():
    """空片段 / 缺 index / 非 dict 片段 / 乱序 index 不应炸,也不应产生残废调用。"""
    acc = ToolCallAccumulator()
    acc.feed(None)
    acc.feed([])
    acc.feed(["not-a-dict", {}])
    # 乱序: 先来 index=1
    acc.feed([{"index": 1, "id": "call_b", "function": {"name": "web_search"}}])
    acc.feed([{"index": 0, "id": "call_a", "function": {"name": "search_kb", "arguments": "{}"}}])
    # 缺 index 的片段归入最近一次的 index(0)
    acc.feed([{"function": {"arguments": ""}}])
    acc.feed([{"index": 1, "function": {"arguments": '{"query":"x"}'}}])
    calls = acc.build()
    assert [c.name for c in calls] == ["search_kb", "web_search"]
    assert calls[1].arguments == '{"query":"x"}'
    # 无 arguments 的调用兜底为 "{}"
    acc2 = ToolCallAccumulator()
    acc2.feed([{"index": 0, "id": "c", "function": {"name": "f"}}])
    assert acc2.build()[0].arguments == "{}"


def test_accumulator_nameless_fragment_dropped():
    acc = ToolCallAccumulator()
    acc.feed([{"index": 0, "id": "call_x", "function": {"arguments": "{}"}}])
    assert acc.build() == []


def test_messages_to_payload_tool_roundtrip():
    msgs = [
        ChatMessage(role="system", content="sys"),
        ChatMessage(role="user", content="问题"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="call_1", name="search_kb", arguments='{"query":"q"}')],
        ),
        ChatMessage(role="tool", content='{"results":[]}', tool_call_id="call_1"),
    ]
    payload = messages_to_payload(msgs)
    assert payload[0] == {"role": "system", "content": "sys"}
    assert payload[1] == {"role": "user", "content": "问题"}
    assert payload[2] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search_kb", "arguments": '{"query":"q"}'},
            }
        ],
    }
    assert payload[3] == {"role": "tool", "content": '{"results":[]}', "tool_call_id": "call_1"}


def _patch_transport(monkeypatch, handler) -> list[httpx.Request]:
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)
    real_client_cls = httpx.AsyncClient

    class _PatchedClient(real_client_cls):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedClient)
    return captured


_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_kb",
            "description": "检索知识库",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
]


@pytest.mark.asyncio
async def test_chat_tools_payload_and_events(monkeypatch):
    body = (
        'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"c1",'
        '"function":{"name":"search_kb","arguments":"{\\"query\\":\\"q\\"}"}}]},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],'
        '"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n'
        "data: [DONE]\n\n"
    ).encode("utf-8")
    captured = _patch_transport(
        monkeypatch,
        lambda req: httpx.Response(
            200, content=body, headers={"content-type": "text/event-stream"}
        ),
    )
    p = DeepSeekProvider(api_key="sk-x", model="deepseek-chat")
    stats: dict = {}
    events = [
        e
        async for e in p.chat_tools(
            [ChatMessage(role="user", content="hi")], tools=_TOOLS, stats=stats
        )
    ]
    assert [e["type"] for e in events] == ["tool_call_begin", "tool_calls", "finish"]
    assert events[1]["calls"][0].name == "search_kb"
    assert stats["finish_reason"] == "tool_calls"
    assert stats["usage"]["total_tokens"] == 15

    req_body = json.loads(captured[0].content)
    assert req_body["tools"] == _TOOLS
    assert req_body["tool_choice"] == "auto"
    assert req_body["stream"] is True


@pytest.mark.asyncio
async def test_chat_tools_tool_choice_none(monkeypatch):
    body = (
        'data: {"choices":[{"index":0,"delta":{"content":"直答"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    ).encode("utf-8")
    captured = _patch_transport(
        monkeypatch,
        lambda req: httpx.Response(
            200, content=body, headers={"content-type": "text/event-stream"}
        ),
    )
    p = DeepSeekProvider(api_key="sk-x")
    events = [
        e
        async for e in p.chat_tools(
            [ChatMessage(role="user", content="hi")],
            tools=_TOOLS,
            tool_choice="none",
        )
    ]
    assert [e["type"] for e in events] == ["token", "finish"]
    assert json.loads(captured[0].content)["tool_choice"] == "none"
