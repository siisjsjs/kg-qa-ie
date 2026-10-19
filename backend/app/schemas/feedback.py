"""用户反馈相关 schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FeedbackCreate(BaseModel):
    category: Literal["bug", "feature", "content", "other"] = "other"
    content: str = Field(min_length=1, max_length=10_000)
    contact: str | None = Field(default=None, max_length=255)


class FeedbackUpdate(BaseModel):
    status: Literal["open", "in_progress", "resolved", "dismissed"] | None = None
    admin_note: str | None = Field(default=None, max_length=10_000)


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    username: str | None = None  # 取自 join,可空
    category: Literal["bug", "feature", "content", "other"]
    content: str
    contact: str | None
    status: Literal["open", "in_progress", "resolved", "dismissed"]
    admin_note: str | None
    created_at: datetime
    updated_at: datetime


class FeedbackList(BaseModel):
    items: list[FeedbackOut]
