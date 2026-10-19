"""status / hot_topics / followups 单元测试 — P5。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import db as db_module
from app.core.db import Base
from app.models.gap_queue import GapQueue
from app.models.qa import QaMessage, QaSession
from app.models.user import User
from app.services.growth.followups import (
    _scan_followups,
    refresh_followups_sync,
    list_followups,
)
from app.services.growth.hot_topics import (
    _read_threshold,
    _tokenize_question,
    list_hot_topics,
    refresh_hot_topics_sync,
)
from app.services.status.aggregator import aggregate
from app.services.status.coverage import coverage


def _seed(root, files: dict[str, str]):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    db_file = tmp_path / "status.db"
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


# ===== coverage =====
@pytest.mark.asyncio
async def test_coverage_counts(tmp_path):
    _seed(tmp_path, {
        "raw/articles/a.md": "x",
        "raw/articles/b.md": "y",
        "raw/zhihu/c.md": "z",
        "wiki/entities/A.md": (
            "---\ntitle: A\nsources: [s1, s2]\n---\n\n## 不同素材中的观点\n"
        ),
        "wiki/entities/B.md": (
            "---\ntitle: B\nsources: [s3]\n---\n\n## 简介\n"
        ),
    })
    out = await coverage(wiki_root=tmp_path)
    assert out["source_type_distribution"] == {"articles": 2, "zhihu": 1}
    assert out["entity_count"] == 2
    # B 单 source → orphan rate=0.5
    assert out["single_source_orphan_rate"] == 0.5
    # A 含 cross-source 段 → cross_source_fusion_rate=0.5
    assert out["cross_source_fusion_rate"] == 0.5


# ===== status aggregator =====
@pytest.mark.asyncio
async def test_aggregate_basic(tmp_path, db_session):
    _seed(tmp_path, {
        "wiki/entities/A.md": "---\ntitle: A\nsources: [s1]\n---\n\n",
        "wiki/topics/T.md": "---\ntitle: T\n---\n\n",
        "raw/articles/a.md": "x",
    })
    out = await aggregate(wiki_root=tmp_path, db=db_session)
    assert out["page_counts"]["entities"] == 1
    assert out["page_counts"]["topics"] == 1
    assert out["source_counts"]["articles"] == 1
    assert "flywheel" in out
    assert "coverage" in out


@pytest.mark.asyncio
async def test_aggregate_flywheel_with_qa_data(tmp_path, db_session):
    _seed(tmp_path, {})
    user = User(username="ab_admin", email="a@b.com", password_hash="x", role="admin", status="active")
    db_session.add(user)
    db_session.flush()
    sess = QaSession(user_id=user.id, title="t")
    db_session.add(sess)
    db_session.flush()
    db_session.add_all([
        QaMessage(
            session_id=sess.id,
            user_id=user.id,
            question="q1",
            answer="a",
            tier="gold",
            answer_quality=0.9,
            gap_flag=False,
        ),
        QaMessage(
            session_id=sess.id,
            user_id=user.id,
            question="q2",
            answer="a",
            tier="bronze",
            answer_quality=0.4,
            gap_flag=True,
        ),
    ])
    db_session.add(GapQueue(
        record_date=datetime.now(timezone.utc).date(),
        origin="query",
        gap_reasons="[]",
        suggested_seeds_json="[]",
        status="open",
    ))
    db_session.commit()

    out = await aggregate(wiki_root=tmp_path, db=db_session)
    fw = out["flywheel"]
    assert fw["query_total"] == 2
    assert fw["gap_flag_share"] == 0.5
    assert fw["answer_quality_tier"]["gold"] == 1
    assert fw["answer_quality_tier"]["bronze"] == 1
    assert fw["gap_queue"]["open"] == 1


# ===== hot_topics =====
def test_tokenize_question_filters_stopwords():
    out = _tokenize_question("VSM 是什么以及怎么实施?")
    assert "VSM" in out
    assert "什么" not in out
    assert "怎么" not in out


def test_read_threshold_default(tmp_path):
    assert _read_threshold(tmp_path) == 3


def test_read_threshold_from_schema(tmp_path):
    (tmp_path / ".wiki-schema.md").write_text(
        "## 成长配置\n\nhot_topic_threshold: 5\n", encoding="utf-8"
    )
    assert _read_threshold(tmp_path) == 5


def test_refresh_hot_topics_aggregates(tmp_path, db_session):
    user = User(username="xy_admin", email="x@y.com", password_hash="x", role="admin", status="active")
    db_session.add(user)
    db_session.flush()
    sess = QaSession(user_id=user.id, title="t")
    db_session.add(sess)
    db_session.flush()
    # 4 个含 VSM 的问题,3 个 gap_flag=True
    for i in range(4):
        db_session.add(QaMessage(
            session_id=sess.id,
            user_id=user.id,
            question=f"VSM 怎么实施 {i}?",
            answer="a",
            tier="silver",
            answer_quality=0.7,
            gap_flag=(i < 3),
        ))
    db_session.flush()

    n = refresh_hot_topics_sync(db=db_session, threshold=3, wiki_root=tmp_path)
    db_session.commit()
    assert n >= 1

    rows = list_hot_topics(db_session, limit=10)
    vsm_row = next((r for r in rows if r["theme"] == "VSM"), None)
    assert vsm_row is not None
    assert vsm_row["query_count_30d"] == 4
    assert vsm_row["gap_share"] == 0.75
    assert vsm_row["auto_promote_candidate"] is True


def test_refresh_hot_topics_below_threshold_skipped(tmp_path, db_session):
    user = User(username="zy_admin", email="z@y.com", password_hash="x", role="admin", status="active")
    db_session.add(user)
    db_session.flush()
    sess = QaSession(user_id=user.id, title="t")
    db_session.add(sess)
    db_session.flush()
    db_session.add(QaMessage(
        session_id=sess.id,
        user_id=user.id,
        question="VSM 是什么?",
        answer="a",
        tier="silver",
    ))
    db_session.commit()
    n = refresh_hot_topics_sync(db=db_session, threshold=5, wiki_root=tmp_path)
    db_session.commit()
    # 数量低于阈值 → 不写入
    assert n == 0


# ===== followups =====
def test_scan_followups_from_frontmatter(tmp_path):
    _seed(tmp_path, {
        "wiki/synthesis/2026-05-20-x.md": (
            "---\ntitle: x\nslug: 2026-05-20-x\nkind: synthesis\n"
            "followups:\n  - 待补 Y 主题\n  - 待补 Z 主题\n"
            "created: 2026-05-19T00:00:00+00:00\n---\n\n"
        ),
    })
    out = _scan_followups(tmp_path)
    texts = {it["followup_text"] for it in out}
    assert "待补 Y 主题" in texts
    assert "待补 Z 主题" in texts


def test_scan_followups_from_section(tmp_path):
    _seed(tmp_path, {
        "wiki/synthesis/2026-05-20-y.md": (
            "---\ntitle: y\nslug: 2026-05-20-y\nkind: synthesis\n---\n\n"
            "## 尚待解决的问题\n\n"
            "- 问题 A\n- 问题 B\n"
        ),
    })
    out = _scan_followups(tmp_path)
    texts = {it["followup_text"] for it in out}
    assert "问题 A" in texts
    assert "问题 B" in texts


def test_refresh_followups_writes_db(tmp_path, db_session):
    _seed(tmp_path, {
        "wiki/synthesis/x.md": (
            "---\ntitle: x\nfollowups:\n  - 跟进 1\n  - 跟进 2\n---\n"
        ),
    })
    n = refresh_followups_sync(db=db_session, wiki_root=tmp_path)
    db_session.commit()
    assert n >= 2
    out = list_followups(db_session)
    texts = {r["followup_text"] for r in out}
    assert "跟进 1" in texts
