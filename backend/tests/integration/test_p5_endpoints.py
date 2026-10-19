"""P5 端点集成测试:status / lint / trend / followups / admin gap-queue。"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core import config as cfg_module
from app.core.db import session_scope
from app.models.gap_queue import GapQueue
from app.models.qa import QaMessage, QaSession
from app.models.user import User


@pytest.fixture
def wiki_root(tmp_path, monkeypatch):
    from app.services.wiki.init import init_wiki

    root = tmp_path / "wiki_root"
    init_wiki(root)
    (root / "wiki" / "entities" / "A.md").write_text(
        "---\ntitle: A\nslug: A\nkind: entity\nsources: [s1]\n---\n[[B]]\n",
        encoding="utf-8",
    )
    (root / "wiki" / "entities" / "B.md").write_text(
        "---\ntitle: B\nslug: B\nkind: entity\nsources: [s2]\nconfidence: AMBIGUOUS\n---\n[[A]]\n",
        encoding="utf-8",
    )
    (root / "wiki" / "entities" / "孤立.md").write_text(
        "---\ntitle: 孤立\nslug: 孤立\nkind: entity\nsources: [s3]\n---\n孤立。\n",
        encoding="utf-8",
    )
    (root / "raw" / "articles" / "a.md").parent.mkdir(parents=True, exist_ok=True)
    (root / "raw" / "articles" / "a.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(cfg_module.settings, "wiki_root", str(root))
    return root


# ===== /lint =====
def test_get_lint_returns_issues(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/lint")
        assert resp.status_code == 200
        data = resp.json()
        kinds = {it["kind"] for it in data["issues"]}
        assert "orphan" in kinds
        assert "low_confidence" in kinds
        assert "summary" in data


def test_post_lint_fix_regenerates_index(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.post("/api/v1/lint/fix")
        assert resp.status_code == 200
        # index.md 应该被覆盖,含 entities 列表
        idx = (wiki_root / "index.md").read_text(encoding="utf-8")
        assert "知识库索引" in idx
        assert "[[A]]" in idx


# ===== /status =====
def test_get_status_basic(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page_counts"]["entities"] == 3
        assert data["source_counts"]["articles"] == 1
        assert "flywheel" in data
        assert "coverage" in data


# ===== /trend =====
def test_get_trend_with_refresh(wiki_root):
    from app.main import app

    with session_scope() as s:
        user = User(username="t_admin", email="t@y.com", password_hash="x", role="admin", status="active")
        s.add(user)
        s.flush()
        sess = QaSession(user_id=user.id, title="t")
        s.add(sess)
        s.flush()
        for _ in range(4):
            s.add(QaMessage(
                session_id=sess.id,
                user_id=user.id,
                question="VSM 怎么实施?",
                answer="a",
                gap_flag=True,
            ))

    with TestClient(app) as client:
        resp = client.get("/api/v1/trend?refresh=true")
        assert resp.status_code == 200
        data = resp.json()
        themes = [it["theme"] for it in data["hot_topics"]]
        assert "VSM" in themes


# ===== /followups =====
def test_get_followups(wiki_root):
    (wiki_root / "wiki" / "synthesis").mkdir(parents=True, exist_ok=True)
    (wiki_root / "wiki" / "synthesis" / "x.md").write_text(
        "---\ntitle: x\nslug: x\nfollowups:\n  - 跟进项目 1\n  - 跟进项目 2\n"
        "created: 2026-05-19T00:00:00+00:00\n---\n",
        encoding="utf-8",
    )
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/followups?refresh=true")
        assert resp.status_code == 200
        data = resp.json()
        texts = {it["followup_text"] for it in data}
        assert "跟进项目 1" in texts


# ===== /admin/gap-queue =====
def test_admin_gap_queue_list_and_update(wiki_root):
    from app.main import app

    with session_scope() as s:
        s.add(GapQueue(
            record_date=date.today(),
            origin="query",
            user_question="原始问题",
            gap_reasons='["score<0.6"]',
            suggested_seeds_json='["VSM"]',
            status="open",
        ))

    with TestClient(app) as client:
        resp = client.get("/api/v1/admin/gap-queue")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        gid = items[0]["id"]
        assert items[0]["status"] == "open"
        assert items[0]["suggested_seeds"] == ["VSM"]

        # 改为 in_progress
        resp = client.put(
            f"/api/v1/admin/gap-queue/{gid}",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

        # 改为 resolved
        resp = client.put(
            f"/api/v1/admin/gap-queue/{gid}",
            json={"status": "resolved", "resolved_via_ingest_job_id": 42},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "resolved"
        assert body["resolved_at"] is not None
        assert body["resolved_via_ingest_job_id"] == 42

        # 重启
        resp = client.put(
            f"/api/v1/admin/gap-queue/{gid}",
            json={"status": "reopened"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "reopened"
        assert resp.json()["resolved_at"] is None


def test_admin_gap_queue_update_404(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.put(
            "/api/v1/admin/gap-queue/99999", json={"status": "resolved"}
        )
        assert resp.status_code == 404


def test_admin_gap_queue_update_invalid_status(wiki_root):
    from app.main import app

    with session_scope() as s:
        row = GapQueue(
            record_date=date.today(),
            origin="query",
            gap_reasons="[]",
            suggested_seeds_json="[]",
            status="open",
        )
        s.add(row)
        s.flush()
        gid = row.id

    with TestClient(app) as client:
        resp = client.put(
            f"/api/v1/admin/gap-queue/{gid}", json={"status": "weird"}
        )
        assert resp.status_code == 422


def test_admin_gap_queue_filter_by_origin(wiki_root):
    from app.main import app

    with session_scope() as s:
        s.add(GapQueue(
            record_date=date.today(), origin="query",
            gap_reasons="[]", suggested_seeds_json="[]", status="open",
        ))
        s.add(GapQueue(
            record_date=date.today(), origin="merge_suggestion",
            gap_reasons="[]", suggested_seeds_json="[]", status="open",
        ))

    with TestClient(app) as client:
        resp = client.get("/api/v1/admin/gap-queue?origin=merge_suggestion")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["origin"] == "merge_suggestion"
