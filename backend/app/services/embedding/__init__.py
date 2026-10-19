"""Embedding 多 provider 抽象层 + 缓存 + 向量库。"""

from app.services.embedding.base import EmbeddingProvider
from app.services.embedding.cache import EmbeddingCache
from app.services.embedding.factory import (
    get_embedding_config,
    get_embedding_provider,
    invalidate_embedding_cache,
    list_providers,
)
from app.services.embedding.vector_store import WikiVectorStore

__all__ = [
    "EmbeddingCache",
    "EmbeddingProvider",
    "WikiVectorStore",
    "get_embedding_config",
    "get_embedding_provider",
    "invalidate_embedding_cache",
    "list_providers",
]
