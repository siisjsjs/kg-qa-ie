"""crystallize / lint / status / trend / followups 等小型 schema。"""

from typing import Literal

from pydantic import BaseModel, Field


# ===== crystallize =====
class CrystallizeRequest(BaseModel):
    text: str = Field(min_length=10)
    target_topic: str | None = None
    kind: Literal["synthesis", "comparison"] = "synthesis"


class CrystallizeResponse(BaseModel):
    slug: str
    title: str
    candidates_added: list[str] = Field(default_factory=list)


# ===== lint =====
class LintIssue(BaseModel):
    kind: str  # orphan / broken_link / contradiction / low_confidence / duplicate
    slug: str
    detail: str
    severity: Literal["info", "warning", "error"] = "warning"


class LintReport(BaseModel):
    issues: list[LintIssue]
    summary: dict[str, int] = Field(default_factory=dict)


# ===== status =====
class StatusReport(BaseModel):
    page_counts: dict[str, int] = Field(default_factory=dict)
    source_counts: dict[str, int] = Field(default_factory=dict)
    flywheel: dict = Field(default_factory=dict)
    coverage: dict = Field(default_factory=dict)
    vector_index: dict = Field(default_factory=dict)


# ===== trend =====
class TrendItem(BaseModel):
    theme: str
    query_count_30d: int
    gap_share: float
    auto_promote_candidate: bool


class TrendReport(BaseModel):
    hot_topics: list[TrendItem]
    comparison_pairs: list[tuple[str, str]] = Field(default_factory=list)


# ===== followups =====
class FollowupItem(BaseModel):
    source_page: str
    followup_text: str
    age_days: int
    status: str
