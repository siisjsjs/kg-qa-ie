"""scraper：firecrawl 单 URL 抓取 + API 配置。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


# ===== firecrawl 配置 =====
class FirecrawlConfigView(BaseModel):
    """读视图：api_key 已屏蔽。"""

    api_key: str = ""
    base_url: str = ""


class FirecrawlConfigRequest(BaseModel):
    api_key: str | None = None  # 空串/含 * 视为"不修改"
    base_url: str | None = None


# ===== scrape =====
class ScrapeRequest(BaseModel):
    url: HttpUrl


class ScrapeResponse(BaseModel):
    markdown: str
    title: str
    source_url: str
    scraped_at: datetime
    metadata: dict = Field(default_factory=dict)
