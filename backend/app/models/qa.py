"""问答会话与消息表。

P6 起转为 role+content 模型(QaMessage 一行一条),持久化多轮对话。
旧的 question/answer 等字段保留 nullable,仅供 hot_topics/gap_queue 旧读路径兼容,
新链路不再写入。
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base


class QaSession(Base):
    __tablename__ = "qa_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    # 截至这一刻之前的消息已被 summary 替代;LLM 上下文从这之后的消息开始拼。
    # 用户视图(/conversations/{id})仍返回完整 messages,该字段对前端不可见。
    summary_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class QaMessage(Base):
    __tablename__ = "qa_messages"
    __table_args__ = (
        Index("ix_qa_messages_session_created", "session_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("qa_sessions.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    # 新模型(P6+)
    role: Mapped[str] = mapped_column(String(16), default="user", server_default="user", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    meta_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}", nullable=False)

    # 旧模型字段 - hot_topics / gap_queue 仍按 question/persisted_query_slug 读历史数据,
    # 新链路不再写入,但保留以保后兼容。
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    path_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    answer_quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    gap_flag: Mapped[bool] = mapped_column(default=False, nullable=False)
    persisted_query_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
