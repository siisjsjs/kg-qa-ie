"""wiki/version_history 单元测试。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.core.db import Base
from app.services.wiki.version_history import (
    compute_diff,
    diff_stat,
    get_version,
    list_versions,
    write_version,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'vh.db'}",
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


def test_compute_diff_frontmatter_changes():
    old_fm = {"title": "X", "tags": ["a"]}
    new_fm = {"title": "X", "tags": ["a", "b"], "version": 2}
    diff = compute_diff(old_fm=old_fm, old_body="", new_fm=new_fm, new_body="")
    fm = diff["frontmatter"]
    assert "version" in fm["added"]
    assert "tags" in fm["changed"]


def test_compute_diff_ignores_timestamp_fields():
    old = {"updated": "2026-01-01"}
    new = {"updated": "2026-05-20"}
    diff = compute_diff(old_fm=old, old_body="", new_fm=new, new_body="")
    fm = diff["frontmatter"]
    assert "updated" not in fm["changed"]


def test_compute_diff_body_added_removed_lines():
    old = "line1\nline2\nline3\n"
    new = "line1\nline2-modified\nline3\nline4\n"
    diff = compute_diff(old_fm={}, old_body=old, new_fm={}, new_body=new)
    stat = diff["body"]["stat"]
    assert stat["added_lines"] >= 1
    assert stat["removed_lines"] >= 1
    assert "@@" in diff["body"]["unified"] or "+line2-modified" in diff["body"]["unified"]


def test_compute_diff_body_truncated():
    old = ""
    new = "X\n" * 5000
    diff = compute_diff(old_fm={}, old_body=old, new_fm={}, new_body=new)
    assert "(truncated)" in diff["body"]["unified"]


def test_write_and_list_versions(db):
    write_version(
        db, slug="A", version=1, diff={"a": 1}, triggered_by="ingest", title_snapshot="A v1"
    )
    write_version(
        db, slug="A", version=2, diff={"b": 2}, triggered_by="manual", title_snapshot="A v2"
    )
    write_version(
        db, slug="B", version=1, diff={"c": 3}, triggered_by="ingest", title_snapshot="B"
    )
    rows = list_versions(db, slug="A")
    assert [r.version for r in rows] == [2, 1]
    assert rows[0].triggered_by == "manual"


def test_get_version(db):
    write_version(
        db, slug="X", version=3, diff={}, triggered_by="ingest", title_snapshot="X"
    )
    row = get_version(db, slug="X", version=3)
    assert row is not None
    assert row.title_snapshot == "X"
    assert get_version(db, slug="X", version=99) is None


def test_diff_stat_extracts_summary():
    diff = {
        "frontmatter": {"added": {"k": 1}, "removed": {}, "changed": {"x": {"old": 1, "new": 2}}},
        "body": {"stat": {"added_lines": 5, "removed_lines": 3}},
    }
    stat = diff_stat(json.dumps(diff))
    assert stat["added_lines"] == 5
    assert stat["fm_added"] == 1
    assert stat["fm_changed"] == 1


def test_diff_stat_handles_corrupt_json():
    assert diff_stat("not json") == {}
    assert diff_stat("") == {}
