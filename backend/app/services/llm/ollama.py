"""Ollama LLM provider（本地：qwen2.5 / llama / deepseek-r1 等）。"""

from collections.abc import AsyncIterator

from app.services.llm.base import ChatMessage, LLMProvider


class OllamaLLMProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "qwen2.5:14b",
        temperature: float = 0.3,
        max_tokens: int = 8192,
        extra_params: dict | None = None,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url or "http://localhost:11434",
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_params=extra_params,
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        stream: bool = False,
        **params,
    ) -> str | AsyncIterator[str]:
        """TODO: Ollama /api/chat（注意：不是 /v1/chat/completions）。"""
        raise NotImplementedError("OllamaLLMProvider.chat 待实现")

    async def test_connection(self) -> tuple[bool, str]:
        raise NotImplementedError("OllamaLLMProvider.test_connection 待实现")
