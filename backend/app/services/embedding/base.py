"""Embedding provider 抽象基类。"""

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    name: str = "base"
    dim: int = 1024

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str,
        dim: int = 1024,
        extra_params: dict | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.dim = dim
        self.extra_params = extra_params or {}

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """对 texts 批量编码。返回 (N, dim) 浮点矩阵。"""

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        """连通性测试：返回 (ok, info_message)。"""
