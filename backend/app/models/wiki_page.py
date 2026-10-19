"""wiki_pages：wiki/ 文件树的 MySQL 索引镜像。

source of truth 是文件系统；本表用于快速查询、JOIN 和 ID 引用。
"""

from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base


class WikiPage(Base):
    __tablename__ = "wiki_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(
        Enum(
            "entity", "topic", "source", "query", "synthesis", "comparison",
            name="wiki_page_kind",
        ),
        index=True,
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    frontmatter_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    body_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
