"""graph builder / insights / neo4j_client(mock)单元测试。"""

from __future__ import annotations

import json
import types

import pytest

from app.services.graph import builder as builder_mod
from app.services.graph.builder import (
    build_graph_data,
    load_graph_data_json,
    write_graph_data_json,
)
from app.services.graph.insights import (
    find_bridge_nodes,
    find_knowledge_gaps,
    find_sparse_communities,
    find_surprising_links,
)
from app.services.graph import neo4j_client as neo4j_mod
from app.services.graph.neo4j_client import (
    Neo4jClient,
    _safe_label,
    _safe_rel_type,
    sync_graph_to_neo4j,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed(root, files: dict[str, str]):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_graph_data_minimum(tmp_path):
    _seed(
        tmp_path,
        {
            "wiki/entities/A.md": (
                "---\ntitle: A\nslug: A\nkind: entity\nsources: [s1]\n---\n\n## 简介\n\n[[B]] <!-- type: 实现 -->\n"
            ),
            "wiki/entities/B.md": (
                "---\ntitle: B\nslug: B\nkind: entity\nsources: [s1]\n---\n\n## 简介\n\nB 内容。\n"
            ),
            "wiki/topics/T1.md": (
                "---\ntitle: T1\nslug: T1\nkind: topic\nsources: [s1]\n---\n\n[[A]]\n"
            ),
        },
    )
    g = await build_graph_data(wiki_root=tmp_path)
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"entity:A", "entity:B", "topic:T1"}
    # slug 字段保留原值,前端跳 wiki 用
    assert {n.get("slug") for n in g["nodes"]} == {"A", "B", "T1"}

    edge_pairs = {(e["source"], e["target"]) for e in g["edges"]}
    assert ("entity:A", "entity:B") in edge_pairs
    assert ("topic:T1", "entity:A") in edge_pairs

    a = next(n for n in g["nodes"] if n["id"] == "entity:A")
    # A 度数 = 来自 T1 的入边 + 指向 B 的出边 = 2
    assert a["degree"] == 2

    # A→B 边 link_type=实现 → type_affinity=1.0
    ab = next(e for e in g["edges"] if e["source"] == "entity:A" and e["target"] == "entity:B")
    assert ab["type"] == "实现"
    assert ab["weight"] > 0


@pytest.mark.asyncio
async def test_build_graph_data_assigns_communities(tmp_path):
    """两簇互不连通的子图应分到不同社区。"""
    _seed(
        tmp_path,
        {
            "wiki/entities/A.md": "---\ntitle: A\nslug: A\nkind: entity\n---\n\n[[B]]\n",
            "wiki/entities/B.md": "---\ntitle: B\nslug: B\nkind: entity\n---\n\n[[A]]\n",
            "wiki/entities/X.md": "---\ntitle: X\nslug: X\nkind: entity\n---\n\n[[Y]]\n",
            "wiki/entities/Y.md": "---\ntitle: Y\nslug: Y\nkind: entity\n---\n\n[[X]]\n",
        },
    )
    g = await build_graph_data(wiki_root=tmp_path)
    inv = {n["id"]: n["community_id"] for n in g["nodes"]}
    assert inv["entity:A"] == inv["entity:B"]
    assert inv["entity:X"] == inv["entity:Y"]
    assert inv["entity:A"] != inv["entity:X"]


@pytest.mark.asyncio
async def test_write_and_load_graph_data_json(tmp_path):
    payload = {"nodes": [{"id": "A"}], "edges": [], "communities": {"0": ["A"]}, "generated_at": "2026-05-19"}
    p = await write_graph_data_json(wiki_root=tmp_path, payload=payload)
    assert p.exists()
    loaded = await load_graph_data_json(wiki_root=tmp_path)
    assert loaded == payload


@pytest.mark.asyncio
async def test_load_graph_data_json_missing_returns_none(tmp_path):
    out = await load_graph_data_json(wiki_root=tmp_path)
    assert out is None


@pytest.mark.asyncio
async def test_load_graph_data_json_corrupt_returns_none(tmp_path):
    (tmp_path / "graph-data.json").write_text("not json", encoding="utf-8")
    out = await load_graph_data_json(wiki_root=tmp_path)
    assert out is None


# ---------------------------------------------------------------------------
# insights
# ---------------------------------------------------------------------------


def _two_community_graph() -> dict:
    return {
        "nodes": [
            {"id": "A", "kind": "entity", "degree": 3, "community_id": 0},
            {"id": "B", "kind": "entity", "degree": 3, "community_id": 0},
            {"id": "C", "kind": "entity", "degree": 2, "community_id": 1},
            {"id": "D", "kind": "entity", "degree": 1, "community_id": 1},
            {"id": "E", "kind": "entity", "degree": 0, "community_id": 2},
        ],
        "edges": [
            {"source": "A", "target": "B", "weight": 0.4, "type": "实现"},
            {"source": "C", "target": "D", "weight": 0.3, "type": "依赖"},
            {"source": "B", "target": "C", "weight": 0.8, "type": "对比"},  # 跨社区高权重
        ],
        "communities": {0: ["A", "B"], 1: ["C", "D"], 2: ["E"]},
    }


def test_find_surprising_links():
    g = _two_community_graph()
    out = find_surprising_links(g)
    assert any(i["related_slugs"] == ["B", "C"] for i in out)
    # weight 低的不该上榜
    assert not any(set(i["related_slugs"]) == {"A", "B"} for i in out)


