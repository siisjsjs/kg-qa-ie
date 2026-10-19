"""P6+: qa_sessions.summary_until

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-22

加 `summary_until DATETIME` 字段,标记"截至这一刻之前的消息已被 summary 替代"。
作用:
  - 压缩动作不再删消息(用户能看到完整历史)
  - LLM 上下文只用 summary + summary_until 之后的消息(省 token)
  - 给前端的 conversation detail 仍返完整 messages
未压缩的会话该字段为 NULL。
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "qa_sessions",
        sa.Column("summary_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("qa_sessions", "summary_until")
