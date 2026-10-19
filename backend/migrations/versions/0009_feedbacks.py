"""新增 feedbacks 表

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-23

用户反馈:用户提交、管理员管理。
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedbacks",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "category",
            sa.Enum("bug", "feature", "content", "other", name="feedback_category"),
            nullable=False,
            server_default="other",
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("contact", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "open", "in_progress", "resolved", "dismissed",
                name="feedback_status",
            ),
            nullable=False,
            server_default="open",
        ),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedbacks_user_id", "feedbacks", ["user_id"])
    op.create_index("ix_feedbacks_status", "feedbacks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_feedbacks_status", table_name="feedbacks")
    op.drop_index("ix_feedbacks_user_id", table_name="feedbacks")
    op.drop_table("feedbacks")
