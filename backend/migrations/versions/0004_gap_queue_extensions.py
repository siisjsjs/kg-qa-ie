"""P4: gap_queue 扩展(reopened 状态 + updated_at + resolved_via_ingest_job_id)

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-20

P4 飞轮:gap-queue 状态机增加 reopened;补 updated_at + resolved_via_ingest_job_id。
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    with op.batch_alter_table("gap_queue") as batch:
        batch.add_column(
            sa.Column("resolved_via_ingest_job_id", sa.Integer(), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            )
        )

    if dialect == "mysql":
        op.execute(
            "ALTER TABLE gap_queue MODIFY status ENUM("
            "'open','in_progress','resolved','dismissed','reopened'"
            ") NOT NULL DEFAULT 'open'"
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "mysql":
        op.execute(
            "ALTER TABLE gap_queue MODIFY status ENUM("
            "'open','in_progress','resolved','dismissed'"
            ") NOT NULL DEFAULT 'open'"
        )

    with op.batch_alter_table("gap_queue") as batch:
        batch.drop_column("updated_at")
        batch.drop_column("resolved_via_ingest_job_id")
