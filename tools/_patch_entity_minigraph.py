"""Patch live wiki.py: entity mini_graph == related_entities."""
from pathlib import Path

P = Path("/app/app/api/v1/wiki.py")
text = P.read_text(encoding="utf-8")
old = '''        related_entities = sorted(rel_map.values(), key=lambda e: (-e.occurrences, e.title))

        # mini_graph:graph-data.json ego 子图(全语义边)
        from app.services.graph.builder import load_graph_data_json

        full = await load_graph_data_json(wiki_root=wiki_root)
        if full:
            node_by_id = {n.get("id"): n for n in full.get("nodes", [])}
            if center_id not in node_by_id:
                center_id = next(
                    (n["id"] for n in full.get("nodes", []) if n.get("slug") == slug), None
                )
            if center_id and center_id in node_by_id:
                keep_ids: set[str] = {center_id}
                ego_edges: list[MiniGraphEdge] = []
                for e in full.get("edges", []):
                    s, t = e.get("source"), e.get("target")
                    if s != center_id and t != center_id:
                        continue
                    other = t if s == center_id else s
                    # 实体关系图只显示实体↔实体的语义边,排除 topic/source 节点
                    onode = node_by_id.get(other)
                    if onode is None or onode.get("kind") != "entity":
                        continue
                    keep_ids.add(s)
                    keep_ids.add(t)
                    ego_edges.append(MiniGraphEdge(
                        source=s, target=t,
                        rel_type=e.get("type") or "related",
                        weight=float(e.get("weight") or 0.0),
                        edge_kind="semantic",
                    ))
                mini_nodes = [
                    MiniGraphNode(
                        id=nid,
                        slug=node_by_id[nid].get("slug", ""),
                        title=node_by_id[nid].get("title", ""),
                        kind=node_by_id[nid].get("kind", "entity"),
                        is_center=(nid == center_id),
                    )
                    for nid in keep_ids if nid in node_by_id
                ]
                mini = MiniGraph(nodes=mini_nodes, edges=ego_edges)
'''
new = '''        related_entities = sorted(rel_map.values(), key=lambda e: (-e.occurrences, e.title))

        # mini_graph:与 related_entities 同一批节点(wiki_links 双向),不读 graph-data.json
        mini_nodes = [MiniGraphNode(id=center_id, slug=slug, title=self_title, kind="entity", is_center=True)]
        mini_edges: list[MiniGraphEdge] = []
        for r in related_entities:
            nid = f"entity:{r.slug}"
            mini_nodes.append(MiniGraphNode(
                id=nid, slug=r.slug, title=r.title, kind="entity", is_center=False,
            ))
            occ = max(int(r.occurrences or 1), 1)
            mini_edges.append(MiniGraphEdge(
                source=center_id, target=nid,
                rel_type=r.rel_type or "related",
                weight=min(1.0, 0.3 + 0.05 * occ),
                edge_kind="semantic",
            ))
        mini = MiniGraph(nodes=mini_nodes, edges=mini_edges)
'''
if old not in text:
    raise SystemExit("OLD BLOCK NOT FOUND")
if "同一批节点(wiki_links 双向)" in text:
    print("already patched")
else:
    bak = P.with_suffix(".py.bak-minigraph")
    if not bak.exists():
        bak.write_text(text, encoding="utf-8")
    P.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("patched", P)
print("has_old", old[:40] in P.read_text(encoding="utf-8"))
print("has_new", "不读 graph-data.json" in P.read_text(encoding="utf-8"))
