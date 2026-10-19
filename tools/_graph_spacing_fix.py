"""Sparsify live graph-data.json and inject degree cap into builder.py.

Visualization-only: wiki page [[links]] are unchanged.
"""
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

GRAPH_JSON = Path("/app/data/wiki/graph-data.json")
BUILDER = Path("/app/app/services/graph/builder.py")
BACKUP_JSON = Path("/app/data/wiki/graph-data.json.bak-20260823-spacing")
MAX_DEG = 16
MIN_DEG = 2

INJECT_FN = '''
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

'''

CALL_OLD = """    # 度数
    deg: dict[str, int] = {}
"""
CALL_NEW = """    edges = _sparsify_edges(nodes, edges)

    # 度数
    deg: dict[str, int] = {}
"""


def sparsify(nodes, edges, max_deg=MAX_DEG, min_deg=MIN_DEG):
    if not edges:
        return edges
    nids = [n["id"] for n in nodes]
    ranked = sorted(range(len(edges)), key=lambda i: float(edges[i].get("weight") or 0), reverse=True)
    deg = {}
    keep = set()
    for i in ranked:
        e = edges[i]
        s, t = e["source"], e["target"]
        if deg.get(s, 0) < max_deg and deg.get(t, 0) < max_deg:
            keep.add(i)
            deg[s] = deg.get(s, 0) + 1
            deg[t] = deg.get(t, 0) + 1
    incident = defaultdict(list)
    for i, e in enumerate(edges):
        w = float(e.get("weight") or 0)
        incident[e["source"]].append((w, i, e["target"]))
        incident[e["target"]].append((w, i, e["source"]))
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


def patch_builder():
    text = BUILDER.read_text(encoding="utf-8")
    if "_sparsify_edges" in text:
        print("builder.py already has _sparsify_edges")
        return
    needle = "def _flatten_strs(value) -> list[str]:"
    if needle not in text:
        raise SystemExit("builder.py: cannot find insert point")
    text = text.replace(needle, INJECT_FN + needle, 1)
    if CALL_OLD not in text:
        raise SystemExit("builder.py: cannot find degree block")
    text = text.replace(CALL_OLD, CALL_NEW, 1)
    BUILDER.write_text(text, encoding="utf-8")
    print("patched builder.py")


JS_REPLACEMENTS = [
    ("t&&t.strength(-160).distanceMax(500)", "t&&t.strength(-420).distanceMax(2400)"),
    (
        "r&&r.distance(o=>40+(1-(o.weight||.5))*50).strength(.25)",
        "r&&r.distance(o=>90+(1-(o.weight||.5))*110).strength(.12)",
    ),
    ("+6).strength(.85)", "+12).strength(.9)"),
    ("const $=18,j=70", "const $=28,j=110"),
    ("ae&&ae.strength(-160)", "ae&&ae.strength(-480)"),
    ("ve&&ve.strength(.03)", "ve&&ve.strength(.012)"),
]


def patch_js(path: Path):
    if not path.exists():
        print("skip js", path)
        return
    text = path.read_text(encoding="utf-8")
    orig = text
    for old, new in JS_REPLACEMENTS:
        if old in text:
            text = text.replace(old, new)
            print("js hit", path.name, old[:40])
        else:
            print("js miss", path.name, old[:40])
    if text != orig:
        bak = path.with_suffix(path.suffix + ".bak-spacing")
        if not bak.exists():
            bak.write_text(orig, encoding="utf-8")
        path.write_text(text, encoding="utf-8")
        print("patched", path, "bytes", len(text))
    else:
        print("unchanged", path)


def main():
    patch_builder()
    payload = json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
    nodes = payload["nodes"]
    edges = payload["edges"]
    print("before", len(nodes), "nodes", len(edges), "edges")
    if not BACKUP_JSON.exists():
        shutil.copy2(GRAPH_JSON, BACKUP_JSON)
        print("backup", BACKUP_JSON)
    else:
        print("backup exists", BACKUP_JSON)
    new_edges = sparsify(nodes, edges)
    deg = {}
    for e in new_edges:
        deg[e["source"]] = deg.get(e["source"], 0) + 1
        deg[e["target"]] = deg.get(e["target"], 0) + 1
    for n in nodes:
        n["degree"] = deg.get(n["id"], 0)
    payload["edges"] = new_edges
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["_spacing_note"] = f"visualization backbone max_deg={MAX_DEG} min_deg={MIN_DEG}"
    tmp = GRAPH_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(GRAPH_JSON)
    vals = sorted(deg.values(), reverse=True) if deg else [0]
    isolated = sum(1 for n in nodes if deg.get(n["id"], 0) == 0)
    print(
        "after",
        len(nodes),
        "nodes",
        len(new_edges),
        "edges",
        "maxdeg",
        vals[0],
        "median",
        vals[len(vals) // 2],
        "mean",
        round(sum(vals) / max(1, len(vals)), 1),
        "isolated",
        isolated,
    )
    for js in (
        Path("/app/data/Graph2D-CqTmZKX8.js"),
        Path("/app/data/Graph3D-Doe-k8YC.js"),
    ):
        if js.exists():
            patch_js(js)


if __name__ == "__main__":
    main()
