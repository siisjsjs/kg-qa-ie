"""Admin Dashboard 指标用 schemas。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RecentUser(BaseModel):
    id: int
    username: str
    email: str
    role: str
    last_login_at: datetime | None
    created_at: datetime


class DailyCount(BaseModel):
    date: str  # YYYY-MM-DD (本地)
    count: int


class DashboardMetrics(BaseModel):
    total_users: int
    new_users_7d: int
    active_users_7d: int  # 近 7 天有 last_login_at 的用户
    total_qa_30d: int
    total_qa_all: int  # 全部历史累计提问数(role=user 消息)
    total_wiki_pages: int
    total_announcements: int
    qa_daily_14d: list[DailyCount]
    recent_active_users: list[RecentUser]
