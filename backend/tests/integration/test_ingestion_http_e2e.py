"""HTTP 端到端测试:模拟前端 Sources.vue 的真实交互路径。

覆盖:
  1. POST /ingestion/upload → 201 + document_id
  2. POST /ingestion/{id}/run → 202 立即返回(异步)
  3. GET /ingestion/documents/{id}/progress 轮询直到 succeeded → source_slug 可用
  4. GET /wiki/pages/{slug} → 能拿到产出的 source 页
  5. 故意让 LLM 抛错 → progress 转 failed + jobs/{id}/detail 返回 traceback
  6. cache 命中再上传一份相同内容 → progress 也走到 succeeded(不能卡 running)
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.core import config as cfg_module
from app.core.db import session_scope
from app.models.user import User
from app.services.ingestion import merger as merger_mod
from app.services.ingestion import step1_extract as step1_mod
from app.services.ingestion import step2_compose as step2_mod
from app.services.llm.base import LLMProvider


# ---------------------------------------------------------------------------
# fixtures(对齐 test_pipeline.py 的写法)
# ---------------------------------------------------------------------------


@pytest.fixture
def wiki_root(tmp_path, monkeypatch):
    from app.services.wiki.init import init_wiki

    root = tmp_path / "wr"
    init_wiki(root)
    monkeypatch.setattr(cfg_module.settings, "wiki_root", str(root))
    monkeypatch.setattr(cfg_module.settings, "cache_dir", str(tmp_path / "cache"))
    return root


@pytest.fixture
def admin_user():
    with session_scope() as db:
        u = User(username="admin_e2e", email="admin@e2e", password_hash="x", role="admin", status="active", display_name="admin")
        db.add(u)
        db.flush()
        return u.id


class _FakeLLM(LLMProvider):
    name = "fake"

    def __init__(self, responses: list[str]):
        super().__init__(model="fake")
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, messages, *, stream=False, **params):
        self.calls += 1
        if not self._responses:
            return ""
        text = self._responses.pop(0)
        if stream:
            async def _gen() -> AsyncIterator[str]:
                yield text
            return _gen()
        return text

    async def test_connection(self):
        return True, "ok"


class _FailingLLM(LLMProvider):
    name = "failing"

    def __init__(self):
        super().__init__(model="failing")

    async def chat(self, messages, *, stream=False, **params):
        raise RuntimeError("LLM 故意挂了用于测试 traceback")

    async def test_connection(self):
        return False, "fail"


class _FakeEmbedding:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0]] + [[0.5, 0.5] for _ in texts[1:]]


def _step1_resp() -> str:
    return json.dumps({
        "source_summary": {
            "title": "工效学第三章笔记",
            "kind": "note",
            "domain_tags": ["人因工程"],
            "key_takeaways": ["工效学关注人机交互", "包括人体测量与作业测定", "实施有量化指标"],
        },
        "entities": [{
            "name": "工效学",
            "kind": "concept",
            "brief": "研究人在工作中的效率与舒适的学科。",
            "aliases": ["人因工程"],
            "evidence": "原文",
            "confidence": "EXTRACTED",
            "domain_tags": ["人因工程"],
        }],
        "topics": [{
            "name": "工效学的研究范畴",
            "scope": "限于作业测定与人体测量",
            "related_entities": ["工效学"],
            "evidence": "原文",
            "confidence": "INFERRED",
        }],
        "connections": [],
        "contradictions": [],
        "new_vs_existing": {
            "new_entity_names": ["工效学"],
            "matched_entity_pages": [],
            "new_topic_names": ["工效学的研究范畴"],
            "matched_topic_pages": [],
        },
    }, ensure_ascii=False)


def _step2_resp(source_slug: str = "2026-05-20-工效学第三章笔记") -> str:
    return f"""=====<file: wiki/sources/{source_slug}.md>=====

---
title: 工效学第三章笔记
slug: {source_slug}
kind: source
source_type: article
content_hash: "deadbeef"
ingested_at: 2026-05-20T00:00:00+00:00
version: 1
---

## 摘要

笔记摘要:工效学与作业测定。

=====<file: wiki/entities/工效学.md>=====

