"""集成测试 fixture:用 SQLite 内存库代替 MySQL,跑流水线全链路。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import db as db_module
from app.core.db import Base


@pytest.fixture(autouse=True)
def _sqlite_engine(tmp_path, monkeypatch):
    """把 db._engine / db._SessionLocal 替换成 SQLite,create_all 14 张表。"""
    # 用文件级 SQLite(每条连接独立),线程间共享一个 file
    db_file = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        future=True,
        connect_args={"check_same_thread": False},
    )

    # 引入所有 ORM 模型让 metadata 注册
    import app.models  # noqa: F401

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    monkeypatch.setattr(db_module, "_engine", engine)
    monkeypatch.setattr(db_module, "_SessionLocal", SessionLocal)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()
