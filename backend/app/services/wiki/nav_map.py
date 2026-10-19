"""wiki/nav_map.py:自动生成「知识库导航 map」(md 大纲),供智能问答检索路由。

每次 ingest / 删除 / 改名 / 移动章节后由 pipeline / wiki API 触发重建:
  - 每本书一份:`wiki_root/nav/{book_slug}.md`
    章节(source) → 主题(topic) → 实体(entity) 三层大纲,每个叶子带 slug + 简介,
    QA 可直接 grep 这一份文件就知道「某概念存不存在 / 在哪本书哪章」。
  - 全局总览:`wiki_root/nav-map.md`
    列出每本书的计数与指向各书导航文件的相对路径 + 全局统计。

存储在 `wiki/` 子目录之外(nav/ 与 nav-map.md 都在 wiki_root 根),
不改 _scan_signature 的 6 个 kind 目录 mtime,因此不会污染 book_tree 扫描缓存。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from app.services.wiki.book_tree import (
    UNCLASSIFIED,
    build_book_index,
    build_book_tree,
)
from app.services.wiki.fs_io import atomic_write, slugify
from app.services.wiki.frontmatter_io import parse_text

_NAV_DIR = "nav"
_GLOBAL_FILE = "nav-map.md"
_BRIEF_MAX = 120


def _extract_brief(body: str, *, max_chars: int = _BRIEF_MAX) -> str:
    """从 body 取「## 简介」段首段;没有则取首段非标题文本。截断到 max_chars。"""
    in_brief = False
    buf: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("## 简介"):
            in_brief = True
            continue
        if in_brief:
            if s.startswith("## "):
                break
            if s and not s.startswith("<!--"):
                buf.append(s)
            elif buf:
                break
    text = ""
    if buf:
        text = " ".join(buf)
    else:
        for para in body.split("\n\n"):
            para = para.strip()
            if para and not para.startswith("#") and not para.startswith("<!--"):
                text = para
                break
    text = " ".join(text.split())  # 折叠空白
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


_KIND_DIR = {"source": "sources", "topic": "topics", "entity": "entities"}


def _brief_for(wiki_root: Path, kind: str, slug: str) -> str:
    """读单页 body 取简介;文件缺失 / 解析失败返回空串(不阻塞)。"""
    sub = _KIND_DIR.get(kind)
    if not sub:
        return ""
    p = Path(wiki_root) / "wiki" / sub / f"{slug}.md"
    try:
        _, body = parse_text(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    return _extract_brief(body)


def build_nav_map_md(book: str, wiki_root: Path) -> str:
    """单本书的导航 md:章节 → 主题 → 实体三层大纲,叶子带 slug + 简介。"""
    wiki_root = Path(wiki_root)
    tree = build_book_tree(book, wiki_root)
    chapters = tree.get("chapters") or []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    lines: list[str] = [
        f"# 导航 · {book}",
        "",
        f"<!-- 自动生成,请勿手改;更新于 {now} -->",
        "",
        f"共 {len(chapters)} 章节。每个条目格式:标题 · `slug` — 简介。",
        "",
    ]

    if not chapters:
        lines.append("_(本知识库暂无章节)_")
        lines.append("")
        return "\n".join(lines)

    for ci, ch in enumerate(chapters, 1):
        c_slug = ch["slug"]
        c_brief = _brief_for(wiki_root, "source", c_slug)
        head = f"## {ci}. {ch['title']} · `{c_slug}`"
        lines.append(head)
        if c_brief:
            lines.append(f"> {c_brief}")
        lines.append("")

        for t in ch.get("topics") or []:
            t_slug = t["slug"]
            t_brief = _brief_for(wiki_root, "topic", t_slug)
            suffix = f" — {t_brief}" if t_brief else ""
            lines.append(f"- **主题** {t['title']} · `{t_slug}`{suffix}")
            for e in t.get("entities") or []:
                # 经过 ensure_zero_void + orphan 分组,实体均应存在;
                # 极少数仍不存在的(理论上不该发生)直接跳过,不渲染"待补"占位。
                if not e.get("exists", True):
                    continue
                e_brief = _brief_for(wiki_root, "entity", e["slug"])
                e_suffix = f" — {e_brief}" if e_brief else ""
                lines.append(f"  - 实体 {e['title']} · `{e['slug']}`{e_suffix}")

        # 孤立实体在 post_process 阶段已被 synthesize_orphan_topics 归入主题,
        # 正常情况下 orphan_entities 为空。若仍有残留,兜底挂到一个"其它"分组下,
        # 确保它们仍可见且"有分组",而不是裸露为"散落实体"。
        orphans = ch.get("orphan_entities") or []
        if orphans:
            lines.append("- **主题** 其它")
            for e in orphans:
                e_brief = _brief_for(wiki_root, "entity", e["slug"])
                e_suffix = f" — {e_brief}" if e_brief else ""
                lines.append(f"  - 实体 {e['title']} · `{e['slug']}`{e_suffix}")
        lines.append("")

    return "\n".join(lines)


def _book_slug(book: str) -> str:
    """书名 → 文件名安全 slug;失败兜底 hash 串避免抛错。"""
    try:
        return slugify(book)
    except ValueError:
        return "book-" + str(abs(hash(book)) % (10**8))


async def write_nav_maps(wiki_root: Path) -> dict:
    """重建所有书的导航 md + 全局 nav-map.md。返回写入统计。

    fire-and-forget 调用方负责吞异常;本函数内单书失败不影响其它书。
    """
    wiki_root = Path(wiki_root)
    index = build_book_index(wiki_root)
    books = index.get("books") or []
    totals = index.get("totals") or {}
    nav_dir = wiki_root / _NAV_DIR

    written: list[str] = []
    for b in books:
        name = b["book"]
        if name == UNCLASSIFIED:
            continue  # 未分类桶不出导航(QA 不路由到它)
        try:
            md = build_nav_map_md(name, wiki_root)
            path = nav_dir / f"{_book_slug(name)}.md"
            await atomic_write(path, md)
            written.append(name)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"nav_map: book {name!r} 导航生成失败(跳过): {e}")

    # 全局总览
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    g_lines: list[str] = [
        "# 知识库导航总览",
        "",
        f"<!-- 自动生成,请勿手改;更新于 {now} -->",
        "",
        (
            f"全局统计:{totals.get('books', 0)} 本书 · "
            f"{totals.get('chapters', 0)} 章节 · "
            f"{totals.get('topics', 0)} 主题 · "
            f"{totals.get('entities', 0)} 实体。"
        ),
        "",
        "| 知识库 | 章节 | 主题 | 实体 | 导航文件 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for b in books:
        name = b["book"]
        if name == UNCLASSIFIED:
            continue
        rel = f"{_NAV_DIR}/{_book_slug(name)}.md"
        g_lines.append(
            f"| {name} | {b['chapter_count']} | {b['topic_count']} | "
            f"{b['entity_count']} | [{rel}]({rel}) |"
        )
    g_lines.append("")
    await atomic_write(wiki_root / _GLOBAL_FILE, "\n".join(g_lines))

    logger.info(f"nav_map: 重建完成 books={len(written)} global={_GLOBAL_FILE}")
    return {"books_written": written, "global": _GLOBAL_FILE}
