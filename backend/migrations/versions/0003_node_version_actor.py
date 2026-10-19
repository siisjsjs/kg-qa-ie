"""add actor_user_id and title_snapshot to wiki_node_versions

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-20

P3 fix: ORM 加了 actor_user_id / title_snapshot 但 0001 没建。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("wiki_node_versions") as batch:
        batch.add_column(sa.Column("actor_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("title_snapshot", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("wiki_node_versions") as batch:
        batch.drop_column("title_snapshot")
        batch.drop_column("actor_user_id")
