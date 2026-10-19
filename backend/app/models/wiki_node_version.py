"""wiki_node_versions:节点级别版本历史(diff 持久化)。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base


class WikiNodeVersion(Base):
    __tablename__ = "wiki_node_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    diff_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(64), nullable=False)  # ingest / manual / merge / crystallize
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
