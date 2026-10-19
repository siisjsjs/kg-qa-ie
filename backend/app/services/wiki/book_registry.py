"""wiki/book_registry.py:声明式「书」注册表(books.json)。

`book` 字段落在 source frontmatter 上,build_book_index 从扫盘派生书目;
但**新建的空书**没有任何 source,因此需要一份独立注册表把它们记下来,
否则空书在概览页不可见、无法点进去往里放章节。

存储位置:`wiki_root/books.json`(在被扫描的 `wiki/` 子目录之外,
不会改 scan 签名、不污染缓存)。结构:
    {"books": ["基础工业工程", "我的新书", ...]}

注册表只存「书名」;章节顺序落在各 source 自己的 frontmatter.order 上。
"""

from __future__ import annotations

import json
from pathlib import Path

_FILENAME = "books.json"


def _registry_path(wiki_root: Path) -> Path:
    return Path(wiki_root).resolve() / _FILENAME


def list_declared_books(wiki_root: Path) -> list[str]:
    """读注册表里显式声明过的书名(去重、保序)。文件缺失返回空。"""
    p = _registry_path(wiki_root)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    books = data.get("books") if isinstance(data, dict) else None
    if not isinstance(books, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for b in books:
        name = str(b).strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _write(wiki_root: Path, books: list[str]) -> None:
    p = _registry_path(wiki_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"books": books}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_book(wiki_root: Path, name: str) -> bool:
    """登记一本新书。已存在返回 False,新增返回 True。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("book name is empty")
    books = list_declared_books(wiki_root)
    if name in books:
        return False
    books.append(name)
    _write(wiki_root, books)
    return True


def remove_book(wiki_root: Path, name: str) -> bool:
    """从注册表移除书名(不删任何 source 文件)。"""
    name = (name or "").strip()
    books = list_declared_books(wiki_root)
    if name not in books:
        return False
    _write(wiki_root, [b for b in books if b != name])
    return True
