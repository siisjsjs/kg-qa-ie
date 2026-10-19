"""OpenAI 兼容 Embedding provider 单元测试。

覆盖:
  - 单批次 embed 正常解析 / 多 batch 自动切片 / 空 list 走捷径
  - test_connection ok 路径返回 dim
  - HTTP 4xx 抛 ProviderError
  - api_key / base_url 缺失分别报错
  - 实际响应 dim ≠ 配置 dim 时自动回填
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.exceptions import ProviderError
from app.services.embedding.openai_compat import (
    DEFAULT_BATCH_SIZE,
    OpenAICompatEmbeddingProvider,
    _normalize_base_url,
)


def _embed_response(vectors: list[list[float]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "object": "list",
            "model": "BAAI/bge-m3",
            "data": [
                {"object": "embedding", "index": i, "embedding": v}
                for i, v in enumerate(vectors)
            ],
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        },
    )


def _patch_transport(monkeypatch, handler) -> list[httpx.Request]:
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)
    real_cls = httpx.AsyncClient

    class _Patched(real_cls):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Patched)
    return captured


def test_normalize_base_url_strips_v1():
    assert _normalize_base_url("https://api.siliconflow.cn/v1") == "https://api.siliconflow.cn"
    assert _normalize_base_url("https://api.siliconflow.cn") == "https://api.siliconflow.cn"
    with pytest.raises(ProviderError):
        _normalize_base_url(None)


@pytest.mark.asyncio
async def test_embed_empty_list_returns_empty(monkeypatch):
    captured = _patch_transport(monkeypatch, lambda req: _embed_response([[0.1, 0.2]]))
    p = OpenAICompatEmbeddingProvider(
        api_key="sk-x",
        base_url="https://api.siliconflow.cn/v1",
        model="BAAI/bge-m3",
        dim=1024,
    )
    out = await p.embed([])
    assert out == []
    assert captured == []  # 不应发请求


@pytest.mark.asyncio
async def test_embed_single_batch(monkeypatch):
    captured = _patch_transport(
        monkeypatch,
        lambda req: _embed_response([[0.1] * 1024, [0.2] * 1024]),
    )
    p = OpenAICompatEmbeddingProvider(
        api_key="sk-x", base_url="https://api.siliconflow.cn", model="BAAI/bge-m3", dim=1024
    )
    out = await p.embed(["a", "b"])
    assert len(out) == 2
    assert len(out[0]) == 1024
    assert len(captured) == 1
    req = captured[0]
    assert str(req.url) == "https://api.siliconflow.cn/v1/embeddings"
    assert req.headers["authorization"] == "Bearer sk-x"
    body = json.loads(req.content)
    assert body["model"] == "BAAI/bge-m3"
    assert body["input"] == ["a", "b"]


@pytest.mark.asyncio
async def test_embed_splits_into_multiple_batches(monkeypatch):
    """超过 DEFAULT_BATCH_SIZE 自动切片。"""

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        n = len(body["input"])
        return _embed_response([[float(i)] * 4 for i in range(n)])

    captured = _patch_transport(monkeypatch, handler)
    p = OpenAICompatEmbeddingProvider(
        api_key="sk-x",
        base_url="https://x.example.com",
        model="m",
        dim=4,
    )
    texts = [f"t{i}" for i in range(DEFAULT_BATCH_SIZE * 2 + 5)]  # 69 条
    out = await p.embed(texts)
    assert len(out) == len(texts)
    assert len(captured) == 3  # 32 + 32 + 5
    sizes = [len(json.loads(r.content)["input"]) for r in captured]
    assert sizes == [DEFAULT_BATCH_SIZE, DEFAULT_BATCH_SIZE, 5]


@pytest.mark.asyncio
async def test_embed_dim_autofill(monkeypatch):
    """实际响应 dim ≠ 初始配置时,自动回填。"""
    _patch_transport(monkeypatch, lambda req: _embed_response([[0.0] * 768]))
    p = OpenAICompatEmbeddingProvider(
        api_key="sk-x", base_url="https://x.example.com", model="m", dim=1024
    )
    await p.embed(["one"])
    assert p.dim == 768


@pytest.mark.asyncio
async def test_test_connection_ok(monkeypatch):
    _patch_transport(monkeypatch, lambda req: _embed_response([[0.1] * 1024]))
    p = OpenAICompatEmbeddingProvider(
        api_key="sk-x", base_url="https://x.example.com", model="BAAI/bge-m3", dim=1024
    )
    ok, info = await p.test_connection()
    assert ok is True
    assert "dim=1024" in info
    assert "BAAI/bge-m3" in info


@pytest.mark.asyncio
async def test_test_connection_no_api_key():
    p = OpenAICompatEmbeddingProvider(
        api_key=None, base_url="https://x.example.com", model="m", dim=1024
    )
    ok, info = await p.test_connection()
    assert ok is False
    assert "API key" in info


@pytest.mark.asyncio
async def test_test_connection_no_base_url():
    p = OpenAICompatEmbeddingProvider(api_key="sk-x", base_url=None, model="m", dim=1024)
    ok, info = await p.test_connection()
    assert ok is False
    assert "base_url" in info


@pytest.mark.asyncio
async def test_embed_4xx_raises(monkeypatch):
    _patch_transport(
        monkeypatch,
        lambda req: httpx.Response(429, json={"error": {"message": "rate limit"}}),
    )
    p = OpenAICompatEmbeddingProvider(
        api_key="sk-x", base_url="https://x.example.com", model="m", dim=4
    )
    with pytest.raises(ProviderError) as e:
        await p.embed(["x"])
    assert "429" in str(e.value)


@pytest.mark.asyncio
async def test_embed_no_api_key_raises(monkeypatch):
    _patch_transport(monkeypatch, lambda req: _embed_response([[0.0] * 4]))
    p = OpenAICompatEmbeddingProvider(
        api_key=None, base_url="https://x.example.com", model="m", dim=4
    )
    with pytest.raises(ProviderError):
        await p.embed(["x"])
