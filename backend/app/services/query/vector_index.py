"""Incremental chunk-level vector index maintenance for retrievable Wiki pages.

改造计划 §4.2:collection 由页级 ``wiki_query_pages`` 换为 chunk 级
``wiki_query_chunks``(老 collection 不删,验证后手动清理)。
页面变化按页级 content_hash(全部 chunk hash 拼接再 hash)判断:变了就删该页
旧 chunk(where={"slug": slug})再插入新 chunk。
增量逻辑、freshness watcher、_SYNC_LOCK、失效回调全部保留。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from app.core.config import settings
from app.services.embedding.factory import get_embedding_provider
from app.services.embedding.vector_store import WikiVectorStore
from app.services.query.chunker import chunk_page, page_content_hash
from app.services.query.grep import _is_generated_page
from app.services.wiki.link_resolver import iter_wiki_pages

COLLECTION_NAME = "wiki_query_chunks"

FRESHNESS_CHECK_INTERVAL_SECONDS = 900
_FIRST_CHECK_DELAY_SECONDS = 60
_SYNC_LOCK = asyncio.Lock()
_WATCHER_TASK: asyncio.Task[None] | None = None


def marker_path() -> Path:
    return Path(settings.cache_dir) / "query_chunk_index.json"


def _indexable_pages(wiki_root: Path) -> list[Path]:
    return [
        path
        for path in iter_wiki_pages(wiki_root)
        if not _is_generated_page(path, wiki_root)
    ]


def index_freshness(wiki_root: Path | None = None) -> dict:
    """对比索引标记与磁盘现状,返回可观测的新鲜度指标。

    `indexed_page_count` 与 `disk_page_count` 都按 slug 去重统计:向量库以
    slug 为分组键,`entities/X.md` 与 `topics/X.md` 会折叠成一组,因此
    `disk_file_count - duplicate_slug_count == disk_page_count`,差额并非陈旧。
    """
    root = Path(wiki_root or settings.wiki_root_path).resolve()
    out: dict = {
        "index_root": str(root),
        "collection": COLLECTION_NAME,
        "indexed_page_count": None,
        "indexed_chunk_count": None,
        "built_at": None,
        "age_hours": None,
        "marker_present": False,
        "root_matches_marker": False,
    }
    try:
        marker = json.loads(marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        marker = None
    built_at: datetime | None = None
    if isinstance(marker, dict):
        out["marker_present"] = True
        out["indexed_page_count"] = marker.get("page_count")
        out["indexed_chunk_count"] = marker.get("chunk_count")
        out["built_at"] = marker.get("built_at")
        out["collection"] = marker.get("collection") or COLLECTION_NAME
        out["root_matches_marker"] = marker.get("index_root") == str(root)
        try:
            built_at = datetime.fromisoformat(str(marker.get("built_at")))
        except (TypeError, ValueError):
            built_at = None

    files = _indexable_pages(root)
    stems = {path.stem for path in files}
    out["disk_file_count"] = len(files)
    out["disk_page_count"] = len(stems)
    out["duplicate_slug_count"] = len(files) - len(stems)

    pending = 0
    if built_at is not None:
        if built_at.tzinfo is None:
            built_at = built_at.replace(tzinfo=timezone.utc)
        cutoff = built_at.timestamp()
        for path in files:
            try:
                if path.stat().st_mtime > cutoff:
                    pending += 1
            except OSError:
                continue
        out["age_hours"] = round(
            (datetime.now(timezone.utc) - built_at).total_seconds() / 3600, 2
        )
    out["pending_page_count"] = pending if built_at is not None else len(files)

    indexed = out["indexed_page_count"]
    out["page_count_delta"] = (
        out["disk_page_count"] - int(indexed) if isinstance(indexed, int) else None
    )
    out["stale"] = bool(
        not out["marker_present"]
        or not out["root_matches_marker"]
        or out["pending_page_count"]
        or out["page_count_delta"]
    )
    return out


def _write_marker(wiki_root: Path, page_count: int, chunk_count: int) -> None:
    marker = marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "index_root": str(wiki_root.resolve()),
                "collection": COLLECTION_NAME,
                "page_count": page_count,
                "chunk_count": chunk_count,
                "built_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


async def refresh_if_stale(*, wiki_root: Path | None = None) -> dict | None:
    """磁盘领先于索引时跑一次增量同步;新鲜则跳过,不调用嵌入 API。"""
    root = Path(wiki_root or settings.wiki_root_path).resolve()
    if _SYNC_LOCK.locked():
        return None
    state = await asyncio.to_thread(index_freshness, root)
    if not state["stale"]:
        return None
    async with _SYNC_LOCK:
        stat = await sync_query_vector_index(wiki_root=root)
    logger.info(
        f"query chunk index refreshed: {stat} "
        f"(pending={state['pending_page_count']} delta={state['page_count_delta']})"
    )
    return stat


async def _freshness_watcher(wiki_root: Path, interval: float) -> None:
    await asyncio.sleep(_FIRST_CHECK_DELAY_SECONDS)
    while True:
        try:
            await refresh_if_stale(wiki_root=wiki_root)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"query chunk index freshness check failed: {exc!r}")
        await asyncio.sleep(interval)


def start_freshness_watcher(
    *,
    wiki_root: Path | None = None,
    interval: float = FRESHNESS_CHECK_INTERVAL_SECONDS,
) -> None:
    """幂等启动后台新鲜度巡检。无事件循环(同步测试)时静默跳过。"""
    global _WATCHER_TASK
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    # 只认当前事件循环上的存活任务:测试里循环会反复创建销毁,旧任务不能挡住重新武装
    if (
        _WATCHER_TASK is not None
        and not _WATCHER_TASK.done()
        and _WATCHER_TASK.get_loop() is loop
    ):
        return
    root = Path(wiki_root or settings.wiki_root_path).resolve()
    _WATCHER_TASK = loop.create_task(_freshness_watcher(root, interval))


def _chunk_metadata(chunk: dict, page_hash: str, root_id: str) -> dict:
    return {
        "slug": chunk["slug"],
        "title": chunk["title"],
        "kind": chunk["kind"],
        "heading": chunk["heading"],
        "line_start": chunk["line_start"],
        "line_end": chunk["line_end"],
        "chunk_index": chunk["chunk_index"],
        "content_hash": chunk["content_hash"],
        "page_hash": page_hash,
        "index_root": root_id,
    }


async def sync_query_vector_index(*, wiki_root: Path, batch_size: int = 32) -> dict:
    """Upsert chunks of new/changed pages and remove stale rows for one Wiki root."""
    wiki_root = Path(wiki_root).resolve()
    root_id = str(wiki_root)

    # slug -> (page_hash, chunks)
    pages: dict[str, tuple[str, list[dict]]] = {}
    for path in iter_wiki_pages(wiki_root):
        if _is_generated_page(path, wiki_root):
            continue
        chunks = chunk_page(path)
        if chunks:
            pages[path.stem] = (page_content_hash(chunks), chunks)

    store = WikiVectorStore(
        persist_dir=settings.chroma_persist_dir,
        host=settings.chroma_host,
        port=settings.chroma_port,
        collection_name=COLLECTION_NAME,
    )
    existing = await store.list_records(where={"index_root": root_id})
    # 现存记录按 slug 分组: slug -> {"page_hash": str, "ids": [chunk_id]}
    existing_pages: dict[str, dict] = {}
    for chunk_id, metadata in existing.items():
        slug = str(metadata.get("slug") or chunk_id.split("::")[0])
        grp = existing_pages.setdefault(slug, {"page_hash": None, "ids": []})
        grp["ids"].append(chunk_id)
        if grp["page_hash"] is None:
            grp["page_hash"] = metadata.get("page_hash")

    changed = [
        slug
        for slug, (page_hash, _) in pages.items()
        if existing_pages.get(slug, {}).get("page_hash") != page_hash
    ]
    stale_slugs = sorted(set(existing_pages) - set(pages))

    provider = get_embedding_provider()
    upserted_chunks = 0
    for slug in changed:
        page_hash, chunks = pages[slug]
        # 先删该页旧 chunk(数量/边界可能变化,直接 upsert 会留孤儿)
        old_ids = existing_pages.get(slug, {}).get("ids") or []
        if old_ids:
            await store.delete(old_ids)
        for offset in range(0, len(chunks), max(1, batch_size)):
            batch = chunks[offset : offset + max(1, batch_size)]
            embeddings = await provider.embed([c["embed_text"] for c in batch])
            await store.upsert(
                [c["chunk_id"] for c in batch],
                embeddings,
                [_chunk_metadata(c, page_hash, root_id) for c in batch],
                documents=[c["text"] for c in batch],
            )
            upserted_chunks += len(batch)

    stale_ids = [
        chunk_id for slug in stale_slugs for chunk_id in existing_pages[slug]["ids"]
    ]
    await store.delete(stale_ids)

    total_chunks = sum(len(chunks) for _, chunks in pages.values())
    _write_marker(wiki_root, len(pages), total_chunks)
    from app.services.query.chunker import invalidate_chunk_cache
    from app.services.query.grep import invalidate_page_cache
    from app.services.query.hybrid import invalidate_vector_index_state

    invalidate_page_cache(wiki_root)
    invalidate_chunk_cache()
    invalidate_vector_index_state(wiki_root)
    return {
        "page_count": len(pages),
        "chunk_count": total_chunks,
        "upserted": len(changed),
        "upserted_chunks": upserted_chunks,
        "deleted": len(stale_slugs),
        "unchanged": len(pages) - len(changed),
    }
