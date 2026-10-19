"""wiki_links：双链反查索引（src_slug -[type]-> dst_slug）。"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base


class WikiLink(Base):
    __tablename__ = "wiki_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    src_slug: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    dst_slug: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    link_type: Mapped[str] = mapped_column(String(64), default="related", nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    confidence: Mapped[str] = mapped_column(
        String(16), default="EXTRACTED", nullable=False  # EXTRACTED / INFERRED / AMBIGUOUS
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uq_wiki_link_src_dst_type", "src_slug", "dst_slug", "link_type", unique=True),
    )
