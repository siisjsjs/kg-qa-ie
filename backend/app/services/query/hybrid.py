"""Hybrid retrieval (chunk-level): lexical + optional vector search + deterministic reranking.

改造计划 §4.3:
  - 语义腿直接返回 chunk 命中(含 text/行号),阈值仍 0.35
  - 词法腿 grep_wiki 返回不动;页面 hit_segments 按行号落到所属 chunk,
    页面 grep 得分 × 命中集中度分摊给对应 chunk
  - 融合:chunk 级 RRF(k=60)+ 确定性 rerank(0.35 lex + 0.35 sem
    + 0.2 title_overlap + 0.1 hits)
  - 每页最多 3 个 chunk 进最终结果,默认返回 top 10 chunk
  - retrievable_slugs 陈旧行过滤逻辑保留(按 slug 判断)
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from pathlib import Path
from typing import Awaitable, Callable

from loguru import logger

from app.core.config import settings
from app.services.embedding.factory import get_embedding_provider
from app.services.embedding.vector_store import WikiVectorStore
from app.services.query.chunker import chunk_page_cached
from app.services.query.grep import grep_wiki, retrievable_slugs
from app.services.query.vector_index import (
    COLLECTION_NAME,
    marker_path,
    start_freshness_watcher,
)

SemanticSearcher = Callable[[str, int], Awaitable[list[dict]]]
RetrievalStats = dict[str, object]
_WORD_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9-]+")
_QUESTION_SPLIT_RE = re.compile(
    r"(?:是什么|有什么|有哪些|包括哪些|应该|怎样|怎么|如何|哪些|什么|是否|可以|包括|内容|的|与|和)"
)
# 每页最多进最终结果的 chunk 数(防单页霸榜)
PER_PAGE_CHUNK_CAP = 3
# (检查时刻, 是否有索引, 标记文件 mtime_ns);只缓存成功探测,失败与重建立即失效
_INDEX_STATE_CACHE: dict[str, tuple[float, bool, int]] = {}
_LAST_STATS: RetrievalStats = {}


def new_retrieval_stats() -> RetrievalStats:
    """构造一个 hybrid_search 可写入的可变统计对象。"""
    return {
        "semantic_ok": False,
        "semantic_degraded": False,
        "semantic_reason": None,
        "semantic_error": None,
        "semantic_count": 0,
        "lexical_count": 0,
    }


def last_retrieval_stats() -> RetrievalStats:
    """返回最近一次 hybrid_search 的统计快照(未传 stats 时的兜底读取通道)。"""
    return dict(_LAST_STATS)


def invalidate_vector_index_state(wiki_root: Path | None = None) -> None:
    if wiki_root is None:
        _INDEX_STATE_CACHE.clear()
    else:
        _INDEX_STATE_CACHE.pop(str(Path(wiki_root).resolve()), None)


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD_RE.finditer(text or "")}


def expand_search_terms(question: str, keywords: list[str]) -> list[str]:
    """Split common Chinese question wrappers while preserving domain phrases."""
    out = list(dict.fromkeys(term for term in keywords if term and term.strip()))
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", question):
        for term in _QUESTION_SPLIT_RE.split(chunk):
            term = term.strip()
            if len(term) >= 2 and term not in out:
                out.append(term)
    return out


def _rrf(rank: int, *, k: int = 60) -> float:
    return 1.0 / (k + rank)


async def _default_semantic_search(
    question: str,
    top_k: int,
    wiki_root: Path,
    stats: RetrievalStats | None = None,
) -> list[dict]:
    def _bail(reason: str) -> list[dict]:
        if stats is not None:
            stats["semantic_reason"] = reason
        return []

    marker_file = marker_path()
    try:
        marker = json.loads(marker_file.read_text(encoding="utf-8"))
        marker_version = marker_file.stat().st_mtime_ns
    except (OSError, ValueError):
        return _bail("marker_missing")
    resolved_root = str(Path(wiki_root).resolve())
    if marker.get("index_root") != resolved_root:
        return _bail("index_root_mismatch")
    store = WikiVectorStore(
        persist_dir=settings.chroma_persist_dir,
        host=settings.chroma_host,
        port=settings.chroma_port,
        collection_name=COLLECTION_NAME,
    )
    index_filter = {"index_root": resolved_root}
    root_key = index_filter["index_root"]
    cached = _INDEX_STATE_CACHE.get(root_key)
    if cached and cached[2] == marker_version and time.monotonic() - cached[0] < 60:
        has_index = cached[1]
    else:
        try:
            has_index = await store.has_records(where=index_filter)
        except Exception as exc:
            logger.warning(f"query chunk index probe failed: {exc!r}")
            _INDEX_STATE_CACHE.pop(root_key, None)
            raise
        _INDEX_STATE_CACHE[root_key] = (time.monotonic(), has_index, marker_version)
    if not has_index:
        return _bail("collection_empty")
    provider = get_embedding_provider()
    vectors = await provider.embed([question])
    if not vectors:
        return _bail("embedding_empty")
    hits = await store.search_similar(
        vectors[0], top_k=top_k, where=index_filter, include_documents=True
    )
    out: list[dict] = []
    for hit in hits:
        out.append(
            {
                "chunk_id": hit.get("id") or hit.get("slug"),
                "slug": hit.get("slug"),
                "title": hit.get("title"),
                "kind": hit.get("kind"),
                "heading": hit.get("heading") or "",
                "text": hit.get("document") or "",
                "line_start": hit.get("line_start"),
                "line_end": hit.get("line_end"),
                "semantic_score": hit.get("semantic_score"),
            }
        )
    return out


def _lexical_page_to_chunks(page_hit: dict, wiki_root: Path) -> list[dict]:
    """把一个页面级 grep 命中映射为 chunk 级词法候选。

    页面 grep 得分按命中集中度(该 chunk 命中行数 / 页面总命中行数)分摊;
    无 hit_segments(纯标题/别名命中)时全额记到第一个 chunk。
    """
    file_path = page_hit.get("file_path")
    if not file_path:
        return []
    chunks = chunk_page_cached(Path(wiki_root) / str(file_path))
    if not chunks:
        return []
    page_score = float(page_hit.get("score") or 0)
    segments = page_hit.get("hit_segments") or []

    hit_lines = [s.get("line") for s in segments if s.get("line")]
    per_chunk_hits: dict[str, int] = {}
    for line in hit_lines:
        for c in chunks:
            if c["line_start"] <= line <= c["line_end"]:
                per_chunk_hits[c["chunk_id"]] = per_chunk_hits.get(c["chunk_id"], 0) + 1
                break
    if not per_chunk_hits:
        per_chunk_hits = {chunks[0]["chunk_id"]: 0}

    total_hits = sum(per_chunk_hits.values())
    by_id = {c["chunk_id"]: c for c in chunks}
    out: list[dict] = []
    for chunk_id, hits in per_chunk_hits.items():
        c = by_id[chunk_id]
        share = hits / total_hits if total_hits > 0 else 1.0
        out.append(
            {
                "chunk_id": chunk_id,
                "slug": c["slug"],
                "title": c["title"],
                "kind": c["kind"],
                "heading": c["heading"],
                "text": c["text"],
                "line_start": c["line_start"],
                "line_end": c["line_end"],
                "lexical_score": page_score * share,
                "hit_count": hits,
            }
        )
    return out


def _norm_chunk_id(item: dict) -> str:
    """候选缺 chunk_id 时按 slug 兜底(兼容页级候选/自定义 searcher)。"""
    cid = item.get("chunk_id")
    if cid:
        return str(cid)
    cid = f"{item.get('slug') or ''}::00"
    item["chunk_id"] = cid
    return cid


def fuse_and_rerank(
    *,
    question: str,
    keywords: list[str],
    lexical: list[dict],
    semantic: list[dict],
    top_k: int,
    per_page_cap: int = PER_PAGE_CHUNK_CAP,
) -> list[dict]:
    """chunk 级 RRF 融合 + 确定性 rerank。lexical/semantic 均为 chunk 候选列表。"""
    lexical = sorted(
        lexical,
        key=lambda x: (-float(x.get("lexical_score") or 0), _norm_chunk_id(x)),
    )
    merged: dict[str, dict] = {}
    for rank, item in enumerate(lexical, start=1):
        row = merged.setdefault(_norm_chunk_id(item), dict(item))
        row["lexical_rank"] = rank
        row["lexical_score"] = float(item.get("lexical_score") or 0)
        row["fusion_score"] = row.get("fusion_score", 0.0) + _rrf(rank)
    for rank, item in enumerate(semantic, start=1):
        row = merged.setdefault(_norm_chunk_id(item), dict(item))
        row["semantic_rank"] = rank
        row["semantic_score"] = float(item.get("semantic_score") or 0)
        row["fusion_score"] = row.get("fusion_score", 0.0) + _rrf(rank)
        # 语义腿带原文,词法腿已有 text 时不覆盖
        if not row.get("text"):
            row["text"] = item.get("text") or ""

    query_tokens = _tokens(question) | {k.lower() for k in keywords if k}
    max_lex = max(
        (float(x.get("lexical_score") or 0) for x in merged.values()), default=1.0
    )
    # grep 零命中、只有向量腿返回结果时 max_lex == 0,log1p(0) 会除零——不能崩。
    lex_denom = math.log1p(max_lex)
    for row in merged.values():
        title_tokens = _tokens(
            f"{row.get('slug', '')} {row.get('title', '')} {row.get('heading', '')}"
        )
        overlap = len(query_tokens & title_tokens) / max(1, len(query_tokens))
        lexical_norm = (
            math.log1p(float(row.get("lexical_score") or 0)) / lex_denom
            if lex_denom > 0
            else 0.0
        )
        semantic_score = float(row.get("semantic_score") or 0)
        row["rerank_score"] = round(
            0.35 * lexical_norm
            + 0.35 * semantic_score
            + 0.2 * overlap
            + 0.1 * min(1.0, int(row.get("hit_count") or 0) / 3),
            6,
        )
        row["score"] = round(row["rerank_score"] * 100, 2)
        row["retrieval_sources"] = [
            name
            for name, present in (
                ("lexical", "lexical_rank" in row),
                ("semantic", "semantic_rank" in row),
            )
            if present
        ]

    ranked = sorted(
        merged.values(),
        key=lambda x: (-x["rerank_score"], -x.get("fusion_score", 0), x["chunk_id"]),
    )
    # 每页最多 per_page_cap 个 chunk,防单页霸榜
    out: list[dict] = []
    per_page: dict[str, int] = {}
    for row in ranked:
        slug = str(row.get("slug") or "")
        if per_page.get(slug, 0) >= per_page_cap:
            continue
        per_page[slug] = per_page.get(slug, 0) + 1
        out.append(row)
        if len(out) >= top_k:
            break
    return out


async def hybrid_search(
    *,
    question: str,
    keywords: list[str],
    wiki_root: Path,
    top_k: int = 10,
    candidate_k: int = 20,
    semantic_searcher: SemanticSearcher | None = None,
    stats: RetrievalStats | None = None,
) -> list[dict]:
    """chunk 级混合检索。语义腿失败只降级不抛错,降级信号写入 `stats`。

    返回 [{chunk_id, slug, title, kind, heading, text, line_start, line_end,
           score, retrieval_sources}]。
    """
    if stats is None:
        stats = new_retrieval_stats()
    else:
        stats.update(new_retrieval_stats())
    start_freshness_watcher(wiki_root=Path(wiki_root))
    search_terms = expand_search_terms(question, keywords)
    lexical_task = asyncio.create_task(
        grep_wiki(keywords=search_terms, wiki_root=wiki_root, top_k=candidate_k)
    )
    semantic: list[dict] = []
    try:
        if semantic_searcher:
            semantic = await semantic_searcher(question, candidate_k)
        else:
            semantic = await _default_semantic_search(
                question, candidate_k, Path(wiki_root), stats
            )
        stats["semantic_ok"] = True
    except Exception as exc:  # semantic retrieval is an optional enhancement
        stats["semantic_reason"] = "exception"
        stats["semantic_error"] = f"{type(exc).__name__}: {exc}"
        logger.warning(
            f"semantic retrieval unavailable, using lexical fallback: {exc!r}"
        )
    lexical_pages = await lexical_task
    # Chroma may contain stale rows after a page deletion or when tests switch wiki roots.
    valid_slugs = retrievable_slugs(Path(wiki_root))
    semantic = [item for item in semantic if item.get("slug") in valid_slugs]
    lexical: list[dict] = []
    for page_hit in lexical_pages:
        lexical.extend(_lexical_page_to_chunks(page_hit, Path(wiki_root)))
    stats["semantic_count"] = len(semantic)
    stats["lexical_count"] = len(lexical)
    stats["semantic_degraded"] = not semantic
    if stats["semantic_degraded"]:
        if stats["semantic_reason"] is None:
            stats["semantic_reason"] = "no_semantic_candidates"
        logger.warning(
            f"retrieval degraded to lexical-only: reason={stats['semantic_reason']} "
            f"lexical={len(lexical)} error={stats['semantic_error']}"
        )
    _LAST_STATS.clear()
    _LAST_STATS.update(stats)
    return fuse_and_rerank(
        question=question,
        keywords=search_terms,
        lexical=lexical,
        semantic=semantic,
        top_k=top_k,
    )
