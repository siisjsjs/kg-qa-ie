"""公告（Announcement）相关 schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    body_md: str = Field(default="", max_length=200_000)
    cover_image_url: str | None = Field(default=None, max_length=512)
    pinned: bool = False
    visibility: Literal["published", "draft"] = "published"


class AnnouncementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    body_md: str | None = Field(default=None, max_length=200_000)
    cover_image_url: str | None = Field(default=None, max_length=512)
    pinned: bool | None = None
    visibility: Literal["published", "draft"] | None = None


class AnnouncementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    body_md: str
    cover_image_url: str | None
    pinned: bool
    visibility: Literal["published", "draft"]
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class AnnouncementList(BaseModel):
    items: list[AnnouncementOut]


class ImageUploadResult(BaseModel):
    url: str
