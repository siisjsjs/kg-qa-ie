"""新增 announcements 表 + users.last_login_at

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-23

公告（管理员发布，主页展示）+ 用户最后登录时间（admin dashboard 用）。
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("cover_image_url", sa.String(length=512), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "visibility",
            sa.Enum("published", "draft", name="announcement_visibility"),
            nullable=False,
            server_default="published",
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_announcements_pinned_created_at",
        "announcements",
        ["pinned", "created_at"],
    )

    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "last_login_at")
    op.drop_index("ix_announcements_pinned_created_at", table_name="announcements")
    op.drop_table("announcements")
