"""P3.1: slug_redirects 表

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-20 12:00:00

新增 slug_redirects 表(P3 delete 级联清理 + 节点重命名重定向用)。
wiki_node_versions 的 actor_user_id / title_snapshot 列移到 0003。
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "slug_redirects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("old_slug", sa.String(length=255), nullable=False),
        sa.Column("new_slug", sa.String(length=255), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_slug_redirects_old_slug"), "slug_redirects", ["old_slug"], unique=False
    )
    op.create_index(
        "uq_slug_redirect_old_kind", "slug_redirects", ["old_slug", "kind"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_slug_redirect_old_kind", table_name="slug_redirects")
    op.drop_index(op.f("ix_slug_redirects_old_slug"), table_name="slug_redirects")
    op.drop_table("slug_redirects")
