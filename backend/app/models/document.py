"""素材文档表。每条对应一份用户上传的 .md。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(
        Enum(
            "md", "web", "ppt", "pdf", "tweet", "wechat", "xiaohongshu", "zhihu", "plain_text",
            name="document_source_type",
        ),
        default="md",
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    parse_status: Mapped[str] = mapped_column(
        Enum("pending", "running", "succeeded", "failed", name="document_parse_status"),
        default="pending",
        nullable=False,
    )
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 上传时选定的目标知识库("书");写入 source frontmatter.book,topic/entity 运行时继承。
    # 缺省 NULL → source 落「未分类」桶。
    book: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
