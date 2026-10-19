"""P6: qa_sessions / qa_messages 持久化扩展

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-22

把"问答 API 直接吐 SSE 但不落 DB"改成"按 user_id 隔离的多轮会话持久化"。

变更:
  qa_sessions 加 summary TEXT(空字符串默认),title 默认"新对话"
  qa_messages 重新定位为"role + content 一行一条"
    - 加 role VARCHAR(16):'user' | 'assistant' | 'system'
    - 加 content TEXT
    - 加 meta_json TEXT default '{}' (存 quality / citations / path 等)
    - 旧字段(question/answer/citations_json/path_json/...)保留 nullable,不再写入新数据,
      避免动到 hot_topics / gap_queue 这些已有读路径
  qa_messages 加 (session_id, created_at) 复合索引,会话拉取按时间序更快
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # qa_sessions.summary:TEXT 不允许 DEFAULT,先加可空再回填再收紧
    op.add_column("qa_sessions", sa.Column("summary", sa.Text(), nullable=True))
    bind.execute(sa.text("UPDATE qa_sessions SET summary = '' WHERE summary IS NULL"))
    op.alter_column("qa_sessions", "summary", existing_type=sa.Text(), nullable=False)

    # qa_messages.role:VARCHAR 可以带 DEFAULT
    op.add_column(
        "qa_messages",
        sa.Column("role", sa.String(length=16), nullable=False, server_default="user"),
    )

    # qa_messages.content / meta_json:TEXT 不允许 DEFAULT,同样两步走
    op.add_column("qa_messages", sa.Column("content", sa.Text(), nullable=True))
    op.add_column("qa_messages", sa.Column("meta_json", sa.Text(), nullable=True))
    bind.execute(sa.text("UPDATE qa_messages SET content = '' WHERE content IS NULL"))
    bind.execute(sa.text("UPDATE qa_messages SET meta_json = '{}' WHERE meta_json IS NULL"))
    op.alter_column("qa_messages", "content", existing_type=sa.Text(), nullable=False)
    op.alter_column("qa_messages", "meta_json", existing_type=sa.Text(), nullable=False)

    # 旧 question 字段放宽到 nullable(新链路只写 content)
    op.alter_column("qa_messages", "question", existing_type=sa.Text(), nullable=True)

    op.create_index(
        "ix_qa_messages_session_created",
        "qa_messages",
        ["session_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_qa_messages_session_created", table_name="qa_messages")
    op.alter_column(
        "qa_messages",
        "question",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.drop_column("qa_messages", "meta_json")
    op.drop_column("qa_messages", "content")
    op.drop_column("qa_messages", "role")
    op.drop_column("qa_sessions", "summary")
