"""Build or incrementally refresh the chunk-level vector index used by QA retrieval.

用法(容器内或本地 backend 目录):
    py scripts/rebuild_query_chunk_index.py [--batch-size 32]

增量语义:按页级 content_hash 判断变化,只嵌入新增/变化页面的 chunk。
老的页级 collection ``wiki_query_pages`` 不受影响,验证后手动清理。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.services.query.vector_index import sync_query_vector_index


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    result = asyncio.run(
        sync_query_vector_index(
            wiki_root=settings.wiki_root_path,
            batch_size=max(1, args.batch_size),
        )
    )
    print(result)
