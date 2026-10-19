"""LLM provider 工厂。

优先级：config_store（DB / dev JSON）> .env > 默认。
保存配置后通过 invalidate_llm_cache() 让下次调用重建实例。
"""

from __future__ import annotations

import json
from threading import RLock

from loguru import logger

from app.core.config import settings
from app.services.admin import config_store
from app.services.llm.anthropic import AnthropicProvider
from app.services.llm.base import LLMProvider
from app.services.llm.deepseek import DeepSeekProvider
from app.services.llm.ollama import OllamaLLMProvider
from app.services.llm.openai_compat import OpenAICompatLLMProvider

_PROVIDER_REGISTRY: dict[str, type[LLMProvider]] = {
    "deepseek": DeepSeekProvider,
    "openai_compat": OpenAICompatLLMProvider,
    "ollama": OllamaLLMProvider,
    "anthropic": AnthropicProvider,
}

_CONFIG_KEYS = [
    "llm.provider",
    "llm.api_key",
    "llm.base_url",
    "llm.model",
    "llm.temperature",
    "llm.max_tokens",
    "llm.extra_params",
]

# 推理模型默认把 reasoning 和正文塞进同一个 max_tokens 预算,且推理量随预算膨胀
# (24576 预算实测推理 14588~18572 tokens,首字要等 138~177s)。知识库问答的推理
# 材料已经在 context 里给全了,关掉 reasoning 实测首字 0.5s、正文反而更长。
_DEFAULT_EXTRA_PARAMS: dict[str, dict] = {
    "openai_compat": {"reasoning_effort": "none"},
    "deepseek": {"reasoning_effort": "none"},
}

_lock = RLock()
_cached: LLMProvider | None = None
_cached_signature: tuple | None = None


def list_providers() -> list[str]:
    return list(_PROVIDER_REGISTRY.keys())


def _read_runtime_config() -> dict:
    """config_store > settings 兜底。"""
    stored = config_store.get_many_sync(_CONFIG_KEYS, mask=False)

    def _pick(key: str, fallback):
        v = stored.get(key, "")
        return v if v != "" else fallback

    provider = _pick("llm.provider", settings.llm_provider)
    return {
        "provider": provider,
        "api_key": _pick("llm.api_key", settings.llm_api_key),
        "base_url": _pick("llm.base_url", settings.llm_base_url),
        "model": _pick("llm.model", settings.llm_model),
        "temperature": float(_pick("llm.temperature", settings.llm_temperature)),
        "max_tokens": int(_pick("llm.max_tokens", settings.llm_max_tokens)),
        "extra_params": _parse_extra_params(stored.get("llm.extra_params", ""), provider),
    }


def _parse_extra_params(raw: str, provider: str) -> dict:
    """llm.extra_params 是一段 JSON,原样并进请求体;留空则用 provider 默认。"""
    text = (raw or "").strip()
    if not text:
        return dict(_DEFAULT_EXTRA_PARAMS.get(provider, {}))
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("llm.extra_params 不是合法 JSON,已忽略: {}", text[:120])
        return dict(_DEFAULT_EXTRA_PARAMS.get(provider, {}))
    if not isinstance(parsed, dict):
        logger.warning("llm.extra_params 必须是 JSON 对象,已忽略")
        return dict(_DEFAULT_EXTRA_PARAMS.get(provider, {}))
    return parsed


def get_llm_config() -> dict:
    """对外暴露当前生效的 LLM 配置（含敏感字段；调用方决定是否屏蔽）。"""
    return _read_runtime_config()


def get_llm_provider() -> LLMProvider:
    """获取当前活跃 LLM provider 实例（带签名缓存）。"""
    global _cached, _cached_signature
    cfg = _read_runtime_config()
    sig = tuple(cfg.items())
    with _lock:
        if _cached is None or _cached_signature != sig:
            cls = _PROVIDER_REGISTRY.get(cfg["provider"])
            if cls is None:
                raise ValueError(f"unknown llm provider: {cfg['provider']}")
            _cached = cls(
                api_key=cfg["api_key"] or None,
                base_url=cfg["base_url"] or None,
                model=cfg["model"],
                temperature=cfg["temperature"],
                max_tokens=cfg["max_tokens"],
                extra_params=cfg["extra_params"],
            )
            _cached_signature = sig
        return _cached


def invalidate_llm_cache() -> None:
    """配置变更后调用以使下次 get_llm_provider 重建实例。"""
    global _cached, _cached_signature
    with _lock:
        _cached = None
        _cached_signature = None
