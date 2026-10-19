"""query：SSE 流式查询请求 / 评分。"""

from typing import Literal

from pydantic import BaseModel, Field


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: int | None = None  # 必传(POST /query 会强制),除非走老的"无状态"模式
    persist: bool | None = None  # None = 自动判断；true/false = 显式
    domain_ie: bool | None = None  # 覆盖 .wiki-schema.md 默认
    history: list[HistoryMessage] = Field(default_factory=list)  # P6+ 由后端从 DB 装载,前端可不传
    summary: str | None = None  # 同上


class Citation(BaseModel):
    slug: str
    title: str
    kind: str | None = None
    confidence: Literal["EXTRACTED", "INFERRED", "AMBIGUOUS"] | None = None
    # chunk 级引用协议扩展字段(改造计划 §6.3)
    type: Literal["chunk", "page", "web"] | None = None
    ref: str | None = None  # E3 / W1
    heading: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    quote: str | None = None
    url: str | None = None


class AnswerQuality(BaseModel):
    score: float
    tier: Literal["gold", "silver", "bronze", "unknown"]
    cited_sources: int
    confidence_mix: dict[str, int] = Field(default_factory=dict)
    alias_miss_count: int = 0
    broken_links: int = 0
    isolated_hits: int = 0
    gap_flag: bool = False
    reasons: list[str] = Field(default_factory=list)


class QueryFinalResponse(BaseModel):
    answer: str
    citations: list[Citation]
    quality: AnswerQuality
    persisted_query_slug: str | None = None
    path_payload: dict = Field(default_factory=dict)
