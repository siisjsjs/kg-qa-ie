"""graph/builder.py:扫双链 → 节点/边 → 社区发现 → insights → graph-data.json。

设计:
  1. 扫 wiki/**/*.md 全部页面 → 节点
     - 同一 slug 在不同 kind 目录下(如 entity:工作研究 与 topic:工作研究)
       视为两个独立节点,用 `{kind}:{slug}` 组合作为图谱内部 id;
       前端跳 wiki 时按 slug 字段拼路径。
  2. 抽 [[X]] 边(用 link_resolver)→ aggregate
     - 目标 slug → 解析到具体节点的优先级:
       同 kind 优先(entity 引用同名 entity)→ entity → topic → 其他;
       这与 wiki API _resolve_path 的查找顺序一致。
  3. 边权重 = 0.5 * occurrences_norm + 0.3 * type_affinity + 0.2 * source_overlap
     - occurrences_norm:边的 occurrences / max_occurrences
     - type_affinity:link_type 中"实现"/"依赖"/"衍生" → 1.0;"对比" → 0.7;"矛盾" → 0.5;None → 0.6
     - source_overlap:src/dst 两页 frontmatter.sources 列表的 Jaccard
  4. Louvain 社区发现(networkx + python-louvain)
  5. insights:规则启发式
  6. 写 wiki_root/graph-data.json
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.wiki.frontmatter_io import parse_text
from app.services.wiki.link_resolver import (
    aggregate_edges,
    iter_wiki_pages,
    rebuild_links_index,
)

_TYPE_AFFINITY = {
    "实现": 1.0,
    "依赖": 1.0,
    "衍生": 0.9,
    "对比": 0.7,
    "矛盾": 0.5,
    None: 0.6,
    "related": 0.6,
}


def _kind_from_path(rel_parts: tuple[str, ...]) -> str:
    if len(rel_parts) >= 2 and rel_parts[0] == "wiki":
        return {
            "entities": "entity",
            "topics": "topic",
            "sources": "source",
            "queries": "query",
            "synthesis": "synthesis",
            "comparisons": "comparison",
        }.get(rel_parts[1], "entity")
    return "entity"


def _is_graph_visible(rel_parts: tuple[str, ...]) -> bool:
    """是否纳入图谱可视化。

    跳过非内容页:
    - `wiki/_growth/*`(gap-queue / hot_topics 等系统自动维护的元数据)
    - 任何下划线开头的目录(约定为系统目录)
    - **问答衍生节点**(queries / synthesis / comparisons)— 现已剥离到独立的
      「问答数据」管理员页(P-book-tree),图谱只保留实体/主题/素材三类
    """
    if len(rel_parts) < 2 or rel_parts[0] != "wiki":
        return False
    sub = rel_parts[1]
    if sub.startswith("_") or sub.startswith("."):
        return False
    return sub in {"entities", "topics", "sources"}


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a or []), set(b or [])
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))



_GRAPH_MAX_DEGREE = 16
_GRAPH_MIN_DEGREE = 2


def _sparsify_edges(nodes: list[dict], edges: list[dict], *, max_deg: int = _GRAPH_MAX_DEGREE, min_deg: int = _GRAPH_MIN_DEGREE) -> list[dict]:
    """Keep a visualization backbone so force layouts can space nodes.

    High-weight edges first; both endpoints capped; then restore isolates
    up to min_deg. Wiki page wikilinks are not modified.
    """
    if not edges:
        return edges
    nids = [n["id"] for n in nodes]
    ranked = sorted(range(len(edges)), key=lambda i: float(edges[i].get("weight") or 0), reverse=True)
    deg: dict[str, int] = {}
    keep: set[int] = set()
    for i in ranked:
        e = edges[i]
        s, t = e["source"], e["target"]
        if deg.get(s, 0) < max_deg and deg.get(t, 0) < max_deg:
            keep.add(i)
            deg[s] = deg.get(s, 0) + 1
            deg[t] = deg.get(t, 0) + 1
    incident: dict[str, list[tuple[float, int, str]]] = {}
    for i, e in enumerate(edges):
        w = float(e.get("weight") or 0)
        incident.setdefault(e["source"], []).append((w, i, e["target"]))
        incident.setdefault(e["target"], []).append((w, i, e["source"]))
    for nid in nids:
        if deg.get(nid, 0) >= min_deg:
            continue
        for _, i, other in sorted(incident.get(nid, []), reverse=True):
            if i in keep:
                continue
            if deg.get(nid, 0) >= min_deg:
                break
            if deg.get(other, 0) >= max_deg + 4:
                continue
            keep.add(i)
            deg[nid] = deg.get(nid, 0) + 1
            deg[other] = deg.get(other, 0) + 1
    return [edges[i] for i in sorted(keep)]

def _flatten_strs(value) -> list[str]:
    """把 frontmatter 里的 tags / domains 规整成 list[str]。

    YAML 写法五花八门:str / list[str] / list[list[str]] / None / 嵌套字典等都见过。
    全部递归扁平化,转字符串,去空,去重保持顺序。
    """
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            for s in _flatten_strs(item):
                if s not in seen:
                    seen.add(s)
                    out.append(s)
        return out
    s = str(value).strip()
    return [s] if s else []


async def build_graph_data(*, wiki_root: Path) -> dict:
    """扫双链 → 计算节点/边 → 社区 → insights → 返回 graph-data dict。"""
    wiki_root = Path(wiki_root).resolve()

    # 1) 节点
    # node_id = `{kind}:{slug}`,允许同 slug 不同 kind 共存(如 entity:工作研究 与 topic:工作研究)。
    # 前端跳 wiki 时使用 node.slug 字段而非 node.id。
    nodes: list[dict] = []
    page_meta: dict[str, dict] = {}  # node_id → {sources, kind, title, slug}
    # slug → {kind: node_id} 用于 [[X]] 边解析时按 kind 优先级 resolve
    slug_to_kind_nodes: dict[str, dict[str, str]] = {}
    # source slug → book(用于 topic/entity 继承 book)
    source_slug_to_book: dict[str, str | None] = {}
    # 缓存 (node_id, raw_fm_sources)以便第二轮回填 book
    pending_book: list[tuple[dict, list[str], str]] = []  # (node_dict, fm_sources, kind)
    for page in iter_wiki_pages(wiki_root):
        rel_parts = page.relative_to(wiki_root).parts
        if not _is_graph_visible(rel_parts):
            continue
        try:
            fm, _ = parse_text(page.read_text(encoding="utf-8"))
        except Exception:
            continue
        slug = page.stem
        kind = _kind_from_path(rel_parts)
        node_id = f"{kind}:{slug}"
        fm_sources = list(fm.get("sources") or [])
        node = {
            "id": node_id,
            "slug": slug,
            "title": str(fm.get("title") or slug),
            "kind": kind,
            "tags": _flatten_strs(fm.get("tags")),
            "domains": _flatten_strs(fm.get("domains")),
            "book": None,
            "community_id": None,
            "degree": 0,
        }
        nodes.append(node)
        page_meta[node_id] = {
            "sources": fm_sources,
            "kind": kind,
            "slug": slug,
        }
        slug_to_kind_nodes.setdefault(slug, {})[kind] = node_id
        if kind == "source":
            book_val = fm.get("book")
            book_str = str(book_val) if book_val else None
            source_slug_to_book[slug] = book_str
            node["book"] = book_str
        else:
            pending_book.append((node, fm_sources, kind))

    # 第二轮:topic / entity 从其首个存在的 source 继承 book
    for node, fm_sources, _kind in pending_book:
        for s in fm_sources:
            if s in source_slug_to_book:
                node["book"] = source_slug_to_book[s]
                break

    # [[X]] 目标 slug → 节点 id 解析。
    # 图谱按用户期望的层级语义裁边(而非把同名多 kind 节点都连一遍):
    #   素材(source) → 只连主题(topic)
    #   主题(topic)  → 只连实体(entity)
    #   实体(entity) → 只连实体(entity)
    # 这样三层各司其职,避免 entity→source / topic→topic 之类的跨层乱边。
    _ALLOWED_DST_KIND = {
        "source": "topic",
        "topic": "entity",
        "entity": "entity",
    }

    def _resolve_target(target_slug: str, dst_kind: str) -> str | None:
        candidates = slug_to_kind_nodes.get(target_slug)
        if not candidates:
            return None
        return candidates.get(dst_kind)

    # 2) 边
    raw_edges = aggregate_edges(rebuild_links_index(wiki_root))
    edges: list[dict] = []
    max_occ = max((int(e["occurrences"] or 1) for e in raw_edges), default=1)
    seen_edges: set[tuple[str, str, str]] = set()

    for e in raw_edges:
        src_slug = e["src_slug"]
        src_kind = e.get("src_kind")
        dst_slug = e["dst_slug"]
        # 出边的合法目标层由源页 kind 决定;源页 kind 不在三层语义内则跳过。
        dst_kind = _ALLOWED_DST_KIND.get(src_kind or "")
        if dst_kind is None:
            continue
        src_nodes = slug_to_kind_nodes.get(src_slug)
        if not src_nodes:
            continue
        src_id = src_nodes.get(src_kind)
        if src_id is None:
            continue
        dst_id = _resolve_target(dst_slug, dst_kind)
        if dst_id is None or dst_id == src_id:
            continue
        link_type = e.get("link_type") or "related"
        occurrences = int(e.get("occurrences") or 1)

        occ_norm = occurrences / max_occ
        type_aff = _TYPE_AFFINITY.get(link_type, _TYPE_AFFINITY[None])
        src_meta = page_meta.get(src_id) or {}
        dst_meta = page_meta.get(dst_id) or {}
        src_overlap = _jaccard(src_meta.get("sources", []), dst_meta.get("sources", []))
        weight = round(0.5 * occ_norm + 0.3 * type_aff + 0.2 * src_overlap, 4)

        dedup_key = (src_id, dst_id, link_type)
        if dedup_key in seen_edges:
            continue
        seen_edges.add(dedup_key)

        edges.append({
            "source": src_id,
            "target": dst_id,
            "type": link_type,
            "weight": weight,
            "confidence": e.get("confidence") or "EXTRACTED",
        })

    edges = _sparsify_edges(nodes, edges)

    # 度数
    deg: dict[str, int] = {}
    for e in edges:
        deg[e["source"]] = deg.get(e["source"], 0) + 1
        deg[e["target"]] = deg.get(e["target"], 0) + 1
    for n in nodes:
        n["degree"] = deg.get(n["id"], 0)

    # 3) 社区
    communities: dict[int, list[str]] = _detect_communities(nodes, edges)
    inv: dict[str, int] = {}
    for cid, members in communities.items():
        for m in members:
            inv[m] = cid
    for n in nodes:
        n["community_id"] = inv.get(n["id"])

    # 4) insights
    from app.services.graph.insights import (
        find_bridge_nodes,
        find_knowledge_gaps,
        find_sparse_communities,
        find_surprising_links,
    )
    graph_dict = {"nodes": nodes, "edges": edges, "communities": communities}
    insights = (
        find_surprising_links(graph_dict)
        + find_bridge_nodes(graph_dict)
        + find_knowledge_gaps(graph_dict)
        + find_sparse_communities(graph_dict)
    )

    return {
        "nodes": nodes,
        "edges": edges,
        "communities": {str(k): v for k, v in communities.items()},
        "insights": insights,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _detect_communities(nodes: list[dict], edges: list[dict]) -> dict[int, list[str]]:
    """优先 networkx + python-louvain;失败时降级:每个连通分量算一个社区。"""
    if not nodes:
        return {}
    try:
        import networkx as nx
        try:
            from community import community_louvain  # python-louvain
        except ImportError:  # pragma: no cover
            community_louvain = None  # type: ignore[assignment]

        g = nx.Graph()
        g.add_nodes_from(n["id"] for n in nodes)
        for e in edges:
            # 同对多类型边只取最大权重
            if g.has_edge(e["source"], e["target"]):
                if g[e["source"]][e["target"]]["weight"] < e["weight"]:
                    g[e["source"]][e["target"]]["weight"] = e["weight"]
            else:
                g.add_edge(e["source"], e["target"], weight=e["weight"])

        if community_louvain is not None and g.number_of_edges() > 0:
            partition = community_louvain.best_partition(g, weight="weight", random_state=42)
        else:
            # 降级:连通分量
            partition = {}
            for cid, comp in enumerate(nx.connected_components(g)):
                for n in comp:
                    partition[n] = cid

        comm: dict[int, list[str]] = {}
        for slug, cid in partition.items():
            comm.setdefault(cid, []).append(slug)
        return {cid: sorted(members) for cid, members in comm.items()}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"community detection failed: {e}; fallback to single community")
        return {0: [n["id"] for n in nodes]} if nodes else {}


async def write_graph_data_json(*, wiki_root: Path, payload: dict) -> Path:
    """把 build 出的 dict 写到 wiki_root/graph-data.json(原子写)。"""
    p = Path(wiki_root) / "graph-data.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    import os
    os.replace(tmp, p)
    return p


async def load_graph_data_json(*, wiki_root: Path) -> dict | None:
    p = Path(wiki_root) / "graph-data.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
