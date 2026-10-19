"""wiki/cache.py：内容 hash 缓存（.wiki-cache.json）。

避免相同 md 重复进流水线;`raw/` 落盘后立即查 hash。
缓存键 = sha256(原始内容);命中时返回上次落盘的 source_slug,跳过 LLM。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

CACHE_FILENAME = ".wiki-cache.json"

_lock = Lock()


def _cache_path(wiki_root: Path) -> Path:
    return Path(wiki_root) / CACHE_FILENAME


def _load(wiki_root: Path) -> dict[str, dict]:
    p = _cache_path(wiki_root)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except json.JSONDecodeError:
        return {}


def _save(wiki_root: Path, data: dict[str, dict]) -> None:
    p = _cache_path(wiki_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def compute_hash(content: bytes | str) -> str:
    """sha256 hex digest;str 自动按 UTF-8 编码。"""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def is_hit(wiki_root: Path, content_hash: str) -> dict | None:
    """缓存命中返回旧记录;未命中返回 None。"""
    with _lock:
        return _load(wiki_root).get(content_hash)


def record(
    wiki_root: Path,
    *,
    content_hash: str,
    source_slug: str,
    entities: list[str] | None = None,
) -> None:
    """登记缓存。"""
    with _lock:
        data = _load(wiki_root)
        data[content_hash] = {
            "hash": content_hash,
            "source_slug": source_slug,
            "entities": list(entities or []),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }
        _save(wiki_root, data)


def evict(wiki_root: Path, content_hash: str) -> bool:
    """删除缓存条目;返回是否实际删除。"""
    with _lock:
        data = _load(wiki_root)
        if content_hash in data:
            del data[content_hash]
            _save(wiki_root, data)
            return True
        return False


def evict_by_source_slug(wiki_root: Path, source_slug: str) -> int:
    """删除所有指向某 source_slug 的缓存条目;返回删除条数(用于 delete 流程)。"""
    with _lock:
        data = _load(wiki_root)
        targets = [h for h, rec in data.items() if rec.get("source_slug") == source_slug]
        for h in targets:
            del data[h]
        if targets:
            _save(wiki_root, data)
        return len(targets)
