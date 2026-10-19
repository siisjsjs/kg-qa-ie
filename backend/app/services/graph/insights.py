"""graph/insights.py:规则启发式 insights。

输入是 build_graph_data 输出的 dict:{nodes, edges, communities}。
输出列表元素与 schemas.graph.GraphInsight 字段对齐。
"""

from __future__ import annotations

from collections import defaultdict


def _community_of(graph: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for cid, members in (graph.get("communities") or {}).items():
        for slug in members:
            out[slug] = int(cid)
    return out


def find_surprising_links(graph: dict, *, weight_threshold: float = 0.6) -> list[dict]:
    """跨社区且权重 ≥ 阈值的边。"""
    inv = _community_of(graph)
    out: list[dict] = []
    for e in graph.get("edges", []):
        ca = inv.get(e["source"])
        cb = inv.get(e["target"])
        if ca is None or cb is None or ca == cb:
            continue
        if e["weight"] < weight_threshold:
            continue
        out.append({
            "kind": "surprising_link",
            "title": f"跨社区高权重边:[[{e['source']}]] —{e['type']}— [[{e['target']}]]",
            "detail": f"weight={e['weight']:.2f} 跨越社区 {ca} 与 {cb}",
            "related_slugs": [e["source"], e["target"]],
        })
    out.sort(key=lambda x: -float(x["detail"].split("=")[1].split(" ")[0]))
    return out[:20]


def find_bridge_nodes(graph: dict, *, min_communities: int = 3) -> list[dict]:
    """连接 ≥ min_communities 个社区的节点。"""
    inv = _community_of(graph)
    neighbor_communities: dict[str, set[int]] = defaultdict(set)
    for e in graph.get("edges", []):
        cs = inv.get(e["source"])
        ct = inv.get(e["target"])
        if cs is not None and ct is not None and cs != ct:
            neighbor_communities[e["source"]].add(ct)
            neighbor_communities[e["target"]].add(cs)
    out: list[dict] = []
    for slug, communities in neighbor_communities.items():
        if len(communities) >= min_communities:
            out.append({
                "kind": "bridge_node",
                "title": f"桥节点 [[{slug}]] 连接 {len(communities)} 个社区",
                "detail": f"communities={sorted(communities)}",
                "related_slugs": [slug],
            })
    out.sort(key=lambda x: -len(x["related_slugs"]))
    return out[:20]


def find_knowledge_gaps(graph: dict, *, max_degree: int = 1) -> list[dict]:
    """度数 ≤ max_degree 的实体节点 → 知识缺口候选。"""
    out: list[dict] = []
    for n in graph.get("nodes", []):
        if n["kind"] != "entity":
            continue
        if n.get("degree", 0) <= max_degree:
            out.append({
                "kind": "knowledge_gap",
                "title": f"度数低节点 [[{n['id']}]]",
                "detail": f"degree={n.get('degree', 0)},缺乏关联,可能需要补素材",
                "related_slugs": [n["id"]],
            })
    out.sort(key=lambda x: int(x["detail"].split("=")[1].split(",")[0]))
    return out[:30]


def find_sparse_communities(graph: dict, *, min_size: int = 3) -> list[dict]:
    """节点数 < min_size 的社区。"""
    out: list[dict] = []
    for cid, members in (graph.get("communities") or {}).items():
        if 0 < len(members) < min_size:
            out.append({
                "kind": "sparse_community",
                "title": f"稀疏社区 #{cid}({len(members)} 个节点)",
                "detail": f"members={members}",
                "related_slugs": list(members),
            })
    return out[:20]
