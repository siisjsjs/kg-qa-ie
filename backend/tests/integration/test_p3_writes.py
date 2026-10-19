"""P3 集成测试:delete 级联 / slug-redirect / crystallize / 版本历史 / 反链 / batch。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.core import config as cfg_module
from app.core.db import session_scope


@pytest.fixture
def wiki_root(tmp_path, monkeypatch):
    from app.services.wiki.init import init_wiki

    root = tmp_path / "wr"
    init_wiki(root)
    monkeypatch.setattr(cfg_module.settings, "wiki_root", str(root))
    monkeypatch.setattr(cfg_module.settings, "cache_dir", str(tmp_path / "cache"))
    return root


# ===========================================================================
# DELETE source 端到端
# ===========================================================================


def test_delete_source_404_when_missing(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.delete("/api/v1/sources/no-such-source")
        assert resp.status_code == 404


def test_delete_source_cascades_and_writes_redirect(wiki_root):
    """删 source → 实体页"### 来源:[[X]]"段被清 + frontmatter.sources 移除。"""
    src_md = (
        "---\ntitle: src1\nslug: src1\nkind: source\n---\n\n## 摘要\n源 1 摘要"
    )
    (wiki_root / "wiki" / "sources" / "src1.md").write_text(src_md, encoding="utf-8")

    entity_md = """---
title: 工效学
slug: 工效学
kind: entity
sources:
  - src1
  - src2
---

## 简介

研究人在工作中的效率。

## 不同素材中的观点

### 来源:[[src1]]

来自 src1 的视角

### 来源:[[src2]]

