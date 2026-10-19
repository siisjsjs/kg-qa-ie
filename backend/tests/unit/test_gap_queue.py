"""gap_queue 服务单元测试 — P4。"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.core import db as db_module
from app.core.db import Base
from app.models.gap_queue import GapQueue
from app.services.growth.gap_queue import (
    VALID_ORIGIN,
    VALID_STATUS,
    append_gap,
    list_gaps,
    list_open_gaps,
    update_status,
    write_gap_queue_markdown,
)


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_file = tmp_path / "gap.db"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True
    )
    import app.models  # noqa: F401

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(db_module, "_engine", engine)
    monkeypatch.setattr(db_module, "_SessionLocal", SessionLocal)
    s = SessionLocal()
    yield s
    s.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.mark.asyncio
async def test_append_gap_writes_row(db_session):
    gid = await append_gap(
        db=db_session,
        origin="query",
        user_question="VSM 是什么?",
        gap_reasons=["score<0.6"],
        suggested_seeds=["VSM", "价值流图"],
    )
    db_session.commit()
    row = db_session.get(GapQueue, gid)
    assert row is not None
    assert row.origin == "query"
    assert row.user_question == "VSM 是什么?"
    assert json.loads(row.gap_reasons) == ["score<0.6"]
    assert json.loads(row.suggested_seeds_json) == ["VSM", "价值流图"]
    assert row.status == "open"


@pytest.mark.asyncio
async def test_append_gap_rejects_invalid_origin(db_session):
    with pytest.raises(ValueError):
        await append_gap(db=db_session, origin="bogus", gap_reasons=[], suggested_seeds=[])


@pytest.mark.asyncio
async def test_append_gap_rejects_invalid_status(db_session):
    with pytest.raises(ValueError):
        await append_gap(
            db=db_session,
            origin="query",
            status="weird",
            gap_reasons=[],
            suggested_seeds=[],
        )


@pytest.mark.asyncio
async def test_list_gaps_filters(db_session):
    await append_gap(db=db_session, origin="query", gap_reasons=[], suggested_seeds=[])
    await append_gap(
        db=db_session, origin="comparison_cell", gap_reasons=[], suggested_seeds=[],
    )
    db_session.commit()

    out_all = await list_gaps(db=db_session, status=None)
    assert len(out_all) == 2
    out_open = await list_open_gaps(limit=100)
    # list_open_gaps 走 session_scope,需要走 db_module engine
    # 这里我们直接调 list_gaps with db=db_session
    out_query = await list_gaps(db=db_session, origin="query")
    assert len(out_query) == 1
    assert out_query[0]["origin"] == "query"


@pytest.mark.asyncio
async def test_update_status_sets_resolved(db_session):
    gid = await append_gap(
        db=db_session, origin="query", gap_reasons=[], suggested_seeds=[]
    )
    db_session.commit()
    out = await update_status(db=db_session, gap_id=gid, status="resolved", resolved_by=42)
    db_session.commit()
    assert out["status"] == "resolved"
    assert out["resolved_at"] is not None
    assert out["resolved_by"] == 42


@pytest.mark.asyncio
async def test_update_status_reopened_clears_resolved_at(db_session):
    gid = await append_gap(
        db=db_session, origin="query", gap_reasons=[], suggested_seeds=[]
    )
    db_session.commit()
    await update_status(db=db_session, gap_id=gid, status="resolved", resolved_by=1)
    db_session.commit()
    row = db_session.get(GapQueue, gid)
    assert row.resolved_at is not None
    out = await update_status(db=db_session, gap_id=gid, status="reopened")
    db_session.commit()
    assert out["status"] == "reopened"
    assert out["resolved_at"] is None


@pytest.mark.asyncio
async def test_update_status_404(db_session):
    with pytest.raises(LookupError):
        await update_status(db=db_session, gap_id=99999, status="resolved")


@pytest.mark.asyncio
async def test_update_status_invalid(db_session):
    gid = await append_gap(
        db=db_session, origin="query", gap_reasons=[], suggested_seeds=[]
    )
    db_session.commit()
    with pytest.raises(ValueError):
        await update_status(db=db_session, gap_id=gid, status="not-a-state")


def test_valid_origin_status_sets():
    assert "query" in VALID_ORIGIN
    assert "merge_suggestion" in VALID_ORIGIN
    assert "reopened" in VALID_STATUS


@pytest.mark.asyncio
async def test_write_gap_queue_markdown(tmp_path, db_session, monkeypatch):
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "wiki_root", str(tmp_path))
    (tmp_path / "wiki" / "_growth").mkdir(parents=True, exist_ok=True)

    await append_gap(
        db=db_session,
        origin="query",
        user_question="VSM 是什么?",
        gap_reasons=["score<0.6"],
        suggested_seeds=["VSM"],
    )
    db_session.commit()

    out = write_gap_queue_markdown(wiki_root=tmp_path)
    assert out is not None
    txt = out.read_text(encoding="utf-8")
    assert "知识缺口队列" in txt
    assert "VSM" in txt
    assert "score<0.6" in txt
