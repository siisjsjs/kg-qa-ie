"""Anthropic Claude LLM provider。"""

from collections.abc import AsyncIterator

from app.services.llm.base import ChatMessage, LLMProvider


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "claude-sonnet-4-6",
        temperature: float = 0.3,
        max_tokens: int = 8192,
        extra_params: dict | None = None,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
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
        """TODO: anthropic SDK 调用，注意 system 消息单独走 system 参数。"""
        raise NotImplementedError("AnthropicProvider.chat 待实现")

    async def test_connection(self) -> tuple[bool, str]:
        raise NotImplementedError("AnthropicProvider.test_connection 待实现")
