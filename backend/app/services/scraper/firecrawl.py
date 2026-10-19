"""firecrawl scrape:把单个 URL 转成 markdown。

直调 firecrawl REST API(/v1/scrape),不引 MCP / SDK。配置从 system_config 读
(firecrawl.api_key / firecrawl.base_url),与 LLM/Embedding 一致。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from app.core.exceptions import ProviderError
from app.services.admin import config_store

DEFAULT_BASE_URL = "https://api.firecrawl.dev"
# scrape 一般 5-30s 完成;给 120s 余量,失败重试 1 次
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
_RETRYABLE_EXC = (httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.PoolTimeout)
_MAX_RETRIES = 1


def _normalize_base_url(base_url: str | None) -> str:
    url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    # firecrawl 路径里带 /v1,允许用户填或不填;统一裁掉再拼
    if url.endswith("/v1"):
        url = url[:-3]
    return url


def _read_config() -> tuple[str, str]:
    """返回 (api_key, base_url)。"""
    api_key = config_store.get_sync("firecrawl.api_key", default="") or ""
    base_url = config_store.get_sync("firecrawl.base_url", default="") or ""
    return api_key, _normalize_base_url(base_url)


async def scrape_url(url: str) -> dict[str, Any]:
    """调 firecrawl /v1/scrape 抓取单个 URL,返回标准化 dict。

    Returns:
        {
            "markdown": str,
            "title": str,
            "source_url": str,         # firecrawl 解析后的最终 URL(经过 redirect)
            "scraped_at": datetime,
            "metadata": dict,           # firecrawl 原始 metadata(language/description/...)
        }

    Raises:
        ProviderError:key 未配置 / 网络错误 / firecrawl 4xx 5xx / 响应格式异常
    """
    api_key, base_url = _read_config()
    if not api_key:
        raise ProviderError("firecrawl api_key not configured")

    endpoint = f"{base_url}/v1/scrape"
    payload = {
        "url": url,
        "formats": ["markdown"],
        "onlyMainContent": True,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_exc: httpx.HTTPError | None = None
    for attempt in range(_MAX_RETRIES + 1):
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                resp = await client.post(endpoint, headers=headers, json=payload)
            except _RETRYABLE_EXC as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    logger.warning(f"firecrawl scrape retry({type(e).__name__}): {e}")
                    continue
                raise ProviderError(
                    f"firecrawl network error after {attempt + 1} attempt(s): "
                    f"{type(e).__name__}: {e}"
                ) from e
            except httpx.HTTPError as e:
                raise ProviderError(f"firecrawl network error: {type(e).__name__}: {e}") from e

            if resp.status_code >= 400:
                raise ProviderError(
                    f"firecrawl HTTP {resp.status_code}: {resp.text[:300]}"
                )
            data = resp.json()
        break  # 成功

    # firecrawl v1 响应:{ "success": bool, "data": { "markdown": ..., "metadata": {...} } }
    if not data.get("success"):
        raise ProviderError(f"firecrawl returned success=false: {str(data)[:300]}")
    body = data.get("data") or {}
    md = body.get("markdown") or ""
    metadata = body.get("metadata") or {}
    if not md.strip():
        raise ProviderError("firecrawl returned empty markdown (页面可能反爬或无内容)")

    title = (
        metadata.get("title")
        or metadata.get("ogTitle")
        or metadata.get("dcTitle")
        or url
    )
    source_url = metadata.get("sourceURL") or metadata.get("url") or url

    return {
        "markdown": md,
        "title": str(title)[:300],
        "source_url": str(source_url),
        "scraped_at": datetime.now(timezone.utc),
        "metadata": metadata,
    }


async def search_web(query: str, *, limit: int = 5, lang: str = "zh") -> list[dict[str, Any]]:
    """调 firecrawl /v1/search 做网页搜索,返回候选 [{title, url, description}]。

    用于"只有概念名、没有 URL"的场景:先搜出候选 URL,再交给 scrape_url 抓正文。

    Raises:
        ProviderError:key 未配置 / 网络错误 / firecrawl 4xx 5xx
    """
    api_key, base_url = _read_config()
    if not api_key:
        raise ProviderError("firecrawl api_key not configured")

    endpoint = f"{base_url}/v1/search"
    payload: dict[str, Any] = {"query": query, "limit": max(1, min(limit, 10)), "lang": lang}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_exc: httpx.HTTPError | None = None
    data: dict[str, Any] = {}
    for attempt in range(_MAX_RETRIES + 1):
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                resp = await client.post(endpoint, headers=headers, json=payload)
            except _RETRYABLE_EXC as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    logger.warning(f"firecrawl search retry({type(e).__name__}): {e}")
                    continue
                raise ProviderError(
                    f"firecrawl search network error after {attempt + 1} attempt(s): "
                    f"{type(e).__name__}: {e}"
                ) from e
            except httpx.HTTPError as e:
                raise ProviderError(f"firecrawl search network error: {type(e).__name__}: {e}") from e

            if resp.status_code >= 400:
                raise ProviderError(f"firecrawl search HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
        break

    if not data.get("success"):
        raise ProviderError(f"firecrawl search success=false: {str(data)[:300]}")
    out: list[dict[str, Any]] = []
    for item in data.get("data") or []:
        url = item.get("url") or item.get("link")
        if not url:
            continue
        out.append({
            "title": str(item.get("title") or "")[:300],
            "url": str(url),
            "description": str(item.get("description") or "")[:500],
        })
    return out


async def test_connection() -> tuple[bool, str]:
    """轻量连通性测试:抓 example.com。

    成功返回 (True, "<title> 字数=N")。不消耗多少配额,但确实会消费一次。
    """
    api_key, _ = _read_config()
    if not api_key:
        return False, "firecrawl api_key not configured"
    try:
        result = await scrape_url("https://example.com")
        return True, f"{result['title']} (md {len(result['markdown'])}字)"
    except ProviderError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"unexpected error: {e}"


__all__ = ["scrape_url", "search_web", "test_connection"]
