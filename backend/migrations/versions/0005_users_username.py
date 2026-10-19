"""P5: users.username

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-20

为 users 表新增 username 字段(唯一、非空)。
现有用户回填为 email 的本地部分(@ 之前),冲突时追加 _<id>。
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1) 先以可空形式加列,便于回填
    op.add_column("users", sa.Column("username", sa.String(length=64), nullable=True))

    # 2) 回填:取 email 的 @ 之前;若与他行重复则追加 _<id>
    bind.execute(
        sa.text(
            """
            UPDATE users
            SET username = SUBSTRING_INDEX(email, '@', 1)
            WHERE username IS NULL
            """
        )
    )
    # 3) 处理回填后的重复:同 username 多于一行的,除最早一行外都追加 _<id>
    bind.execute(
        sa.text(
            """
            UPDATE users u
            JOIN (
                SELECT username, MIN(id) AS keep_id
                FROM users
                GROUP BY username
                HAVING COUNT(*) > 1
            ) d ON d.username = u.username AND u.id <> d.keep_id
            SET u.username = CONCAT(u.username, '_', u.id)
            """
        )
    )

    # 4) 收紧:NOT NULL + UNIQUE 索引
    op.alter_column("users", "username", existing_type=sa.String(length=64), nullable=False)
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
