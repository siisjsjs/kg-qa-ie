"""digest / synthesis schema。"""

from typing import Literal

from pydantic import BaseModel, Field


class DigestRequest(BaseModel):
    topic: str = Field(min_length=1)
    mode: Literal["auto", "deep_dive", "comparison", "timeline"] = "auto"
    objects: list[str] = Field(default_factory=list)  # comparison 时传对比对象
    domain_ie: bool | None = None


class DigestSection(BaseModel):
    heading: str
    content: str


class DigestResponse(BaseModel):
    slug: str
    mode: Literal["deep_dive", "comparison", "timeline"]
    sections: list[DigestSection]
    citations: list[str]
    quality_score: float
    gap_flag: bool
    sections_missing: list[str] = Field(default_factory=list)
    fill_rate: float | None = None  # comparison 模式有效
