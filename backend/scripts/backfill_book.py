"""一次性脚本:给 wiki/sources/*.md 的 frontmatter 回填 `book` 字段。

回填规则(基于文件名日期前缀 + 章节数字体系):
  - 精确文件名 `2026-05-24-制造业：定义、分类与发展现状.md` → 基础资料
  - 精确文件名 `2026-05-25-精益生产概述.md`              → 精益生产
  - 前缀 `2026-05-19-` / `2026-05-22-`                   → 基础工业工程
  - 前缀 `2026-05-23-`                                    → 质量管理
  - 前缀 `2026-05-24-`(已剔除制造业一篇)                → 质量管理
  - 前缀 `2026-05-25-` + 阿拉伯数字章节(第\\d+章)        → 生产计划与管理
  - 前缀 `2026-05-25-` + 中文数字章节(第[一-十]+章)      → 精益生产

预期分布:基础工业工程 15 / 生产计划与管理 14 / 精益生产 13 / 质量管理 16 / 基础资料 1 = 59

用法:
  cd D:/kg-qa-ie/backend
  python scripts/backfill_book.py --dry-run        # 仅打印映射
  python scripts/backfill_book.py                  # 实际写入(merge_frontmatter,不 bump version)
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.services.wiki.frontmatter_io import parse_text, serialize  # noqa: E402
from app.services.wiki.fs_io import atomic_write  # noqa: E402

BOOK_FOUNDATION = "基础工业工程"
BOOK_PRODUCTION = "生产计划与管理"
BOOK_LEAN = "精益生产"
BOOK_QUALITY = "质量管理"
BOOK_BASIC = "基础资料"

EXACT_OVERRIDES = {
    "2026-05-24-制造业：定义、分类与发展现状.md": BOOK_BASIC,
    "2026-05-25-精益生产概述.md": BOOK_LEAN,
}

ARABIC_CHAPTER = re.compile(r"第-?\d+-?章")
CHINESE_CHAPTER = re.compile(r"第[一二三四五六七八九十百零]+章")


def classify(filename: str) -> str | None:
    if filename in EXACT_OVERRIDES:
        return EXACT_OVERRIDES[filename]

    if filename.startswith("2026-05-19-") or filename.startswith("2026-05-22-"):
        return BOOK_FOUNDATION
    if filename.startswith("2026-05-23-"):
        return BOOK_QUALITY
    if filename.startswith("2026-05-24-"):
        return BOOK_QUALITY
    if filename.startswith("2026-05-25-"):
        if ARABIC_CHAPTER.search(filename):
            return BOOK_PRODUCTION
        if CHINESE_CHAPTER.search(filename):
            return BOOK_LEAN
        return BOOK_LEAN
    return None


async def write_book_field(path: Path, book: str) -> None:
    text = path.read_text(encoding="utf-8")
    fm, body = parse_text(text)
    fm["book"] = book
    new_text = serialize(fm, body)
    await atomic_write(path, new_text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印映射,不写入")
    args = parser.parse_args()

    sources_dir = settings.wiki_root_path / "wiki" / "sources"
    if not sources_dir.exists():
        print(f"[ERROR] sources dir not found: {sources_dir}")
        return 1

    files = sorted(sources_dir.glob("*.md"))
    if not files:
        print(f"[WARN] no .md files in {sources_dir}")
        return 0

    counts: dict[str, int] = {}
    unclassified: list[str] = []
    rows: list[tuple[Path, str]] = []
    for p in files:
        book = classify(p.name)
        if book is None:
            unclassified.append(p.name)
            continue
        counts[book] = counts.get(book, 0) + 1
        rows.append((p, book))

    print(f"sources dir : {sources_dir}")
    print(f"total files : {len(files)}")
    print("分类映射:")
    for book in (BOOK_FOUNDATION, BOOK_PRODUCTION, BOOK_LEAN, BOOK_QUALITY, BOOK_BASIC):
        print(f"  - {book:<10s} : {counts.get(book, 0)}")
    if unclassified:
        print(f"未分类 ({len(unclassified)} 个):")
        for n in unclassified:
            print(f"  ! {n}")

    print("\n详细映射 (filename -> book):")
    for p, book in rows:
        print(f"  [{book}] {p.name}")

    if args.dry_run:
        print("\n--dry-run 已退出,未写入文件。")
        return 0

    print("\n开始写入...")
    written = 0
    skipped = 0
    for p, book in rows:
        text = p.read_text(encoding="utf-8")
        fm, body = parse_text(text)
        if fm.get("book") == book:
            skipped += 1
            continue
        asyncio.run(write_book_field(p, book))
        written += 1
    print(f"写入完成: {written} 个文件已更新,{skipped} 个已跳过(book 字段已正确)。")
    if unclassified:
        print(f"[WARN] {len(unclassified)} 个文件未分类,需人工处理。")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
