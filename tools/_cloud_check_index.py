"""Cloud kgqa vector-index health. Run inside kgqa-backend-1."""
from __future__ import annotations

import json
import os
from pathlib import Path

print("=== env ===")
for k in [
    "CHROMA_HOST",
    "CHROMA_PORT",
    "CHROMA_PERSIST_DIR",
    "WIKI_ROOT",
    "CACHE_DIR",
]:
    print(f"{k}={os.environ.get(k)!r}")

from app.core.config import settings

print("settings.chroma_host", settings.chroma_host)
print("settings.chroma_port", settings.chroma_port)
print("settings.chroma_persist_dir", settings.chroma_persist_dir)
print("settings.wiki_root", settings.wiki_root)
print("settings.cache_dir", settings.cache_dir)

from app.services.query.vector_index import (
    COLLECTION_NAME,
    index_freshness,
    marker_path,
)

mp = marker_path()
print("\n=== marker ===")
print("marker_path", mp)
print("exists", mp.exists())
if mp.exists():
    print(mp.read_text(encoding="utf-8"))

print("\n=== freshness ===")
try:
    print(json.dumps(index_freshness(settings.wiki_root_path), ensure_ascii=False, indent=2))
except Exception as e:
    print("freshness_error", type(e).__name__, e)

print("\n=== chroma collection ===")
try:
    from app.services.embedding.vector_store import WikiVectorStore

    store = WikiVectorStore(
        persist_dir=settings.chroma_persist_dir,
        host=settings.chroma_host,
        port=settings.chroma_port,
        collection_name=COLLECTION_NAME,
    )
    col = store._get_collection()
    print("collection", col.name, "count", col.count())
    peek = col.peek(limit=2)
    ids = peek.get("ids") or []
    metas = peek.get("metadatas") or []
    print("peek_ids", ids[:2])
    if metas:
        print("peek_meta_keys", list(metas[0].keys()) if metas[0] else None)
        print("peek_index_root", (metas[0] or {}).get("index_root"))
        print("peek_slug", (metas[0] or {}).get("slug"))
except Exception as e:
    print("chroma_error", type(e).__name__, e)

print("\n=== hybrid gate (same as QA) ===")
try:
    marker_file = marker_path()
    marker = json.loads(marker_file.read_text(encoding="utf-8"))
    resolved_root = str(Path(settings.wiki_root).resolve())
    print("index_root_in_marker", marker.get("index_root"))
    print("resolved_wiki_root", resolved_root)
    print("root_match", marker.get("index_root") == resolved_root)
    print("collection_in_marker", marker.get("collection"))
    print("page_count", marker.get("page_count"), "chunk_count", marker.get("chunk_count"))
except FileNotFoundError:
    print("QA_SEMANTIC_BAIL", "marker_missing")
except Exception as e:
    print("QA_SEMANTIC_BAIL", type(e).__name__, e)

print("\n=== hybrid.py bail reasons present ===")
hp = Path("/app/app/services/query/hybrid.py")
text = hp.read_text(encoding="utf-8")
for needle in [
    "marker_missing",
    "index_root_mismatch",
    "collection_empty",
    "embedding_empty",
    "start_freshness_watcher",
]:
    print(needle, needle in text)
