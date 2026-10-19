# IE QA benchmark

The checked-in dataset starts with high-value IE retrieval cases and should grow
from anonymized production questions. Each JSONL row contains a question,
acceptable source slugs, and terms expected in the selected sections.

Run the retrieval-only benchmark from `backend/`:

```powershell
python scripts/evaluate_query_retrieval.py
```

Reported metrics are `recall@5`, MRR, and section hit rate. This benchmark does
not call the answer LLM, so it is deterministic apart from an enabled semantic
provider/index. Build or incrementally refresh the vector index with:

```powershell
python scripts/rebuild_query_chunk_index.py
```

The ingestion post-processing pipeline runs the same incremental sync
automatically. Only new or changed Wiki pages are embedded; deleted pages are
removed from Chroma.
