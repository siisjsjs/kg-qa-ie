"""Embedding 结果缓存（SQLite + LRU）。

复用旧项目 embedding_service.py 思路：避免重复编码，按 sha1(text + model + provider) 缓存。
"""


class EmbeddingCache:
    def __init__(self, cache_path: str, capacity: int = 10000):
        self.cache_path = cache_path
        self.capacity = capacity
        # TODO: 初始化 SQLite + 内存 LRU

    async def get(self, text: str, *, provider: str, model: str) -> list[float] | None:
        raise NotImplementedError("EmbeddingCache.get 待实现")

    async def put(self, text: str, vector: list[float], *, provider: str, model: str) -> None:
        raise NotImplementedError("EmbeddingCache.put 待实现")

    async def get_batch(
        self, texts: list[str], *, provider: str, model: str
    ) -> tuple[list[list[float] | None], list[int]]:
        """返回 (cached_vectors, missing_indices)；missing_indices 由调用方编码后回填。"""
        raise NotImplementedError("EmbeddingCache.get_batch 待实现")