---
title: 工效学
slug: 工效学
kind: entity
aliases: ["人因工程"]
domains: ["人因工程"]
sources: ["{source_slug}"]
version: 1
---

## 简介

研究人在工作中的效率与舒适的学科。

=====<file: wiki/topics/工效学的研究范畴.md>=====

---
title: 工效学的研究范畴
slug: 工效学的研究范畴
kind: topic
domains: ["人因工程"]
sources: ["{source_slug}"]
version: 1
---

## 主题概要

工效学覆盖人体测量与作业测定。
"""


def _patch_llm(monkeypatch, *, step1: str, step2: str):
    fake = _FakeLLM([step1, step2])
    monkeypatch.setattr(step1_mod, "get_llm_provider", lambda: fake)
    monkeypatch.setattr(step2_mod, "get_llm_provider", lambda: fake)
    monkeypatch.setattr(merger_mod, "get_embedding_provider", lambda: _FakeEmbedding())
    return fake


def _patch_failing_llm(monkeypatch):
    fail = _FailingLLM()
    monkeypatch.setattr(step1_mod, "get_llm_provider", lambda: fail)
    monkeypatch.setattr(step2_mod, "get_llm_provider", lambda: fail)
    monkeypatch.setattr(merger_mod, "get_embedding_provider", lambda: _FakeEmbedding())


# ---------------------------------------------------------------------------
# 工具:轮询 progress 直到终态(succeeded / failed)
# ---------------------------------------------------------------------------


def _wait_terminal(client: TestClient, doc_id: int, timeout_s: float = 10.0) -> dict:
    """模拟前端 1.5s 轮询 progress;实际测试里加快到 50ms。"""
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/ingestion/documents/{doc_id}/progress")
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["parse_status"] in ("succeeded", "failed"):
            return last
        time.sleep(0.05)
    pytest.fail(f"progress did not reach terminal in {timeout_s}s; last={last}")


# ===========================================================================
# 测试用例
# ===========================================================================


def test_e2e_upload_run_progress_succeeds(wiki_root, admin_user, monkeypatch):
    """前端真实交互路径:upload → run(202) → 轮询 progress → succeeded → source 页可读。"""
    _patch_llm(monkeypatch, step1=_step1_resp(), step2=_step2_resp())
    from app.main import app

    raw = "工效学是研究人在工作中效率的学科。\n\n包含人体测量与作业测定。"
    with TestClient(app) as client:
        # 1) upload
        r = client.post(
            "/api/v1/ingestion/upload",
            files={"file": ("工效学笔记.md", raw.encode("utf-8"), "text/markdown")},
            params={"source_type": "md"},
        )
        assert r.status_code == 201, r.text
        doc_id = r.json()["document_id"]

        # 2) run → 必须 202(异步)且 status=queued
        r = client.post(f"/api/v1/ingestion/{doc_id}/run", json={})
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "queued"

        # 3) 轮询 progress 直到 succeeded
        final = _wait_terminal(client, doc_id)
        assert final["parse_status"] == "succeeded", final
        assert final["progress"] == 100
        assert final["stages_done"] == 4
        assert final["source_slug"], final
        assert final["failed_job_id"] is None

        # 4) wiki/pages/{slug} 能拿到 source 页
        r = client.get(f"/api/v1/wiki/pages/{final['source_slug']}")
        assert r.status_code == 200, r.text
        page = r.json()
        assert page["frontmatter"]["kind"] == "source"

        # 5) 实体页也产出了
        r = client.get("/api/v1/wiki/pages/工效学")
        assert r.status_code == 200, r.text


def test_e2e_failed_run_exposes_traceback(wiki_root, admin_user, monkeypatch):
    """LLM 故意抛错 → progress=failed + jobs/{id}/detail 返回 traceback + exception_type。"""
    _patch_failing_llm(monkeypatch)
    from app.main import app

    with TestClient(app) as client:
        r = client.post(
            "/api/v1/ingestion/upload",
            files={"file": ("x.md", b"content for failing run", "text/markdown")},
            params={"source_type": "md"},
        )
        assert r.status_code == 201
        doc_id = r.json()["document_id"]

        r = client.post(f"/api/v1/ingestion/{doc_id}/run", json={})
        assert r.status_code == 202

        final = _wait_terminal(client, doc_id)
        assert final["parse_status"] == "failed", final
        assert final["failed_job_id"] is not None
        assert final["current_stage"] in ("step1_extract", "step2_compose"), final

        r = client.get(f"/api/v1/ingestion/jobs/{final['failed_job_id']}/detail")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "failed"
        assert d["exception_type"] == "RuntimeError", d
        assert d["traceback"], "traceback should be persisted to payload_json"
        assert "LLM 故意挂了用于测试 traceback" in d["traceback"]


def test_e2e_cache_hit_does_not_stick_running(wiki_root, admin_user, monkeypatch):
    """同一份内容上传两次:第二次 cache 命中 → progress 也必须能到 succeeded(否则前端轮询永远卡 running)。

    这是回归测试 — 当前 pipeline.py:524 cache_hit 提前 return 时没改 doc.parse_status。
    """
    _patch_llm(monkeypatch, step1=_step1_resp(), step2=_step2_resp())
    from app.main import app

    raw = "工效学缓存命中测试 — 唯一内容确保 hash 稳定。\n\n人体测量。"
    with TestClient(app) as client:
        # 第一次:正常跑完
        r = client.post(
            "/api/v1/ingestion/upload",
            files={"file": ("a.md", raw.encode("utf-8"), "text/markdown")},
            params={"source_type": "md"},
        )
        doc1 = r.json()["document_id"]
        client.post(f"/api/v1/ingestion/{doc1}/run", json={})
        first = _wait_terminal(client, doc1)
        assert first["parse_status"] == "succeeded"

        # 第二次:同样的内容 → hash 命中
        r = client.post(
            "/api/v1/ingestion/upload",
            files={"file": ("b.md", raw.encode("utf-8"), "text/markdown")},
            params={"source_type": "md"},
        )
        doc2 = r.json()["document_id"]
        client.post(f"/api/v1/ingestion/{doc2}/run", json={})

        second = _wait_terminal(client, doc2, timeout_s=5.0)
        # 关键断言:cache 命中也必须把 doc.parse_status 推到终态,不能让前端轮询永远 running
        assert second["parse_status"] in ("succeeded",), (
            f"cache 命中后 doc.parse_status 应到 succeeded,实际:{second}"
        )
        assert second["source_slug"], second  # cache record 中应能取到 source_slug


def test_progress_404_for_missing_doc(wiki_root, admin_user):
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/v1/ingestion/documents/99999/progress")
        assert r.status_code == 404


def test_jobs_detail_404_for_missing_job(wiki_root, admin_user):
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/v1/ingestion/jobs/99999/detail")
        assert r.status_code == 404


@pytest.mark.parametrize(
    "source_type,expected_raw_dir",
    [
        ("md", "articles"),
        ("web", "articles"),
        ("pdf", "pdfs"),
        ("ppt", "articles"),
        ("tweet", "tweets"),
        ("wechat", "wechat"),
        ("zhihu", "zhihu"),
        ("xiaohongshu", "xiaohongshu"),
        ("plain_text", "plain_text"),
    ],
)
def test_source_type_routes_to_correct_raw_subdir(
    wiki_root, admin_user, source_type, expected_raw_dir
):
    """前端 select 9 种 source_type 都要路由到 init.py 真正建的 raw/ 子目录。

    回归 bug:之前 _VALID_RAW_DIRS 含 'pdfs' 不含 'pdf','md' 被映射到 'md'(不存在),
    web/ppt 全降级到 plain_text。
    """
    from app.main import app

    with TestClient(app) as client:
        r = client.post(
            "/api/v1/ingestion/upload",
            files={"file": (f"x_{source_type}.md", b"# test", "text/markdown")},
            params={"source_type": source_type},
        )
        assert r.status_code == 201, r.text
        file_path = r.json()["file_path"].replace("\\", "/")
        assert f"/raw/{expected_raw_dir}/" in file_path, (
            f"source_type={source_type} 应该路由到 raw/{expected_raw_dir}/,实际:{file_path}"
        )
