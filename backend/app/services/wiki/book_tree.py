"""wiki/book_tree.py:把 wiki 数据按 `book` 维度切片成结构化书 / 章节 / 主题 / 实体树。

用于前端「Wiki 浏览」的初始页(4 本书 + 基础资料卡片 + 全局统计) +
书内目录树(source → topic → entity 三层) + 书内知识图谱子集。

设计:
  - **book 字段在 source frontmatter 上**,topic / entity 运行时从其 `sources[0]`
    对应的 source 继承 book(避免落盘冗余信息)。
  - 缺失 book 的 source 归类到 "未分类" 桶,前端不展示,但仍可通过书名访问。
  - 一次扫盘,共享 page 列表;Top-level API 只暴露 build_book_index / build_book_tree。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.services.wiki.book_registry import list_declared_books
from app.services.wiki.frontmatter_io import parse_text
from app.services.wiki.link_resolver import iter_wiki_pages, topic_member_slugs

UNCLASSIFIED = "未分类"

_KIND_DIR_TO_KIND = {
    "entities": "entity",
    "topics": "topic",
    "sources": "source",
    "queries": "query",
    "synthesis": "synthesis",
    "comparisons": "comparison",
}

# 进程内缓存:同一 wiki_root 在内容签名不变时复用扫描结果。
# 签名 = 每个 kind 子目录下所有 .md 文件的 (name, mtime_ns, size) 集合。
# 注意:不能只用「目录 mtime」——topic/entity 页被「就地改写」(write_text 原文件)
# 时父目录 mtime 不变(目录 mtime 只在增/删/改名条目时变)。classify_orphan_entities
# 把孤立实体追加进已有 topic 的 related_entities 就是就地改写 → 旧实现缓存永不失效
# → 前端始终看到分类前的「无主题分组」幽灵孤立。改为逐文件 stat 收敛到内容变更。
_SCAN_CACHE: dict[Path, tuple[tuple, dict[str, dict[str, dict]]]] = {}
# 派生索引(slug→book / source→referrers)缓存,与 scan 共享同一签名。
_DERIVED_CACHE: dict[Path, tuple[tuple, dict]] = {}


def _scan_signature(wiki_root: Path) -> tuple:
    """每个 kind 子目录下所有 .md 的 (name, mtime_ns, size) 签名,任一变化即失效。

    用 os.scandir 一次拿到 name + stat(不读文件内容),覆盖三种变更:
      - 增/删文件:文件集合变
      - 改名:name 变
      - 就地改写内容:mtime_ns / size 变(目录 mtime 不一定变,故必须逐文件 stat)
    ~1700 文件的 scandir+stat 远快于 parse,且只在签名命中失败时才重扫。
    """
    sig: list[tuple] = []
    for sub in _KIND_DIR_TO_KIND:
        d = wiki_root / "wiki" / sub
        files: list[tuple] = []
        try:
            with os.scandir(d) as it:
                for entry in it:
                    if not entry.name.endswith(".md") or not entry.is_file():
                        continue
                    try:
                        st = entry.stat()
                        files.append((entry.name, st.st_mtime_ns, st.st_size))
                    except OSError:
                        files.append((entry.name, 0, 0))
        except OSError:
            pass
        files.sort()
        sig.append((sub, tuple(files)))
    return tuple(sig)


def _kind_from_path(rel_parts: tuple[str, ...]) -> str | None:
    if len(rel_parts) >= 2 and rel_parts[0] == "wiki":
        return _KIND_DIR_TO_KIND.get(rel_parts[1])
    return None


def _scan_pages(wiki_root: Path) -> dict[str, dict[str, dict]]:
    """扫一次 wiki/**/*.md,按 kind 桶 + slug 索引。

    返回 `{kind: {slug: {fm, title, slug, kind}}}`,只含 6 种正式 kind 的页面。
    带进程内缓存:同 mtime 签名直接命中,避免重复 1700+ 文件读。
    """
    root = Path(wiki_root).resolve()
    sig = _scan_signature(root)
    cached = _SCAN_CACHE.get(root)
    if cached is not None and cached[0] == sig:
        return cached[1]

    out: dict[str, dict[str, dict]] = {k: {} for k in _KIND_DIR_TO_KIND.values()}
    for page in iter_wiki_pages(root):
        rel_parts = page.relative_to(root).parts
        kind = _kind_from_path(rel_parts)
        if kind is None:
            continue
        # 只接受 wiki/{kind_dir}/x.md 的扁平结构;wiki/synthesis/sessions/* 等不计入
        if len(rel_parts) != 3:
            continue
        try:
            fm, body = parse_text(page.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        slug = page.stem
        out[kind][slug] = {
            "fm": fm,
            "slug": slug,
            "title": str(fm.get("title") or slug),
            "kind": kind,
            # 仅 topic 留正文:目录树「主题→实体」以正文「## 关键实体」段为权威,
            # 与主题页右侧框口径一致。其它 kind 不留 body,省内存。
            "body": body if kind == "topic" else "",
        }
    _SCAN_CACHE[root] = (sig, out)
    return out


def _book_for_source(source_entry: dict | None) -> str:
    if not source_entry:
        return UNCLASSIFIED
    book = source_entry["fm"].get("book")
    return str(book) if book else UNCLASSIFIED


def _slug_to_book_map(scanned: dict[str, dict[str, dict]]) -> dict[tuple[str, str], set[str]]:
    """构造 (kind, slug) → {books} 映射(多值,支持多源实体跨书露出)。

    - source: 直接读 frontmatter.book(单本)→ {book}
    - topic / entity: 遍历 fm.sources **全集**,收每个 source 对应的 book(继承);
      被多本书引用的实体在每本书都计一次。sources 为空 / 目标 source 都不存在
      → {UNCLASSIFIED}。
    - query / synthesis / comparison: 不归任何书(不在树/图谱里)
    """
    sources_bucket = scanned["source"]
    out: dict[tuple[str, str], set[str]] = {}
    for slug, entry in sources_bucket.items():
        out[("source", slug)] = {_book_for_source(entry)}

    for kind in ("topic", "entity"):
        for slug, entry in scanned[kind].items():
            srcs = entry["fm"].get("sources") or []
            books: set[str] = set()
            for s in srcs:
                if s in sources_bucket:
                    books.add(_book_for_source(sources_bucket[s]))
            out[(kind, slug)] = books or {UNCLASSIFIED}
    return out


def _get_derived(wiki_root: Path) -> dict:
    """缓存:slug2book + source_to_refs(由 _scan_pages 派生),与 _SCAN_CACHE 共享签名。"""
    root = Path(wiki_root).resolve()
    sig = _scan_signature(root)
    cached = _DERIVED_CACHE.get(root)
    if cached is not None and cached[0] == sig:
        return cached[1]
    scanned = _scan_pages(root)
    derived = {
        "scanned": scanned,
        "slug2book": _slug_to_book_map(scanned),
        "source_to_refs": _build_source_to_referrers(scanned),
    }
    _DERIVED_CACHE[root] = (sig, derived)
    return derived


def build_book_index(wiki_root: Path) -> dict[str, Any]:
    """全局概览:每本书 {chapter_count, topic_count, entity_count} + 总计。

    返回:
        {
          "books": [
            {"book": "基础工业工程", "chapter_count": 15, "topic_count": ..., "entity_count": ...},
            ...
          ],
          "totals": {"books": 5, "chapters": 59, "topics": 211, "entities": 1410}
        }
    """
    derived = _get_derived(Path(wiki_root))
    scanned = derived["scanned"]
    slug2book = derived["slug2book"]

    book_stats: dict[str, dict[str, int]] = {}

    def _bump(book: str, key: str) -> None:
        if not book:
            book = UNCLASSIFIED
        d = book_stats.setdefault(book, {"chapter_count": 0, "topic_count": 0, "entity_count": 0})
        d[key] = d.get(key, 0) + 1

    for slug in scanned["source"]:
        for book in slug2book[("source", slug)]:
            _bump(book, "chapter_count")
    for slug in scanned["topic"]:
        for book in slug2book[("topic", slug)]:
            _bump(book, "topic_count")
    for slug in scanned["entity"]:
        for book in slug2book[("entity", slug)]:
            _bump(book, "entity_count")

    # 声明过但还没有任何 source 的空书也要出现在概览(否则点不进去往里放章节)
    for name in list_declared_books(Path(wiki_root)):
        if name and name != UNCLASSIFIED:
            book_stats.setdefault(
                name, {"chapter_count": 0, "topic_count": 0, "entity_count": 0}
            )

    # 输出按总章节数 desc 排序,UNCLASSIFIED 永远放最后
    items = []
    for book, s in book_stats.items():
        items.append({
            "book": book,
            "chapter_count": s["chapter_count"],
            "topic_count": s["topic_count"],
            "entity_count": s["entity_count"],
        })
    items.sort(key=lambda x: (x["book"] == UNCLASSIFIED, -x["chapter_count"]))

    totals = {
        "books": sum(1 for x in items if x["book"] != UNCLASSIFIED),
        "chapters": len(scanned["source"]),
        "topics": len(scanned["topic"]),
        "entities": len(scanned["entity"]),
    }
    return {"books": items, "totals": totals}


def _build_source_to_referrers(scanned: dict[str, dict[str, dict]]) -> dict[str, set[str]]:
    """source slug → 引用它的所有 (topic + entity) page slug 集合。

    通过遍历 topic / entity 的 frontmatter.sources 一次构建,避免 build_book_tree
    每章 N 次全盘扫描。
    """
    out: dict[str, set[str]] = {}
    for kind in ("topic", "entity"):
        for slug, entry in scanned[kind].items():
            for src in entry["fm"].get("sources") or []:
                out.setdefault(str(src), set()).add(slug)
    return out


def build_book_tree(book: str, wiki_root: Path) -> dict[str, Any]:
    """单本书的目录树:chapters → topics → entities + chapter.orphan_entities。

    每个 chapter (source):
      - {slug, title, kind: 'source', topics: [...], orphan_entities: [...]}
    每个 topic:
      - {slug, title, kind: 'topic', entities: [...]}
    每个 entity:
      - {slug, title, kind: 'entity'}

    orphan_entities = 引用此 source 但未被其下任一 topic 的 related_entities 覆盖的实体。
    """
    wiki_root = Path(wiki_root).resolve()
    derived = _get_derived(wiki_root)
    scanned = derived["scanned"]
    slug2book = derived["slug2book"]
    source_to_refs = derived["source_to_refs"]

    sources_bucket = scanned["source"]
    topics_bucket = scanned["topic"]
    entities_bucket = scanned["entity"]

    # 1) 该书下的 source(章节);优先按显式 order,缺失退到 created+title
    book_sources = [
        e for slug, e in sources_bucket.items()
        if book in slug2book[("source", slug)]
    ]

    def _order_key(e: dict) -> tuple:
        raw = e["fm"].get("order")
        try:
            has_order = raw is not None and str(raw) != ""
            ordv = float(raw) if has_order else 0.0
        except (TypeError, ValueError):
            has_order, ordv = False, 0.0
        # 有 order 的排前(0),组内按 order;无 order 的排后(1),按 created+title
        return (0 if has_order else 1, ordv, e["fm"].get("created") or "", e["title"])

    book_sources.sort(key=_order_key)

    chapters: list[dict[str, Any]] = []
    for src in book_sources:
        src_slug = src["slug"]
        # 引用此 source 的页面 slug(同时含 topic 和 entity);O(1) 查表。
        # 注意:referenced 混入了「跨素材实体」带来的 slug —— 实体可合法横跨多个
        # source,所以它的 fm.sources 含本章节时,其 slug 会出现在这里。该集合
        # 用于「挑孤立实体」是对的,但**不能**用来挑主题:否则当某实体与某主题
        # 同名时(如「管理事务分析」既是 entity 又是 topic),同名主题会被这个
        # 实体的跨素材引用误挂到别的章节下。主题必须只认它自己的 fm.sources。
        referenced = source_to_refs.get(src_slug, set())

        # 主题:只认 topic 自己的 fm.sources 是否含本章节(不借实体反查),
        # 避免同名实体把主题串到别的素材章节。
        chapter_topics: list[dict[str, Any]] = []
        topic_covered_entities: set[str] = set()

        topic_slugs = sorted(
            slug for slug, t in topics_bucket.items()
            if src_slug in (t["fm"].get("sources") or [])
        )
        for slug in topic_slugs:
            t = topics_bucket.get(slug)
            if t is None:
                continue
            # 主题成员实体以正文「## 关键实体」段为权威(与主题页右侧框一致);
            # 正文无该段的老页/极简页退回 fm.related_entities。
            related_clean = topic_member_slugs(t.get("body") or "")
            if not related_clean:
                related = t["fm"].get("related_entities") or []
                related_clean = [str(x) for x in related if x]
            ents = []
            for ent_slug in related_clean:
                ent_entry = entities_bucket.get(ent_slug)
                ents.append({
                    "slug": ent_slug,
                    "title": ent_entry["title"] if ent_entry else ent_slug,
                    "kind": "entity",
                    "exists": ent_entry is not None,
                })
                if ent_entry is not None:
                    topic_covered_entities.add(ent_slug)
            chapter_topics.append({
                "slug": t["slug"],
                "title": t["title"],
                "kind": "topic",
                "entities": ents,
            })

        chapter_topics.sort(key=lambda x: x["title"])

        # orphan: 引用此 source 的实体,且未被任何 topic 涵盖
        orphan_entities = []
        for slug in sorted(referenced):
            if slug in topic_covered_entities:
                continue
            ent_entry = entities_bucket.get(slug)
            if ent_entry is None:
                continue
            orphan_entities.append({
                "slug": slug,
                "title": ent_entry["title"],
                "kind": "entity",
            })

        chapters.append({
            "slug": src["slug"],
            "title": src["title"],
            "kind": "source",
            "topics": chapter_topics,
            "orphan_entities": orphan_entities,
        })

    return {
        "book": book,
        "chapter_count": len(chapters),
        "chapters": chapters,
    }


def filter_graph_by_book(graph_data: dict, book: str, wiki_root: Path) -> dict:
    """把全量 graph-data.json 按 book 过滤为该书子图。

    - 只保留 (kind, slug) 在该书的 nodes
    - 丢弃端点不在子集内的 edges
    - communities / insights 一并按节点裁剪;不重新计算社区
    """
    derived = _get_derived(Path(wiki_root))
    slug2book = derived["slug2book"]

    keep_node_ids: set[str] = set()
    nodes_out: list[dict] = []
    for n in graph_data.get("nodes", []):
        kind = n.get("kind")
        slug = n.get("slug")
        if kind not in ("source", "topic", "entity"):
            # 防御性:即便后端已剔除,这里再兜底一次
            continue
        if book not in slug2book.get((kind, slug), set()):
            continue
        nodes_out.append(n)
        keep_node_ids.add(n["id"])

    edges_out = [
        e for e in graph_data.get("edges", [])
        if e.get("source") in keep_node_ids and e.get("target") in keep_node_ids
    ]

    communities_in = graph_data.get("communities") or {}
    communities_out: dict[str, list[str]] = {}
    for cid, members in communities_in.items():
        kept = [m for m in members if m in keep_node_ids]
        if kept:
            communities_out[str(cid)] = kept

    insights_out = []
    for ins in graph_data.get("insights", []) or []:
        related = ins.get("related_slugs") or []
        if any(s in {n.get("slug") for n in nodes_out} for s in related):
            insights_out.append(ins)

    return {
        "nodes": nodes_out,
        "edges": edges_out,
        "communities": communities_out,
        "insights": insights_out,
        "generated_at": graph_data.get("generated_at"),
    }
