"""documents.book

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-03

上传素材时选定的目标知识库("书")。落库后由 pipeline 写入 source frontmatter.book,
topic/entity 运行时从 sources[0] 继承。缺省 NULL → 章节落「未分类」桶。
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("book", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "book")
