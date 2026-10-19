"""graph API 集成测试。

通过 TestClient 跑 /graph/rebuild + /graph/data + /graph/insights。
Neo4j 同步 fail-safe(连不上时静默忽略),不 mock。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import config as cfg_module
from app.services.graph import neo4j_client as neo4j_mod
from app.services.graph.neo4j_client import Neo4jClient


@pytest.fixture
def wiki_root(tmp_path, monkeypatch):
    from app.services.wiki.init import init_wiki

    root = tmp_path / "wiki_root"
    init_wiki(root)
    # seed 一个最小图:工效学 → 作业测定;主题 → 工效学
    (root / "wiki" / "entities" / "工效学.md").write_text(
        "---\ntitle: 工效学\nslug: 工效学\nkind: entity\nsources: [s1]\n---\n\n[[作业测定]] <!-- type: 实现 -->\n",
        encoding="utf-8",
    )
    (root / "wiki" / "entities" / "作业测定.md").write_text(
        "---\ntitle: 作业测定\nslug: 作业测定\nkind: entity\nsources: [s1]\n---\n\n## 简介\n\n衡量单位作业时间。\n",
        encoding="utf-8",
    )
    (root / "wiki" / "topics" / "T1.md").write_text(
        "---\ntitle: T1\nslug: T1\nkind: topic\nsources: [s1]\n---\n\n## 概要\n\n[[工效学]] 是基础。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg_module.settings, "wiki_root", str(root))
    # Neo4j 不接真实例 — connect 必返 False
    neo4j_mod.reset_for_tests()
    monkeypatch.setattr(Neo4jClient, "connect", _bad_connect)
    return root


async def _bad_connect(self):
    return False


def test_graph_rebuild_then_data_and_insights(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        # 重建
        resp = client.post("/api/v1/graph/rebuild")
        assert resp.status_code == 200
        body = resp.json()
        # 三页 seed + init_wiki 自带的 _growth/gap-queue.md 也会被算节点
        assert body["total_nodes"] >= 3
        assert body["total_edges"] >= 2
        assert body["job_id"].startswith("graph-rebuild-")

        # data
        resp = client.get("/api/v1/graph/data")
        assert resp.status_code == 200
        data = resp.json()
        slugs = {n["id"] for n in data["nodes"]}
        assert {"entity:工效学", "entity:作业测定", "topic:T1"}.issubset(slugs)
        # 边都至少有 weight
        assert all("weight" in e for e in data["edges"])
        # 必有 generated_at
        assert data["generated_at"]

        # insights
        resp = client.get("/api/v1/graph/insights")
        assert resp.status_code == 200
        insights = resp.json()
        # 这个图很小,sparse_communities / knowledge_gaps 可能命中
        # 但至少不应抛异常
        assert isinstance(insights, list)

        # 持久化文件确实落盘
        graph_json = wiki_root / "graph-data.json"
        assert graph_json.exists()


def test_graph_data_auto_builds_when_missing(wiki_root):
    """没跑过 rebuild,直接 GET /graph/data 应自动构建。"""
    from app.main import app

    assert not (wiki_root / "graph-data.json").exists()
    with TestClient(app) as client:
        resp = client.get("/api/v1/graph/data")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"]
    assert (wiki_root / "graph-data.json").exists()


def test_graph_insights_returns_empty_when_no_file(tmp_path, monkeypatch):
    """graph-data.json 完全不存在时 insights 返回空列表(不会触发 build)。"""
    monkeypatch.setattr(cfg_module.settings, "wiki_root", str(tmp_path))
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/graph/insights")
        assert resp.status_code == 200
        assert resp.json() == []
