import json
from collections import Counter

p = "/app/data/wiki/graph-data.json"
g = json.load(open(p, encoding="utf-8"))
nodes = g["nodes"]
edges = g["edges"]
print("generated_at", g.get("generated_at"))
print("nodes", len(nodes), "edges", len(edges))
print("types", Counter(e.get("type") for e in edges).most_common(8))
ws = sorted(float(e.get("weight") or 0) for e in edges)


def pct(q):
    i = min(len(ws) - 1, int(q * len(ws)))
    return ws[i]


print(
    "weight min/p10/p25/p50/p90/max",
    round(ws[0], 4),
    round(pct(0.1), 4),
    round(pct(0.25), 4),
    round(pct(0.5), 4),
    round(pct(0.9), 4),
    round(ws[-1], 4),
)
deg = Counter()
for e in edges:
    deg[e["source"]] += 1
    deg[e["target"]] += 1
vals = sorted(deg.values(), reverse=True)
print(
    "deg max p99 p90 median mean",
    vals[0],
    vals[int(len(vals) * 0.01)],
    vals[int(len(vals) * 0.1)],
    vals[len(vals) // 2],
    round(sum(vals) / max(1, len(vals)), 1),
)
id2 = {n["id"]: n.get("title") or n["id"] for n in nodes}
print("top hubs")
for nid, d in deg.most_common(8):
    print(d, nid, id2.get(nid))
print("isolated", sum(1 for n in nodes if deg[n["id"]] == 0))
for cap in (24, 32, 48, 64):
    kept = 0
    outdeg = Counter()
    for e in sorted(edges, key=lambda x: float(x.get("weight") or 0), reverse=True):
        s = e["source"]
        if outdeg[s] >= cap:
            continue
        outdeg[s] += 1
        kept += 1
    print("outdeg_cap", cap, "kept", kept, "frac", round(kept / len(edges), 3))
