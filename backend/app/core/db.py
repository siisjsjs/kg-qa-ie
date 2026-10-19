"""数据库会话：SQLAlchemy 2.x engine + sessionmaker。

骨架阶段：engine 延迟创建（首次调用 get_db 时构造），允许无 MySQL 启动 FastAPI。
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

_engine: Any = None
_SessionLocal: sessionmaker[Session] | None = None


class Base(DeclarativeBase):
    """所有 ORM 模型继承此基类。"""


def _get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            future=True,
        )
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：注入 SQLAlchemy Session。

    骨架阶段调用会触发真实 DB 连接尝试；未配 MySQL 时端点会 502。
    """
    _get_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """非依赖注入场景下使用：with session_scope() as db: ..."""
    _get_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
