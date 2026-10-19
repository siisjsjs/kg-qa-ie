import json
from collections import Counter, defaultdict

g = json.load(open("/app/data/wiki/graph-data.json", encoding="utf-8"))
edges = g["edges"]
nids = [n["id"] for n in g["nodes"]]


def backbone(max_deg, min_deg=2):
    ranked = sorted(
        enumerate(edges),
        key=lambda it: float(it[1].get("weight") or 0),
        reverse=True,
    )
    deg = Counter()
    keep = set()
    for i, e in ranked:
        s, t = e["source"], e["target"]
        if deg[s] < max_deg and deg[t] < max_deg:
            keep.add(i)
            deg[s] += 1
            deg[t] += 1
    # ensure low-degree nodes keep their strongest leftover edges
    incident = defaultdict(list)
    for i, e in enumerate(edges):
        incident[e["source"]].append((float(e.get("weight") or 0), i, e["target"]))
        incident[e["target"]].append((float(e.get("weight") or 0), i, e["source"]))
    for nid in nids:
        if deg[nid] >= min_deg:
            continue
        lst = sorted(incident.get(nid, []), reverse=True)
        for _, i, other in lst:
            if i in keep:
                continue
            if deg[nid] >= min_deg:
                break
            if deg[other] >= max_deg + 4:
                continue
            keep.add(i)
            deg[nid] += 1
            deg[other] += 1
    vals = sorted((deg[n] for n in nids), reverse=True)
    isolated = sum(1 for n in nids if deg[n] == 0)
    return len(keep), vals[0], vals[len(vals) // 2], round(sum(vals) / len(vals), 1), isolated


print("orig edges", len(edges))
for md in (8, 10, 12, 16, 20):
    kept, mx, med, mean, iso = backbone(md)
    print(f"max_deg={md} edges={kept} max={mx} median={med} mean={mean} isolated={iso}")
