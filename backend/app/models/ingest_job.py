"""ingest_jobs：5 阶段流水线任务记录。"""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base


class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(
        Enum(
            "upload", "cache_check", "step1_extract", "step2_compose", "post_process",
            name="ingest_stage",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum("queued", "running", "succeeded", "failed", "skipped", name="ingest_status"),
        default="queued",
        nullable=False,
        index=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
