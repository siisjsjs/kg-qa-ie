"""gap_queue：知识缺口队列。镜像 wiki/_growth/gap-queue.md。"""

from datetime import datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base


class GapQueue(Base):
    __tablename__ = "gap_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    origin: Mapped[str] = mapped_column(
        Enum(
            "query", "digest", "comparison_cell", "graph_isolated", "graph_sparse_community",
            "merge_suggestion", "manual",
            name="gap_origin",
        ),
        nullable=False,
        index=True,
    )
    user_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    query_page: Mapped[str | None] = mapped_column(String(255), nullable=True)
    digest_page: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gap_reasons: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    suggested_seeds_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(
            "open", "in_progress", "resolved", "dismissed", "reopened",
            name="gap_status",
        ),
        default="open",
        nullable=False,
        index=True,
    )
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_via_ingest_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
