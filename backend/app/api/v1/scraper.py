"""scraper:firecrawl 抓取 + LLM 整合为 IE 标准 md。

流程:
  1. firecrawl /v1/scrape 拉原始 markdown
  2. LLM 抽取工业工程相关内容,自起标题,套上标准 frontmatter
  3. 直接返回整合后的 md,前端 Blob 下载

不入库、不写盘。
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AdminUserId
from app.core.exceptions import ProviderError
from app.schemas.scraper import ScrapeRequest, ScrapeResponse
from app.services.scraper import scrape_url
from app.services.scraper.distill import IrrelevantContentError, distill_scraped

router = APIRouter(prefix="/scraper")


@router.post("/scrape", response_model=ScrapeResponse)
async def scrape(payload: ScrapeRequest, user_id: AdminUserId) -> ScrapeResponse:  # noqa: ARG001
    """单 URL → firecrawl 抓取 → LLM 整合为 IE 标准 md。"""
    url = str(payload.url)
    logger.info(f"[scraper] start url={url}")

    # Step 1: firecrawl 抓原始
    t0 = time.perf_counter()
    try:
        raw = await scrape_url(url)
    except ProviderError as e:
        logger.warning(f"[scraper] firecrawl failed: {e}")
        raise HTTPException(status_code=502, detail=f"firecrawl 抓取失败:{e}") from e
    t_firecrawl = time.perf_counter() - t0
    logger.info(
        f"[scraper] firecrawl done in {t_firecrawl:.1f}s, "
        f"raw_chars={len(raw['markdown'])}, raw_title={raw['title']!r}"
    )

    # Step 2: LLM 整合
    t1 = time.perf_counter()
    try:
        final_md, final_title = await distill_scraped(
            raw_markdown=raw["markdown"],
            source_url=raw["source_url"],
            raw_title=raw["title"],
            scraped_at=raw["scraped_at"],
        )
    except IrrelevantContentError as e:
        logger.warning(f"[scraper] LLM judged irrelevant: {e}")
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ProviderError as e:
        logger.warning(f"[scraper] LLM distill failed: {e}")
        raise HTTPException(status_code=502, detail=f"LLM 整合失败:{e}") from e
    t_llm = time.perf_counter() - t1
    logger.info(
        f"[scraper] distill done in {t_llm:.1f}s, "
        f"final_chars={len(final_md)}, final_title={final_title!r}"
    )

    return ScrapeResponse(
        markdown=final_md,
        title=final_title,
        source_url=raw["source_url"],
        scraped_at=raw["scraped_at"],
        metadata={
            **raw.get("metadata", {}),
            "raw_chars": len(raw["markdown"]),
            "final_chars": len(final_md),
            "raw_title": raw["title"],
            "firecrawl_seconds": round(t_firecrawl, 1),
            "distill_seconds": round(t_llm, 1),
        },
    )
