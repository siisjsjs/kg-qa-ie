"""admin schema/purpose + wiki pages + auth 集成测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import config as cfg_module


@pytest.fixture
def wiki_root(tmp_path, monkeypatch):
    from app.services.wiki.init import init_wiki

    root = tmp_path / "wiki_root"
    init_wiki(root)
    monkeypatch.setattr(cfg_module.settings, "wiki_root", str(root))
    return root


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


def test_auth_register_first_user_is_admin(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "first@test.io", "password": "longpass1", "display_name": "First"},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["role"] == "admin"

        # 第二个用户应是 user
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "second@test.io", "password": "longpass1"},
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "user"


def test_auth_register_duplicate_email(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/register",
            json={"email": "dup@test.io", "password": "longpass1"},
        )
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "dup@test.io", "password": "longpass1"},
        )
        assert resp.status_code == 409


def test_auth_login_and_me(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/register",
            json={"email": "login@test.io", "password": "longpass1"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.io", "password": "longpass1"},
        )
        assert resp.status_code == 200
        token = resp.json()["access_token"]
        assert token

        # /me 携带 token
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "login@test.io"


def test_auth_login_bad_password(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/register",
            json={"email": "x@test.io", "password": "longpass1"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "x@test.io", "password": "wrong-pw1"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# admin schema / purpose
# ---------------------------------------------------------------------------


def test_admin_schema_get_then_put(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/admin/schema")
        assert resp.status_code == 200
        # init_wiki 已 seed schema 文件,有 IE 默认别名
        data = resp.json()
        assert any(a["canonical"] == "工效学" for a in data["aliases"])

        new_payload = {
            "raw_markdown": "",
            "aliases": [
                {"canonical": "新概念", "members": ["新概念", "新别名"]},
            ],
            "relation_types": [{"name": "实现", "description": "A 是 B 的方法"}],
            "growth_config": {"hot_topic_threshold": 10},
        }
        resp = client.put("/api/v1/admin/schema", json=new_payload)
        assert resp.status_code == 200, resp.text

        resp = client.get("/api/v1/admin/schema")
        data = resp.json()
        aliases = {a["canonical"]: a["members"] for a in data["aliases"]}
        assert "新概念" in aliases
        assert data["growth_config"]["hot_topic_threshold"] == 10


def test_admin_purpose_get_then_put(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/admin/purpose")
        assert resp.status_code == 200

        payload = {
            "raw_markdown": "",
            "research_goals": ["建立 IE 知识库"],
            "key_questions": ["工效学是什么", "VSM 怎么实施"],
            "in_scope": ["作业测定", "VSM"],
            "out_of_scope": ["财务"],
        }
        resp = client.put("/api/v1/admin/purpose", json=payload)
        assert resp.status_code == 200

        resp = client.get("/api/v1/admin/purpose")
        data = resp.json()
        assert "VSM 怎么实施" in data["key_questions"]
        assert "作业测定" in data["in_scope"]


# ---------------------------------------------------------------------------
# wiki pages
# ---------------------------------------------------------------------------


def test_wiki_page_get_404(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/pages/不存在")
        assert resp.status_code == 404


def test_wiki_page_get_returns_backlinks_outlinks(wiki_root):
    # seed 两页:A 引用 B
    (wiki_root / "wiki" / "entities" / "A.md").write_text(
        "---\ntitle: A\nslug: A\nkind: entity\n---\n\n[[B]] 是它的实现。\n",
        encoding="utf-8",
    )
    (wiki_root / "wiki" / "entities" / "B.md").write_text(
        "---\ntitle: B\nslug: B\nkind: entity\n---\n\nB 内容。\n",
        encoding="utf-8",
    )

    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/pages/A")
        assert resp.status_code == 200
        data = resp.json()
        # outlinks/backlinks 是 [{slug, title, kind}] 对象列表
        assert [o["slug"] for o in data["outlinks"]] == ["B"]
        assert data["backlinks"] == []

        resp = client.get("/api/v1/wiki/pages/B")
        data = resp.json()
        assert data["outlinks"] == []
        assert [b["slug"] for b in data["backlinks"]] == ["A"]


def test_wiki_page_put_writes_and_bumps_version(wiki_root):
    (wiki_root / "wiki" / "entities" / "X.md").write_text(
        "---\ntitle: X\nslug: X\nkind: entity\nversion: 1\n---\n\n旧正文。\n",
        encoding="utf-8",
    )
    from app.main import app

    with TestClient(app) as client:
        resp = client.put(
            "/api/v1/wiki/pages/X",
            json={
                "body": "新正文 [[Y]]",
                "frontmatter": {"title": "X", "tags": ["新增"]},
                "bump_version": True,
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["version"] == 2
        assert data["body"].strip().startswith("新正文")
        assert [o["slug"] for o in data["outlinks"]] == ["Y"]


def test_wiki_index_lists_pages(wiki_root):
    (wiki_root / "wiki" / "entities" / "A.md").write_text(
        "---\ntitle: A\nslug: A\nkind: entity\n---\n\nA。\n", encoding="utf-8"
    )
    (wiki_root / "wiki" / "topics" / "T1.md").write_text(
        "---\ntitle: T1\nslug: T1\nkind: topic\n---\n\nT1。\n", encoding="utf-8"
    )
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/index")
        assert resp.status_code == 200
        data = resp.json()
        # by_kind 现在是 {kind: [{slug, title, kind}]}
        entity_slugs = [e["slug"] for e in data["by_kind"]["entity"]]
        topic_slugs = [t["slug"] for t in data["by_kind"]["topic"]]
        assert "A" in entity_slugs
        assert "T1" in topic_slugs
        assert data["counts"]["total"] >= 2


def test_delete_source_removes_file_and_evicts_cache(wiki_root):
    from app.services.wiki.cache import record

    # seed 一个 source 页 + cache 条目
    (wiki_root / "wiki" / "sources" / "2026-05-19-x.md").write_text(
        "---\ntitle: x\nslug: 2026-05-19-x\nkind: source\n---\n\nbody\n",
        encoding="utf-8",
    )
    record(wiki_root, content_hash="a" * 64, source_slug="2026-05-19-x")

    from app.main import app

    with TestClient(app) as client:
        resp = client.delete("/api/v1/sources/2026-05-19-x")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["cache_evicted"] >= 1
        assert "wiki/sources/2026-05-19-x.md" in data["removed_pages"]
        assert not (wiki_root / "wiki" / "sources" / "2026-05-19-x.md").exists()