def test_find_bridge_nodes():
    """连接 ≥ 3 个外社区的节点才算桥节点(默认 min_communities=3)。"""
    g = {
        "nodes": [
            {"id": s, "kind": "entity", "degree": 5, "community_id": cid}
            for s, cid in [("HUB", 0), ("A", 1), ("B", 2), ("C", 3)]
        ],
        "edges": [
            {"source": "HUB", "target": "A", "weight": 0.5, "type": "实现"},
            {"source": "HUB", "target": "B", "weight": 0.5, "type": "实现"},
            {"source": "HUB", "target": "C", "weight": 0.5, "type": "实现"},
        ],
        "communities": {0: ["HUB"], 1: ["A"], 2: ["B"], 3: ["C"]},
    }
    out = find_bridge_nodes(g)
    assert any("HUB" in i["related_slugs"] for i in out)


def test_find_knowledge_gaps():
    g = _two_community_graph()
    gaps = find_knowledge_gaps(g)
    slugs = {i["related_slugs"][0] for i in gaps}
    assert "D" in slugs   # degree=1
    assert "E" in slugs   # degree=0
    # 度数高的不算 gap
    assert "A" not in slugs


def test_find_sparse_communities():
    g = _two_community_graph()
    out = find_sparse_communities(g, min_size=3)
    related = {tuple(i["related_slugs"]) for i in out}
    # 社区 0 / 1 / 2 都 < 3 节点
    assert ("A", "B") in related or ("B", "A") in related or any(set(r) == {"A", "B"} for r in related)


# ---------------------------------------------------------------------------
# Neo4j client(mock)
# ---------------------------------------------------------------------------


def test_safe_label_whitelist():
    assert _safe_label("Entity")
    assert _safe_label("WikiNode")
    assert not _safe_label("DROP TABLE; --")


def test_safe_rel_type_maps_chinese():
    assert _safe_rel_type("实现") == "IMPLEMENTS"
    assert _safe_rel_type("依赖") == "DEPENDS_ON"
    assert _safe_rel_type("对比") == "COMPARES_WITH"
    assert _safe_rel_type("矛盾") == "CONTRADICTS"
    assert _safe_rel_type("衍生") == "DERIVED_FROM"
    assert _safe_rel_type(None) == "RELATED"
    # 未知类型一律降级 RELATED(防注入)
    assert _safe_rel_type("DROP TABLE") == "RELATED"


@pytest.mark.asyncio
async def test_neo4j_client_connect_failure_returns_false(monkeypatch):
    """driver verify_connectivity 抛错 → connect() 返回 False,后续操作均降级。"""

    class _BadDriver:
        async def verify_connectivity(self):
            raise RuntimeError("connection refused")

        async def close(self):
            pass

    class _FakeAsyncGraphDatabase:
        @staticmethod
        def driver(uri, auth):
            return _BadDriver()

    fake_neo4j = types.SimpleNamespace(AsyncGraphDatabase=_FakeAsyncGraphDatabase)
    monkeypatch.setitem(__import__("sys").modules, "neo4j", fake_neo4j)

    c = Neo4jClient(uri="bolt://x", user="u", password="p")
    ok = await c.connect()
    assert ok is False
    assert c.connected is False

    # health 与 upsert_* 均不抛
    h = await c.health()
    assert h["ok"] is False
    assert (await c.upsert_node(slug="x", label="Entity", props={})) is False
    assert (await c.upsert_edge(src_slug="a", dst_slug="b", rel_type="实现")) is False


@pytest.mark.asyncio
async def test_sync_graph_to_neo4j_returns_failure_on_connect_fail(monkeypatch):
    neo4j_mod.reset_for_tests()

    async def _bad_connect(self):
        return False

    monkeypatch.setattr(Neo4jClient, "connect", _bad_connect)
    out = await sync_graph_to_neo4j({"nodes": [], "edges": []})
    assert out["ok"] is False
    assert out["nodes_synced"] == 0
    assert out["edges_synced"] == 0
    neo4j_mod.reset_for_tests()


@pytest.mark.asyncio
async def test_sync_graph_to_neo4j_counts_successes(monkeypatch):
    """connect 成功 + upsert 总成功 → 计数正确。"""
    neo4j_mod.reset_for_tests()

    async def _ok_connect(self):
        self._connected = True
        self._driver = object()  # 占位
        return True

    async def _ok_upsert_node(self, *, slug, label, props):
        return True

    async def _ok_upsert_edge(self, *, src_slug, dst_slug, rel_type, props=None):
        return True

    monkeypatch.setattr(Neo4jClient, "connect", _ok_connect)
    monkeypatch.setattr(Neo4jClient, "upsert_node", _ok_upsert_node)
    monkeypatch.setattr(Neo4jClient, "upsert_edge", _ok_upsert_edge)

    g = {
        "nodes": [
            {"id": "A", "kind": "entity", "title": "A"},
            {"id": "B", "kind": "topic", "title": "B"},
        ],
        "edges": [{"source": "A", "target": "B", "type": "实现", "weight": 0.5}],
    }
    out = await sync_graph_to_neo4j(g)
    assert out["ok"] is True
    assert out["nodes_synced"] == 2
    assert out["edges_synced"] == 1
    neo4j_mod.reset_for_tests()