来自 src2 的视角
"""
    (wiki_root / "wiki" / "entities" / "工效学.md").write_text(entity_md, encoding="utf-8")

    from app.main import app

    with TestClient(app) as client:
        resp = client.delete("/api/v1/sources/src1")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert "wiki/sources/src1.md" in data["removed_pages"]
        assert any(p == "wiki/entities/工效学.md" for p in data["updated_pages"])

        # 实体页仍在
        text = (wiki_root / "wiki" / "entities" / "工效学.md").read_text(encoding="utf-8")
        assert "src1" not in text
        assert "来自 src2 的视角" in text


def test_delete_source_orphan_entity_returns_410_via_redirect(wiki_root):
    """孤立实体被删 → 写 redirect(new_slug=None);GET 该 slug → 410 Gone。"""
    (wiki_root / "wiki" / "sources" / "only_src.md").write_text(
        "---\ntitle: only\nslug: only_src\nkind: source\n---\n\nsrc",
        encoding="utf-8",
    )
    (wiki_root / "wiki" / "entities" / "孤立.md").write_text(
        "---\ntitle: 孤立\nslug: 孤立\nkind: entity\nsources:\n  - only_src\n---\n\n## 简介\n仅来自 only_src",
        encoding="utf-8",
    )

    from app.main import app

    with TestClient(app) as client:
        resp = client.delete("/api/v1/sources/only_src")
        assert resp.status_code == 200
        assert "wiki/entities/孤立.md" in resp.json()["removed_pages"]

        # 再 GET 那个孤立 slug → 410
        resp = client.get("/api/v1/wiki/pages/孤立")
        assert resp.status_code == 410
        body = resp.json()
        # FastAPI 把 detail dict 直接放到响应体
        assert body["detail"]["code"] == "GONE"


# ===========================================================================
# SLUG REDIRECT - GET 跟随
# ===========================================================================


def test_get_page_follows_redirect_chain(wiki_root):
    """seed 一条 X → Y 的 redirect,GET /wiki/pages/X 应返回 Y 的内容。"""
    (wiki_root / "wiki" / "entities" / "Y.md").write_text(
        "---\ntitle: Y\nslug: Y\nkind: entity\n---\n\nY 正文",
        encoding="utf-8",
    )

    from app.main import app
    from app.services.wiki.slug_redirect import write_redirect

    with TestClient(app) as client:
        # 先建一条 redirect
        with session_scope() as db:
            write_redirect(db, old_slug="X", new_slug="Y", kind="entity", reason="rename")

        resp = client.get("/api/v1/wiki/pages/X")
        assert resp.status_code == 200
        data = resp.json()
        assert data["slug"] == "Y"
        assert resp.headers.get("X-KGQA-Redirected-From") == "X"


def test_get_page_no_follow_when_disabled(wiki_root):
    (wiki_root / "wiki" / "entities" / "Y2.md").write_text(
        "---\ntitle: Y2\nslug: Y2\nkind: entity\n---\n\nY2",
        encoding="utf-8",
    )
    from app.main import app
    from app.services.wiki.slug_redirect import write_redirect

    with TestClient(app) as client:
        with session_scope() as db:
            write_redirect(db, old_slug="X2", new_slug="Y2", kind="entity")

        resp = client.get("/api/v1/wiki/pages/X2?follow_redirect=false")
        # follow=false + 文件不存在 → 404
        assert resp.status_code == 404


# ===========================================================================
# 反链 API
# ===========================================================================


def test_backlinks_api_from_db(wiki_root):
    """seed wiki_links 行 + 对应 wiki_pages,GET /wiki/pages/X/backlinks 走 DB 路径。"""
    from app.main import app
    from app.models.wiki_link import WikiLink
    from app.models.wiki_page import WikiPage

    (wiki_root / "wiki" / "entities" / "X.md").write_text(
        "---\ntitle: X\nslug: X\nkind: entity\n---\n\nX 内容", encoding="utf-8"
    )

    with TestClient(app) as client:
        with session_scope() as db:
            db.add(
                WikiPage(
                    slug="A", title="实体 A", kind="entity",
                    file_path="wiki/entities/A.md",
                    frontmatter_json="{}", version=1,
                )
            )
            db.add(
                WikiPage(
                    slug="B", title="主题 B", kind="topic",
                    file_path="wiki/topics/B.md",
                    frontmatter_json="{}", version=1,
                )
            )
            db.add(
                WikiLink(
                    src_slug="A", dst_slug="X", link_type="实现",
                    occurrences=3, confidence="EXTRACTED",
                )
            )
            db.add(
                WikiLink(
                    src_slug="B", dst_slug="X", link_type="related",
                    occurrences=1, confidence="EXTRACTED",
                )
            )

        resp = client.get("/api/v1/wiki/pages/X/backlinks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        slugs = {b["src_slug"] for b in data["backlinks"]}
        assert slugs == {"A", "B"}
        a_item = next(b for b in data["backlinks"] if b["src_slug"] == "A")
        assert a_item["link_type"] == "实现"
        assert a_item["occurrences"] == 3
        assert a_item["src_title"] == "实体 A"
        assert a_item["src_kind"] == "entity"


def test_backlinks_api_falls_back_to_filesystem_scan(wiki_root):
    """wiki_links 表为空时降级到全文扫描。"""
    (wiki_root / "wiki" / "entities" / "P.md").write_text(
        "---\ntitle: P\nslug: P\nkind: entity\n---\n\n指向 [[Q]]", encoding="utf-8"
    )
    (wiki_root / "wiki" / "entities" / "Q.md").write_text(
        "---\ntitle: Q\nslug: Q\nkind: entity\n---\n\nQ 内容", encoding="utf-8"
    )

    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/pages/Q/backlinks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["backlinks"][0]["src_slug"] == "P"


# ===========================================================================
# 节点版本历史 API
# ===========================================================================


def test_versions_list_and_detail_api(wiki_root):
    from app.main import app

    (wiki_root / "wiki" / "entities" / "VV.md").write_text(
        "---\ntitle: VV\nslug: VV\nkind: entity\nversion: 1\n---\n\n初始内容",
        encoding="utf-8",
    )

    with TestClient(app) as client:
        # PUT bump version 一次 → 写一条版本历史
        resp = client.put(
            "/api/v1/wiki/pages/VV",
            json={"body": "新内容 [[X]]", "frontmatter": {"title": "VV"}, "bump_version": True},
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == 2

        # 列版本
        resp = client.get("/api/v1/wiki/pages/VV/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        v2 = next((v for v in data["versions"] if v["version"] == 2), None)
        assert v2 is not None
        assert v2["triggered_by"] == "manual"
        assert v2["title_snapshot"] == "VV"
        assert "added_lines" in v2["diff_stat"]

        # 单版本 detail
        resp = client.get("/api/v1/wiki/pages/VV/versions/2")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["version"] == 2
        assert "frontmatter" in detail["diff"]
        assert "body" in detail["diff"]


def test_versions_404(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/pages/no-page/versions/1")
        assert resp.status_code == 404


# ===========================================================================
# crystallize
# ===========================================================================


class _FakeLLMSimple:
    name = "fake"

    def __init__(self, response: str):
        self._response = response
        self.calls = 0

    async def chat(self, messages, *, stream=False, **params):
        self.calls += 1
        if stream:
            async def _gen():
                yield self._response
            return _gen()
        return self._response

    async def test_connection(self):
        return True, "ok"


def test_crystallize_creates_synthesis_page(wiki_root, monkeypatch):
    """LLM 返回完整 synthesis md → 写入 wiki/synthesis/ + 引用未建实体入 gap_queue。"""
    fake_md = """---
title: 精益生产笔记
kind: synthesis
tags: [精益, 工厂]
domains: [精益生产]
sources: [user_input]
version: 1
---

## 核心观点

- 精益的核心是消除浪费(出处:用户原文)
- [[VSM]] 是关键工具
- [[尚未建立的概念]] 也很重要

## 衍生问题

