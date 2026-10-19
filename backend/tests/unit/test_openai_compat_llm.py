"""OpenAI 兼容 LLM provider 单元测试。

通过 httpx.MockTransport 拦截对 /v1/chat/completions 的请求,验证:
  - base_url 末尾 /v1 与无 /v1 都能正确路由
  - chat(stream=False) 解析 choices[0].message.content
  - chat(stream=True) 按 SSE 行解析 delta.content,遇 [DONE] 终止
  - test_connection 返回 (True, sample)
  - HTTP 4xx/5xx 抛 ProviderError
  - api_key 缺失抛 ProviderError("API key not configured")
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from app.core.exceptions import ProviderError
from app.services.llm.base import ChatMessage
from app.services.llm.openai_compat import (
    OpenAICompatLLMProvider,
    _normalize_base_url,
)


def _ok_response(content: str = "pong") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-x",
            "object": "chat.completion",
            "model": "test-model",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content}}
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        },
    )


def _stream_body(deltas: list[str]) -> bytes:
    """构造一段 SSE body,模拟 OpenAI 流式响应。"""
    lines: list[str] = []
    for d in deltas:
        chunk = {
            "choices": [
                {"index": 0, "delta": {"content": d}, "finish_reason": None}
            ]
        }
        lines.append(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode("utf-8")


def _stream_response(deltas: list[str]) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=_stream_body(deltas),
    )


def _patch_transport(monkeypatch, handler) -> list[httpx.Request]:
    """把 httpx.AsyncClient 替换成走 MockTransport 的版本,捕获 request 列表。"""
    captured: list[httpx.Request] = []

    def _wrapped_handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped_handler)
    real_client_cls = httpx.AsyncClient

    class _PatchedClient(real_client_cls):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedClient)
    return captured


def test_normalize_base_url_strips_v1_and_trailing_slash():
    assert _normalize_base_url("https://api.example.com/v1/") == "https://api.example.com"
    assert _normalize_base_url("https://api.example.com/v1") == "https://api.example.com"
    assert _normalize_base_url("https://api.example.com/") == "https://api.example.com"
    assert _normalize_base_url(None) == "https://api.openai.com"


@pytest.mark.asyncio
async def test_chat_non_stream_parses_content(monkeypatch):
    captured = _patch_transport(monkeypatch, lambda req: _ok_response("hello"))
    p = OpenAICompatLLMProvider(
        api_key="sk-x",
        base_url="https://api.siliconflow.cn/v1",
        model="Qwen/Qwen2.5-7B-Instruct",
    )

    out = await p.chat([ChatMessage(role="user", content="hi")])
    assert out == "hello"
    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert str(req.url) == "https://api.siliconflow.cn/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-x"
    body = json.loads(req.content)
    assert body["model"] == "Qwen/Qwen2.5-7B-Instruct"
    assert body["stream"] is False
    assert body["messages"][0]["content"] == "hi"


@pytest.mark.asyncio
async def test_chat_stream_yields_tokens(monkeypatch):
    _patch_transport(monkeypatch, lambda req: _stream_response(["你", "好", "!"]))
    p = OpenAICompatLLMProvider(api_key="sk-x", base_url="https://api.example.com")

    iterator = await p.chat([ChatMessage(role="user", content="hi")], stream=True)
    assert isinstance(iterator, AsyncIterator)

    tokens = [t async for t in iterator]
    assert tokens == ["你", "好", "!"]


@pytest.mark.asyncio
async def test_chat_stream_skips_malformed_lines(monkeypatch):
    """非 data: 开头的行 / [DONE] 之外的非 JSON data 行 都应被忽略。"""

    def handler(req: httpx.Request) -> httpx.Response:
        body = (
            "ignored: heartbeat\n\n"
            'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
            "data: not-json\n\n"
            'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'
            "data: [DONE]\n\n"
        ).encode("utf-8")
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    _patch_transport(monkeypatch, handler)
    p = OpenAICompatLLMProvider(api_key="sk-x", base_url="https://x.example.com")
    iterator = await p.chat([ChatMessage(role="user", content="hi")], stream=True)
    tokens = [t async for t in iterator]
    assert tokens == ["a", "b"]


@pytest.mark.asyncio
async def test_test_connection_ok(monkeypatch):
    _patch_transport(monkeypatch, lambda req: _ok_response("pong"))
    p = OpenAICompatLLMProvider(api_key="sk-x", base_url="https://x.example.com")
    ok, info = await p.test_connection()
    assert ok is True
    assert info == "pong"


@pytest.mark.asyncio
async def test_test_connection_no_api_key():
    p = OpenAICompatLLMProvider(api_key=None, base_url="https://x.example.com")
    ok, info = await p.test_connection()
    assert ok is False
    assert "API key" in info


@pytest.mark.asyncio
async def test_chat_4xx_raises_provider_error(monkeypatch):
    _patch_transport(
        monkeypatch,
        lambda req: httpx.Response(401, json={"error": {"message": "invalid api key"}}),
    )
    p = OpenAICompatLLMProvider(api_key="sk-bad", base_url="https://x.example.com")
    with pytest.raises(ProviderError) as e:
        await p.chat([ChatMessage(role="user", content="hi")])
    assert "401" in str(e.value)


@pytest.mark.asyncio
async def test_chat_no_api_key_raises(monkeypatch):
    p = OpenAICompatLLMProvider(api_key=None, base_url="https://x.example.com")
    with pytest.raises(ProviderError):
        await p.chat([ChatMessage(role="user", content="hi")])


@pytest.mark.asyncio
async def test_extra_params_merged(monkeypatch):
    captured = _patch_transport(monkeypatch, lambda req: _ok_response())
    p = OpenAICompatLLMProvider(
        api_key="sk-x",
        base_url="https://x.example.com",
        model="m",
        extra_params={"top_p": 0.7, "frequency_penalty": 0.1, "stream": "ignored"},
    )
    await p.chat([ChatMessage(role="user", content="hi")])
    body = json.loads(captured[0].content)
    assert body["top_p"] == 0.7
    assert body["frequency_penalty"] == 0.1
    # extra_params 中的 stream 必须被忽略,以构造期入参为准
    assert body["stream"] is False
