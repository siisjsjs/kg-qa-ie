"""Embedding provider 工厂。

优先级：config_store > .env > 默认。
"""

from __future__ import annotations

from threading import RLock

from app.core.config import settings
from app.services.admin import config_store
from app.services.embedding.base import EmbeddingProvider
from app.services.embedding.openai_compat import OpenAICompatEmbeddingProvider

_PROVIDER_REGISTRY: dict[str, type[EmbeddingProvider]] = {
    "openai_compat": OpenAICompatEmbeddingProvider,
}

_CONFIG_KEYS = [
    "embedding.provider",
    "embedding.api_key",
    "embedding.base_url",
    "embedding.model",
    "embedding.dim",
]

_lock = RLock()
_cached: EmbeddingProvider | None = None
_cached_signature: tuple | None = None


def list_providers() -> list[str]:
    return list(_PROVIDER_REGISTRY.keys())


def _read_runtime_config() -> dict:
    stored = config_store.get_many_sync(_CONFIG_KEYS, mask=False)

    def _pick(key: str, fallback) -> str:
        v = stored.get(key, "")
        return v if v != "" else fallback

    return {
        "provider": _pick("embedding.provider", settings.embedding_provider),
        "api_key": _pick("embedding.api_key", settings.embedding_api_key),
        "base_url": _pick("embedding.base_url", settings.embedding_base_url),
        "model": _pick("embedding.model", settings.embedding_model),
        "dim": int(_pick("embedding.dim", str(settings.embedding_dim)) or settings.embedding_dim),
    }


def get_embedding_config() -> dict:
    return _read_runtime_config()


def get_embedding_provider() -> EmbeddingProvider:
    global _cached, _cached_signature
    cfg = _read_runtime_config()
    sig = tuple(cfg.items())
    with _lock:
        if _cached is None or _cached_signature != sig:
            cls = _PROVIDER_REGISTRY.get(cfg["provider"])
            if cls is None:
                raise ValueError(f"unknown embedding provider: {cfg['provider']}")
            _cached = cls(
                api_key=cfg["api_key"] or None,
                base_url=cfg["base_url"] or None,
                model=cfg["model"],
                dim=cfg["dim"],
            )
            _cached_signature = sig
        return _cached


def invalidate_embedding_cache() -> None:
    global _cached, _cached_signature
    with _lock:
        _cached = None
        _cached_signature = None
