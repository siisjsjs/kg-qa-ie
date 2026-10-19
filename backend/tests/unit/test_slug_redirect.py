"""slug_redirect 服务单元测试。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  registers all ORM
from app.core.db import Base
from app.services.wiki.slug_redirect import (
    list_redirects,
    lookup,
    remove_redirect,
    resolve_chain,
    write_many,
    write_redirect,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sr.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_write_and_lookup(db):
    row = write_redirect(
        db, old_slug="工效学", new_slug="人因工程", kind="entity", reason="rename"
    )
    assert row.id is not None

    found = lookup(db, old_slug="工效学", kind="entity")
    assert found is not None
    assert found.new_slug == "人因工程"
    assert found.reason == "rename"


def test_write_overrides_existing(db):
    write_redirect(db, old_slug="A", new_slug="B", kind="entity", reason="rename")
    write_redirect(db, old_slug="A", new_slug="C", kind="entity", reason="merge")
    rows = list_redirects(db, kind="entity")
    # 同 (old_slug, kind) 唯一,只有一行,但 new_slug 已变
    assert len(rows) == 1
    assert rows[0].new_slug == "C"
    assert rows[0].reason == "merge"


def test_resolve_chain_two_hops(db):
    write_redirect(db, old_slug="X", new_slug="Y", kind="entity")
    write_redirect(db, old_slug="Y", new_slug="Z", kind="entity")
    final, hops = resolve_chain(db, slug="X", kind="entity")
    assert final == "Z"
    assert hops == ["Y", "Z"]


def test_resolve_chain_to_deleted(db):
    write_redirect(db, old_slug="孤立", new_slug=None, kind="entity", reason="source_deleted")
    final, hops = resolve_chain(db, slug="孤立", kind="entity")
    assert final is None
    assert hops == []


def test_resolve_chain_no_redirect(db):
    final, hops = resolve_chain(db, slug="不存在", kind="entity")
    assert final == "不存在"
    assert hops == []


def test_resolve_chain_cycle_protection(db):
    write_redirect(db, old_slug="A", new_slug="B", kind="entity")
    write_redirect(db, old_slug="B", new_slug="A", kind="entity")
    final, hops = resolve_chain(db, slug="A", kind="entity", max_hops=5)
    # 防环:遇到已访问 → 终止于上一个有效跳转
    assert final in ("B",)
    assert hops == ["B"]


def test_lookup_cross_kind(db):
    write_redirect(db, old_slug="src1", new_slug=None, kind="source", reason="source_deleted")
    write_redirect(db, old_slug="src1", new_slug="src2", kind="entity", reason="rename")
    # kind=None 时跨 kind 查最新
    row = lookup(db, old_slug="src1", kind=None)
    assert row is not None  # 拿到至少一条


def test_remove_redirect(db):
    write_redirect(db, old_slug="A", new_slug="B", kind="entity")
    assert remove_redirect(db, old_slug="A", kind="entity") is True
    assert lookup(db, old_slug="A", kind="entity") is None
    # 第二次删返 False
    assert remove_redirect(db, old_slug="A", kind="entity") is False


def test_list_redirects_filters_kind(db):
    write_redirect(db, old_slug="a1", new_slug="b1", kind="entity")
    write_redirect(db, old_slug="s1", new_slug=None, kind="source", reason="source_deleted")
    entities = list_redirects(db, kind="entity")
    sources = list_redirects(db, kind="source")
    assert {r.old_slug for r in entities} == {"a1"}
    assert {r.old_slug for r in sources} == {"s1"}


def test_write_many(db):
    n = write_many(
        db,
        rows=[
            {"old_slug": "a", "new_slug": "b", "kind": "entity"},
            {"old_slug": "x", "new_slug": None, "kind": "source", "reason": "source_deleted"},
        ],
    )
    assert n == 2
    assert lookup(db, old_slug="a", kind="entity") is not None
    assert lookup(db, old_slug="x", kind="source").new_slug is None
