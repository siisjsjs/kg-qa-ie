"""Compare entity mini_graph nodes vs related_entities for a hub page."""
from __future__ import annotations

import asyncio

from app.core.db import session_scope
from app.api.v1.wiki import get_relations


async def check(slug: str) -> None:
    with session_scope() as db:
        data = await get_relations(slug=slug, user_id=1, db=db, kind="entity")
    rel = {r.slug for r in data.related_entities}
    nodes = {n.slug for n in data.mini_graph.nodes if not n.is_center}
    print("slug", slug)
    print("kind", data.kind)
    print("related", len(rel), "mini_neighbors", len(nodes), "mini_nodes", len(data.mini_graph.nodes), "edges", len(data.mini_graph.edges))
    print("equal", rel == nodes)
    print("only_in_related", sorted(rel - nodes)[:10], "n", len(rel - nodes))
    print("only_in_graph", sorted(nodes - rel)[:10], "n", len(nodes - rel))
    print("sample_related", [r.slug for r in data.related_entities[:8]])


async def main():
    for s in ("方法研究", "工业工程", "精益生产"):
        await check(s)
        print("---")


if __name__ == "__main__":
    asyncio.run(main())
