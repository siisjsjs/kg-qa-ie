"""admin/config_store.py：配置读写（dev 期：JSON 文件 + fernet 加密）。

接口约定（与未来 DB 实现一致）：
  - get_sync / set_value_sync 同步版（factory 单例使用）
  - get / set_value 异步版（API 端点使用，内部包 to_thread）

存储位置：data/cache/system_config.json
格式：{ "<key>": {"value": "<plain_or_encrypted>", "encrypted": bool, "updated_at": "<iso>"} }
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.crypto import decrypt_value, encrypt_value, mask_secret

_LOCK = threading.RLock()
_CACHE: dict[str, dict[str, Any]] | None = None
_CACHE_PATH_USED: Path | None = None


def _store_path() -> Path:
    cache_dir = Path(settings.cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "system_config.json"


def _load() -> dict[str, dict[str, Any]]:
    global _CACHE, _CACHE_PATH_USED
    path = _store_path()
    with _LOCK:
        if _CACHE is None or _CACHE_PATH_USED != path:
            if path.exists():
                try:
                    _CACHE = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    _CACHE = {}
            else:
                _CACHE = {}
            _CACHE_PATH_USED = path
        return _CACHE


def _save() -> None:
    path = _store_path()
    with _LOCK:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_CACHE or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def reset_cache() -> None:
    """测试用：丢弃内存缓存，强制下次重新读盘。"""
    global _CACHE, _CACHE_PATH_USED
    with _LOCK:
        _CACHE = None
        _CACHE_PATH_USED = None


# ===== 同步 API（factory 用）=====


def get_sync(key: str, *, default: str | None = None) -> str | None:
    """读配置项，自动解密。未命中返回 default。"""
    store = _load()
    item = store.get(key)
    if item is None:
        return default
    raw = item.get("value", "")
    if item.get("encrypted"):
        return decrypt_value(raw) if raw else raw
    return raw


def get_many_sync(keys: list[str], *, mask: bool = False) -> dict[str, str]:
    """批量读。mask=True 时 encrypted 字段返回屏蔽值（前 4 位 + ****），其它原值返回。"""
    store = _load()
    out: dict[str, str] = {}
    for k in keys:
        item = store.get(k)
        if item is None:
            out[k] = ""
            continue
        raw = item.get("value", "")
        if item.get("encrypted"):
            plain = decrypt_value(raw) if raw else raw
            out[k] = mask_secret(plain) if mask else plain
        else:
            out[k] = raw
    return out


def set_value_sync(
    key: str,
    value: str,
    *,
    encrypted: bool = False,
    updated_by: int | None = None,
) -> None:
    """写配置项；encrypted=True 时存加密值。"""
    store = _load()
    payload = {
        "value": encrypt_value(value) if encrypted else value,
        "encrypted": bool(encrypted),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if updated_by is not None:
        payload["updated_by"] = updated_by
    store[key] = payload
    _save()


def set_many_sync(items: list[dict], *, updated_by: int | None = None) -> None:
    """批量写。items=[{key, value, encrypted}, ...]"""
    for it in items:
        set_value_sync(
            it["key"],
            it.get("value", "") or "",
            encrypted=bool(it.get("encrypted")),
            updated_by=updated_by,
        )


# ===== 异步 wrapper（API 端点用）=====


async def get(key: str, *, default: str | None = None) -> str | None:
    return await asyncio.to_thread(get_sync, key, default=default)


async def get_many(keys: list[str], *, mask: bool = False) -> dict[str, str]:
    return await asyncio.to_thread(get_many_sync, keys, mask=mask)


async def set_value(
    key: str,
    value: str,
    *,
    encrypted: bool = False,
    updated_by: int | None = None,
) -> None:
    await asyncio.to_thread(set_value_sync, key, value, encrypted=encrypted, updated_by=updated_by)


async def set_many(items: list[dict], *, updated_by: int | None = None) -> None:
    await asyncio.to_thread(set_many_sync, items, updated_by=updated_by)


__all__ = [
    "decrypt_value",
    "encrypt_value",
    "get",
    "get_many",
    "get_many_sync",
    "get_sync",
    "mask_secret",
    "reset_cache",
    "set_many",
    "set_many_sync",
    "set_value",
    "set_value_sync",
]
