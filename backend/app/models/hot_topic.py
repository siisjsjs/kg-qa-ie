"""hot_topics：热门主题聚合表（周期性 job 更新）。"""

from datetime import datetime

from sqlalchemy import Date, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base


class HotTopic(Base):
    __tablename__ = "hot_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    window_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    query_count_30d: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    gap_share: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    auto_promote_candidate: Mapped[bool] = mapped_column(default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
