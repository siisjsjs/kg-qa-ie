import hashlib
import json
import pathlib
import re

needles = [
    "_sparsify_edges",
    "required_first",
    "_autofill_named_chart",
    "response_format",
    "hold_for_required",
    "coerce_chart_params",
    "related_entities",
    "mini_graph",
    "_test_imagegen",
    "/v1/models",
    "session_id",
    "citation_details",
    "generate_chart",
    "exclude=",
]

files = {
    "builder.py": "/app/app/services/graph/builder.py",
    "wiki.py": "/app/app/api/v1/wiki.py",
    "query.py": "/app/app/api/v1/query.py",
    "agent.py": "/app/app/services/query/agent.py",
    "imagegen.py": "/app/app/services/tools/imagegen.py",
    "chart_generate.py": "/app/app/services/tools/chart_generate.py",
    "chart_templates.py": "/app/app/services/tools/chart_templates.py",
    "admin.py": "/app/app/api/v1/admin.py",
    "persistor.py": "/app/app/services/query/persistor.py",
    "agent_system.md": "/app/app/prompts/agent_system.md",
    "qa_data.py": "/app/app/api/v1/qa_data.py",
    "wiki_schema.py": "/app/app/schemas/wiki.py",
    "registry.py": "/app/app/services/tools/registry.py",
}

print("=== hashes / hits ===")
for name, path in files.items():
    p = pathlib.Path(path)
    if not p.exists():
        print(name, "MISSING")
        continue
    data = p.read_bytes()
    text = data.decode("utf-8", "replace")
    hits = [n for n in needles if n in text]
    print(f"{name}\t{len(data)}\t{hashlib.md5(data).hexdigest()}\t{hits}")

print("\n=== wiki / graph ===")
root = pathlib.Path("/app/data/wiki")
print("wiki_root", root.exists())
for rel in ["wiki/entities", "entities", "wiki/topics", "topics"]:
    d = root / rel
    if d.exists():
        print(rel, len(list(d.iterdir())))
g = root / "graph-data.json"
print("graph-data", g.exists(), g.stat().st_size if g.exists() else 0)
if g.exists():
    d = json.loads(g.read_text(encoding="utf-8"))
    print("nodes", len(d.get("nodes", [])), "edges", len(d.get("edges", [])))

print("\n=== wiki.py mini_graph hint ===")
wiki = pathlib.Path("/app/app/api/v1/wiki.py")
if wiki.exists():
    text = wiki.read_text(encoding="utf-8", errors="replace")
    for i, line in enumerate(text.splitlines(), 1):
        if "mini_graph" in line or "related_entities" in line or "graph-data" in line:
            if i < 30 or "mini_graph" in line or "spars" in line.lower():
                print(f"{i}:{line[:200]}")

print("\n=== imagegen payload keys ===")
img = pathlib.Path("/app/app/services/tools/imagegen.py")
if img.exists():
    text = img.read_text(encoding="utf-8", errors="replace")
    for i, line in enumerate(text.splitlines(), 1):
        if "response_format" in line or "b64_json" in line or "/v1/models" in line or "payload" in line:
            print(f"{i}:{line[:200]}")
