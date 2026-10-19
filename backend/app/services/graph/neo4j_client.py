"""graph/neo4j_client.py:Neo4j 异步驱动封装。

设计:
  - 单例 driver(由 get_neo4j_client() 工厂取),延迟连接
  - 失败时不抛异常,返回 None / False;主问答不依赖 Neo4j
  - upsert_node / upsert_edge 用 MERGE Cypher,幂等
  - sync_from_links_index:把 wiki_links 全量同步到 Neo4j(供 P3+ Neo4j GDS 使用)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from loguru import logger

from app.core.config import settings


class Neo4jClient:
    def __init__(self, uri: str, user: str, password: str):
        self.uri = uri
        self.user = user
        self.password = password
        self._driver: Any = None
        self._connected: bool = False

    async def connect(self) -> bool:
        """连接 Neo4j。失败时返回 False(不抛异常)。"""
        try:
            from neo4j import AsyncGraphDatabase
        except ImportError:  # pragma: no cover
            logger.warning("neo4j driver 未安装")
            return False
        try:
            self._driver = AsyncGraphDatabase.driver(
                self.uri, auth=(self.user, self.password)
            )
            await self._driver.verify_connectivity()
            self._connected = True
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Neo4j connect failed: {e}")
            self._connected = False
            self._driver = None
            return False

    async def close(self) -> None:
        if self._driver is not None:
            try:
                await self._driver.close()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Neo4j close error: {e}")
            self._driver = None
            self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self._driver is not None

    @asynccontextmanager
    async def session(self):
        if not self.connected:
            ok = await self.connect()
            if not ok:
                yield None
                return
        async with self._driver.session() as s:  # type: ignore[union-attr]
            yield s

    async def health(self) -> dict:
        """连通性检查。"""
        async with self.session() as s:
            if s is None:
                return {"ok": False, "error": "no connection"}
            try:
                result = await s.run("RETURN 1 AS ok")
                row = await result.single()
                return {"ok": bool(row and row["ok"] == 1)}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}

    async def upsert_node(self, *, slug: str, label: str, props: dict) -> bool:
        """MERGE 节点。label ∈ Entity/Topic/Source/Query/Synthesis/Comparison。"""
        if not _safe_label(label):
            return False
        async with self.session() as s:
            if s is None:
                return False
            try:
                cypher = (
                    f"MERGE (n:{label} {{slug: $slug}}) "
                    "SET n += $props "
                    "RETURN n.slug AS slug"
                )
                await s.run(cypher, slug=slug, props=props)
                return True
            except Exception as e:  # noqa: BLE001
                logger.warning(f"upsert_node({slug}) failed: {e}")
                return False

    async def upsert_edge(
        self,
        *,
        src_slug: str,
        dst_slug: str,
        rel_type: str,
        props: dict | None = None,
    ) -> bool:
        """MERGE 边。两端节点缺失时自动 MERGE 一个未分类的占位节点(label = WikiNode)。

        Cypher 中关系类型不能参数化,因此对 rel_type 做白名单 sanitization。
        """
        rel = _safe_rel_type(rel_type)
        async with self.session() as s:
            if s is None:
                return False
            try:
                cypher = (
                    "MERGE (a {slug: $src}) "
                    "ON CREATE SET a:WikiNode "
                    "MERGE (b {slug: $dst}) "
                    "ON CREATE SET b:WikiNode "
                    f"MERGE (a)-[r:{rel}]->(b) "
                    "SET r += $props "
                    "RETURN a.slug, b.slug"
                )
                await s.run(cypher, src=src_slug, dst=dst_slug, props=dict(props or {}))
                return True
            except Exception as e:  # noqa: BLE001
                logger.warning(f"upsert_edge({src_slug}->{dst_slug}) failed: {e}")
                return False

    async def delete_node(self, slug: str) -> bool:
        async with self.session() as s:
            if s is None:
                return False
            try:
                await s.run("MATCH (n {slug: $slug}) DETACH DELETE n", slug=slug)
                return True
            except Exception as e:  # noqa: BLE001
                logger.warning(f"delete_node({slug}) failed: {e}")
                return False

    async def delete_edges_for_source(self, src_slug: str) -> int:
        """删除 src_slug 所有出边。返回删除条数(失败时 0)。"""
        async with self.session() as s:
            if s is None:
                return 0
            try:
                result = await s.run(
                    "MATCH (n {slug: $slug})-[r]->() DELETE r RETURN count(r) AS cnt",
                    slug=src_slug,
                )
                row = await result.single()
                return int(row["cnt"]) if row else 0
            except Exception as e:  # noqa: BLE001
                logger.warning(f"delete_edges_for_source({src_slug}) failed: {e}")
                return 0


_LABEL_WHITELIST = {
    "Entity", "Topic", "Source", "Query", "Synthesis", "Comparison", "WikiNode"
}
# Cypher 关系类型限白名单,其他降级为 RELATED;命名沿用 schema 关系词汇表。
_REL_WHITELIST_MAP = {
    "实现": "IMPLEMENTS",
    "依赖": "DEPENDS_ON",
    "对比": "COMPARES_WITH",
    "矛盾": "CONTRADICTS",
    "衍生": "DERIVED_FROM",
    "related": "RELATED",
    "RELATED": "RELATED",
    None: "RELATED",
}


def _safe_label(label: str) -> bool:
    return label in _LABEL_WHITELIST


def _safe_rel_type(rel: str | None) -> str:
    if rel in _REL_WHITELIST_MAP:
        return _REL_WHITELIST_MAP[rel]
    # 对未知中文/英文统一降级 RELATED,避免 Cypher 注入
    return "RELATED"


_KIND_TO_LABEL = {
    "entity": "Entity",
    "topic": "Topic",
    "source": "Source",
    "query": "Query",
    "synthesis": "Synthesis",
    "comparison": "Comparison",
}


# ---------------------------------------------------------------------------
# 全局单例 + 工厂
# ---------------------------------------------------------------------------

_global_client: Neo4jClient | None = None


def get_neo4j_client() -> Neo4jClient:
    """返回单例 client(未连接);使用前需先 await connect()。"""
    global _global_client
    if _global_client is None:
        _global_client = Neo4jClient(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password,
        )
    return _global_client


def reset_for_tests() -> None:
    global _global_client
    _global_client = None


async def sync_graph_to_neo4j(graph: dict) -> dict:
    """把 build_graph_data 输出的 graph dict 全量同步到 Neo4j。

    失败不抛异常,返回 {ok, nodes_synced, edges_synced, error?}。
    主流程不依赖结果。
    """
    client = get_neo4j_client()
    if not await client.connect():
        return {"ok": False, "error": "neo4j connect failed", "nodes_synced": 0, "edges_synced": 0}

    nodes_synced = 0
    edges_synced = 0
    try:
        for n in graph.get("nodes", []):
            label = _KIND_TO_LABEL.get(n.get("kind"), "WikiNode")
            ok = await client.upsert_node(
                slug=n["id"],
                label=label,
                props={
                    "title": n.get("title") or n["id"],
                    "tags": n.get("tags") or [],
                    "domains": n.get("domains") or [],
                    "community_id": n.get("community_id"),
                    "degree": n.get("degree", 0),
                },
            )
            if ok:
                nodes_synced += 1
        for e in graph.get("edges", []):
            ok = await client.upsert_edge(
                src_slug=e["source"],
                dst_slug=e["target"],
                rel_type=e.get("type"),
                props={
                    "weight": e.get("weight", 1.0),
                    "confidence": e.get("confidence") or "EXTRACTED",
                    "original_type": e.get("type"),
                },
            )
            if ok:
                edges_synced += 1
        return {"ok": True, "nodes_synced": nodes_synced, "edges_synced": edges_synced}
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(e),
            "nodes_synced": nodes_synced,
            "edges_synced": edges_synced,
        }
