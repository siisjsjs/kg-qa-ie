import json
from collections import Counter, defaultdict

g = json.load(open("/app/data/wiki/graph-data.json", encoding="utf-8"))
edges = g["edges"]
nodes = g["nodes"]
nids = [n["id"] for n in nodes]


def knn(k):
    inc = defaultdict(list)
    for i, e in enumerate(edges):
        w = float(e.get("weight") or 0)
        inc[e["source"]].append((w, i))
        inc[e["target"]].append((w, i))
    keep = set()
    for nid, lst in inc.items():
        lst.sort(reverse=True)
        for _, i in lst[:k]:
            keep.add(i)
    kept = [edges[i] for i in keep]
    deg = Counter()
    for e in kept:
        deg[e["source"]] += 1
        deg[e["target"]] += 1
    vals = sorted(deg.values(), reverse=True) if deg else [0]
    isolated = sum(1 for nid in nids if deg[nid] == 0)
    return len(kept), vals[0], vals[len(vals) // 2], round(sum(vals) / max(1, len(vals)), 1), isolated


print("orig", len(edges))
for k in (6, 8, 10, 12, 16, 20, 24):
    kept, mx, med, mean, iso = knn(k)
    print(f"k={k} edges={kept} maxdeg={mx} median={med} mean={mean} isolated={iso}")