- 如何度量浪费?
"""
    fake = _FakeLLMSimple(fake_md)
    import app.services.crystallize as crystallize_mod

    monkeypatch.setattr(crystallize_mod, "get_llm_provider", lambda: fake)

    from app.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/crystallize",
            json={"text": "我对精益生产的零散笔记,VSM 很重要", "kind": "synthesis"},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        slug = data["slug"]
        assert "精益生产笔记" in data["title"]
        # 落盘
        page = wiki_root / "wiki" / "synthesis" / f"{slug}.md"
        assert page.exists()
        # 候选合并:VSM + 尚未建立的概念 都未建页
        assert "VSM" in data["candidates_added"]
        assert "尚未建立的概念" in data["candidates_added"]

        # gap_queue 有对应行
        with session_scope() as db:
            from app.models.gap_queue import GapQueue
            rows = (
                db.query(GapQueue)
                .filter(GapQueue.origin == "merge_suggestion")
                .all()
            )
            seeds = []
            for r in rows:
                seeds.extend(json.loads(r.suggested_seeds_json))
            assert "VSM" in seeds


def test_crystallize_rejects_short_text(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/crystallize",
            json={"text": "短", "kind": "synthesis"},
        )
        # pydantic min_length=10 → 422
        assert resp.status_code == 422


def test_crystallize_writes_node_version(wiki_root, monkeypatch):
    fake_md = """---
title: 笔记标题
kind: synthesis
tags: []
sources: [user_input]
version: 1
---

## 核心观点

- 一些观点
"""
    fake = _FakeLLMSimple(fake_md)
    import app.services.crystallize as crystallize_mod

    monkeypatch.setattr(crystallize_mod, "get_llm_provider", lambda: fake)

    from app.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/crystallize",
            json={"text": "用户的零散笔记,值得保留" * 2, "kind": "synthesis"},
        )
        assert resp.status_code == 201
        slug = resp.json()["slug"]

        # versions 端点应该能查到一条 triggered_by=crystallize
        resp = client.get(f"/api/v1/wiki/pages/{slug}/versions")
        data = resp.json()
        assert data["total"] >= 1
        assert data["versions"][0]["triggered_by"] == "crystallize"


# ===========================================================================
# batch ingest 状态机端到端
# ===========================================================================


def test_batch_ingest_pauses_and_continues(wiki_root, monkeypatch):
    from app.main import app
    from app.models.document import Document
    from app.models.user import User
    from app.services.ingestion import pipeline as pipeline_mod

    # mock pipeline.run_pipeline 立即成功
    async def _fake(*, document_id, force=False):
        return {"final_status": "succeeded", "source_slug": f"src{document_id}", "jobs": []}

    monkeypatch.setattr(pipeline_mod, "run_pipeline", _fake)

    # seed admin + 5 documents
    with session_scope() as db:
        u = User(username="abc_admin", email="a@b.c", password_hash="x", role="admin", status="active")
        db.add(u)
        db.flush()
        owner_id = u.id
        for i in range(5):
            db.add(
                Document(
                    source_type="md",
                    file_path=f"/tmp/doc{i}.md",
                    original_filename=f"d{i}.md",
                    content_hash=f"h{i}",
                    size_bytes=10,
                    parse_status="pending",
                    owner_id=owner_id,
                    title=f"d{i}",
                    tags_json="[]",
                )
            )

    # 拿这 5 条的 id
    with session_scope() as db:
        docs = db.query(Document).order_by(Document.id.asc()).all()
        doc_ids = [d.id for d in docs]

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/ingestion/batch",
            json={"document_ids": doc_ids, "pause_every": 2},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        batch_id = data["batch_id"]
        assert data["status"] == "paused"
        assert data["next_index"] == 2
        assert data["total"] == 5
        assert data["next_pause_at"] == 4

        # 继续一段
        resp = client.post(f"/api/v1/ingestion/batch/{batch_id}/continue")
        data = resp.json()
        assert data["status"] == "paused"
        assert data["next_index"] == 4

        # 再继续 → completed
        resp = client.post(f"/api/v1/ingestion/batch/{batch_id}/continue")
        data = resp.json()
        assert data["status"] == "completed"
        assert data["next_index"] == 5
        assert data["completed_at"] is not None

        # GET state
        resp = client.get(f"/api/v1/ingestion/batch/{batch_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

        # list
        resp = client.get("/api/v1/ingestion/batches")
        assert resp.status_code == 200
        assert any(b["batch_id"] == batch_id for b in resp.json())


def test_batch_ingest_404_for_missing_doc(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/ingestion/batch",
            json={"document_ids": [99999], "pause_every": 5},
        )
        assert resp.status_code == 404


def test_batch_continue_404(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.post("/api/v1/ingestion/batch/no-batch/continue")
        assert resp.status_code == 404


def test_batch_cancel_changes_status(wiki_root, monkeypatch):
    from app.main import app
    from app.services.ingestion import batch_state as bs_mod

    bid = bs_mod.start_batch(document_ids=[1, 2, 3], pause_every=1)

    with TestClient(app) as client:
        resp = client.post(f"/api/v1/ingestion/batch/{bid}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"
