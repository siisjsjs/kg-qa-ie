"""OpenAI 兼容 embedding provider。

覆盖范围:走 `POST {base}/v1/embeddings` 协议的服务 ——
  - 硅基流动:BAAI/bge-m3、BAAI/bge-large-zh-v1.5、Pro/BAAI/bge-m3
  - 智谱:embedding-2、embedding-3
  - OpenAI 官方:text-embedding-3-small / large、text-embedding-ada-002
  - 自托管 vLLM / Infinity / OneAPI

设计要点:
  - 批量 ≤32 条一批(硅基流动文档建议),内部自动切片
  - 维度 dim 优先从配置取,空响应或缺省时从首批响应回填(惰性自检)
  - 失败重试由调用方负责;本层只暴露 ProviderError
"""

from __future__ import annotations

import httpx

from app.core.exceptions import ProviderError
from app.services.embedding.base import EmbeddingProvider

DEFAULT_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=20.0)
DEFAULT_BATCH_SIZE = 32


def _normalize_base_url(base_url: str | None) -> str:
    """与 LLM 端一致:支持带或不带 /v1。"""
    if not base_url:
        raise ProviderError("Embedding base_url not configured")
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url


class OpenAICompatEmbeddingProvider(EmbeddingProvider):
    name = "openai_compat"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "BAAI/bge-m3",
        dim: int = 1024,
        extra_params: dict | None = None,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            dim=dim,
            extra_params=extra_params,
        )
        # base_url 校验推迟到首次调用,避免实例化时直接报错
        self._normalized_base: str | None = None
        # 进程内 embedding 缓存:key=文本,value=向量。
        # 同一篇文档入库时 merger 会对每个新实体重复 embed 全部现有实体 brief
        # (O(新实体 × 现有页)),这些 brief 文本在一次入库内完全相同,远程
        # 重复请求是 post_process 阶段的主要耗时来源。embedding 对同一输入
        # 是确定性的,缓存命中返回的向量与远程一致,合并判定逐字节不变。
        # provider 实例由 factory 按配置签名做单例,缓存可跨调用/跨文档存活。
        self._embed_cache: dict[str, list[float]] = {}

    def _get_base(self) -> str:
        if self._normalized_base is None:
            self._normalized_base = _normalize_base_url(self.base_url)
        return self._normalized_base

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderError("OpenAI-compatible embedding API key not configured")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _embed_batch(
        self, client: httpx.AsyncClient, texts: list[str]
    ) -> list[list[float]]:
        url = f"{self._get_base()}/v1/embeddings"
        payload = {
            "model": self.model,
            "input": texts,
            **{k: v for k, v in self.extra_params.items() if k not in {"model", "input"}},
        }
        try:
            resp = await client.post(url, headers=self._headers(), json=payload)
        except httpx.HTTPError as e:
            raise ProviderError(f"OpenAI-compat embedding network error: {e}") from e
        if resp.status_code >= 400:
            raise ProviderError(
                f"OpenAI-compat embedding HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        try:
            items = data["data"]
            vectors = [item["embedding"] for item in items]
        except (KeyError, TypeError) as e:
            raise ProviderError(f"OpenAI-compat embedding malformed response: {data}") from e
        if vectors and self.dim != len(vectors[0]):
            # 首次调用时回填实际维度,避免下游 schema 不一致
            self.dim = len(vectors[0])
        return vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # 先查进程内缓存,只对未命中的去重文本发远程请求,再按原始顺序回填。
        # 返回的向量对每个输入文本与"全量请求"完全一致 → 上游合并判定不变。
        result: list[list[float] | None] = [self._embed_cache.get(t) for t in texts]
        missing_unique: list[str] = []
        seen: set[str] = set()
        for t, v in zip(texts, result, strict=True):
            if v is None and t not in seen:
                seen.add(t)
                missing_unique.append(t)

        if missing_unique:
            fetched: list[list[float]] = []
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                for i in range(0, len(missing_unique), DEFAULT_BATCH_SIZE):
                    batch = missing_unique[i : i + DEFAULT_BATCH_SIZE]
                    fetched.extend(await self._embed_batch(client, batch))
            for t, vec in zip(missing_unique, fetched, strict=True):
                self._embed_cache[t] = vec

        return [v if v is not None else self._embed_cache[t]
                for t, v in zip(texts, result, strict=True)]

    async def test_connection(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "API key not configured"
        if not self.base_url:
            return False, "base_url not configured"
        try:
            vectors = await self.embed(["ping"])
        except ProviderError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"unexpected error: {e}"
        if not vectors:
            return False, "empty embedding"
        return True, f"dim={len(vectors[0])} model={self.model}"
