"""Alembic env.py — 异步可改造。

骨架阶段：使用 SQLAlchemy 同步引擎；后续如改 async 再切换到 async_engine_from_config。
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.db import Base

# 重要:导入所有模型让 Base.metadata 收齐
from app.models import (  # noqa: F401
    Document,
    FollowupQueue,
    GapQueue,
    HotTopic,
    IngestJob,
    PromptVersion,
    QaMessage,
    QaSession,
    SlugRedirect,
    SystemConfig,
    User,
    WikiAlias,
    WikiLink,
    WikiNodeVersion,
    WikiPage,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 优先级:命令行 -x url=... > 已设置的 sqlalchemy.url(且不是 ini 占位)> settings.database_url
# 让 P3 起的 alembic 验证可以临时切到 SQLite,而不影响生产 MySQL 默认。
_existing_url = config.get_main_option("sqlalchemy.url") or ""
_x_args = context.get_x_argument(as_dictionary=True)
if "url" in _x_args:
    config.set_main_option("sqlalchemy.url", _x_args["url"])
elif not _existing_url or _existing_url.startswith("mysql+pymysql://kgqa:CHANGE_ME@localhost:3306"):
    # ini 文件保留的占位 URL → 用 settings 覆盖(保持 P1/P2 行为)
    config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
