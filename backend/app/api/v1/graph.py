"""graph:知识图谱数据 + 重建 + insights。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.core.deps import AdminUserId, CurrentUserId
from app.schemas.graph import GraphData, GraphInsight, GraphRebuildResponse
from app.services.graph.builder import (
    build_graph_data,
    load_graph_data_json,
    write_graph_data_json,
)
from app.services.graph.neo4j_client import sync_graph_to_neo4j

router = APIRouter()


@router.get("/graph/data", response_model=GraphData)
async def get_graph_data(user_id: CurrentUserId) -> GraphData:
    """读取已生成的 graph-data.json;首次未生成时自动构建一次。"""
    wiki_root = settings.wiki_root_path
    payload = await load_graph_data_json(wiki_root=wiki_root)
    if payload is None:
        payload = await build_graph_data(wiki_root=wiki_root)
        await write_graph_data_json(wiki_root=wiki_root, payload=payload)

    # 把 communities 的 key 从 str → int(schema 要求 int)
    raw_comm = payload.get("communities") or {}
    communities: dict[int, list[str]] = {}
    for k, v in raw_comm.items():
        try:
            communities[int(k)] = list(v)
        except (TypeError, ValueError):
            continue

    # 兜底:历史存量 graph-data.json 里 tags/domains 可能混了 nested list。
    # 在送进 schema 前清洗成 list[str]。
    nodes_raw = payload.get("nodes") or []
    cleaned_nodes = []
    for n in nodes_raw:
        if not isinstance(n, dict):
            continue
        node = dict(n)
        for key in ("tags", "domains"):
            val = node.get(key)
            if isinstance(val, list):
                flat = []
                for item in val:
                    if isinstance(item, str):
                        s = item.strip()
                        if s:
                            flat.append(s)
                    elif isinstance(item, (list, tuple)):
                        for sub in item:
                            if isinstance(sub, str):
                                s = sub.strip()
                                if s:
                                    flat.append(s)
                node[key] = flat
            elif val is None:
                node[key] = []
            else:
                node[key] = [str(val)]
        cleaned_nodes.append(node)

    return GraphData(
        nodes=cleaned_nodes,
        edges=payload.get("edges") or [],
        communities=communities,
        insights=payload.get("insights") or [],
        generated_at=payload.get("generated_at"),
    )


@router.post("/graph/rebuild", response_model=GraphRebuildResponse)
async def rebuild_graph(user_id: AdminUserId) -> GraphRebuildResponse:
    """触发图谱重建:扫双链 + 社区 + insights → 写盘 → fire-and-forget Neo4j 同步。"""
    wiki_root = settings.wiki_root_path
    payload = await build_graph_data(wiki_root=wiki_root)
    await write_graph_data_json(wiki_root=wiki_root, payload=payload)
    # Neo4j 同步:错误不影响响应
    try:
        await sync_graph_to_neo4j(payload)
    except Exception:  # noqa: BLE001
        pass

    job_id = f"graph-rebuild-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    return GraphRebuildResponse(
        job_id=job_id,
        total_nodes=len(payload.get("nodes") or []),
        total_edges=len(payload.get("edges") or []),
    )


@router.get("/graph/insights", response_model=list[GraphInsight])
async def get_graph_insights(user_id: CurrentUserId) -> list[GraphInsight]:
    """取 insights:惊人连接 / 桥节点 / 知识缺口 / 稀疏社区。

    若 graph-data.json 不存在,返回空列表(由前端引导用户先 rebuild)。
    """
    wiki_root = settings.wiki_root_path
    payload = await load_graph_data_json(wiki_root=wiki_root)
    if payload is None:
        return []
    return [GraphInsight(**i) for i in payload.get("insights") or []]
