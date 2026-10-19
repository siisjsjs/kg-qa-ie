"""DeepSeek LLM provider。

使用 OpenAI 兼容协议（POST /v1/chat/completions）。
非流式返 str；stream=True 返 AsyncIterator[str]（按 SSE 行解析 delta.content）。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.core.exceptions import ProviderError
from app.services.llm.base import (
    ChatMessage,
    LLMProvider,
    messages_to_payload,
    parse_tools_sse,
)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)


class DeepSeekProvider(LLMProvider):
    name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "deepseek-chat",
        temperature: float = 0.3,
        max_tokens: int = 8192,
        extra_params: dict | None = None,
    ):
        super().__init__(
            api_key=api_key,
            base_url=(base_url or DEFAULT_BASE_URL).rstrip("/"),
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_params=extra_params,
        )

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderError("DeepSeek API key not configured")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        messages: list[ChatMessage],
        *,
        stream: bool,
        params: dict,
    ) -> dict:
        # 调用方可通过 chat(**kwargs) 覆盖 extra_params 默认;None 表示移除该键
        # (如抽取流水线传 reasoning_effort=None,恢复模型自然推理)。
        reserved = {"model", "messages", "stream", "temperature", "max_tokens", "stats"}
        extra = {k: v for k, v in self.extra_params.items() if k not in {"model", "messages", "stream"}}
        for k, v in params.items():
            if k in reserved:
                continue
            if v is None:
                extra.pop(k, None)
            else:
                extra[k] = v
        payload = {
            "model": params.get("model", self.model),
            "messages": messages_to_payload(messages),
            "temperature": params.get("temperature", self.temperature),
            "stream": stream,
            **extra,
        }
        payload["max_tokens"] = self.resolve_max_tokens(params)
        return payload

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        stream: bool = False,
        **params,
    ) -> str | AsyncIterator[str]:
        if stream:
            return self._stream(messages, params)
        return await self._chat_once(messages, params)

    async def _chat_once(self, messages: list[ChatMessage], params: dict) -> str:
        payload = self._build_payload(messages, stream=False, params=params)
        url = f"{self.base_url}/v1/chat/completions"
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                resp = await client.post(url, headers=self._headers(), json=payload)
            except httpx.HTTPError as e:
                raise ProviderError(f"DeepSeek network error: {e}") from e
            if resp.status_code >= 400:
                raise ProviderError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"DeepSeek malformed response: {data}") from e

    async def _stream(
        self,
        messages: list[ChatMessage],
        params: dict,
    ) -> AsyncIterator[str]:
        stats = params.pop("stats", None)
        payload = self._build_payload(messages, stream=True, params=params)
        url = f"{self.base_url}/v1/chat/completions"
        finish_reason: str | None = None
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                async with client.stream("POST", url, headers=self._headers(), json=payload) as resp:
                    if resp.status_code >= 400:
                        text = (await resp.aread()).decode("utf-8", errors="replace")
                        raise ProviderError(f"DeepSeek HTTP {resp.status_code}: {text[:300]}")
                    async for line in resp.aiter_lines():
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
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield delta
            except httpx.HTTPError as e:
                raise ProviderError(f"DeepSeek stream error: {e}") from e
        self._record_finish(finish_reason, stats, payload["max_tokens"])

    async def chat_tools(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict],
        **params,
    ):
        """带 function calling 的流式对话,yield 结构化事件（见 base.parse_tools_sse）。"""
        stats = params.pop("stats", None)
        tool_choice = params.pop("tool_choice", "auto")
        payload = self._build_payload(messages, stream=True, params=params)
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
        url = f"{self.base_url}/v1/chat/completions"
        finish_reason: str | None = None
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                async with client.stream("POST", url, headers=self._headers(), json=payload) as resp:
                    if resp.status_code >= 400:
                        text = (await resp.aread()).decode("utf-8", errors="replace")
                        raise ProviderError(f"DeepSeek HTTP {resp.status_code}: {text[:300]}")
                    async for event in parse_tools_sse(resp.aiter_lines(), stats=stats):
                        if event["type"] == "finish":
                            finish_reason = event.get("finish_reason")
                        yield event
            except httpx.HTTPError as e:
                raise ProviderError(f"DeepSeek stream error: {e}") from e
        self._record_finish(finish_reason, stats, payload["max_tokens"])

    async def test_connection(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "API key not configured"
        try:
            sample = await self._chat_once(
                [
                    ChatMessage(role="system", content="You are a connection-test bot. Reply in one short sentence."),
                    ChatMessage(role="user", content="Reply 'pong' to confirm connectivity."),
                ],
                params={"max_tokens": 32, "temperature": 0.0},
            )
            return True, sample.strip() or "(empty response)"
        except ProviderError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"unexpected error: {e}"
