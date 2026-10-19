"""followup_queue：待跟进项聚合视图。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base


class FollowupQueue(Base):
    __tablename__ = "followup_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_page: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    followup_text: Mapped[str] = mapped_column(Text, nullable=False)
    age_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
