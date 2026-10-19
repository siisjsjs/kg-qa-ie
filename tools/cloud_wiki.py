"""wiki:页面 CRUD + index + 删除素材 + 反链 + 版本历史。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from loguru import logger

from app.core.config import settings
from app.core.deps import AdminUserId, CurrentUserId, DbSession
from app.models.slug_redirect import SlugRedirect
from app.models.wiki_alias import WikiAlias
from app.models.wiki_link import WikiLink
from app.models.wiki_node_version import WikiNodeVersion
from app.models.wiki_page import WikiPage
from app.schemas.common import OkResponse
from app.schemas.wiki import (
    BacklinkItem,
    BacklinksResponse,
    BookIndex,
    BookTree,
    CreateBookRequest,
    CreateBookResponse,
    DeletePageResponse,
    DeleteSourceResponse,
    MoveChapterRequest,
    NodeVersionDetail,
    NodeVersionList,
    NodeVersionListItem,
    MentioningTopic,
    MiniGraph,
    MiniGraphEdge,
    MiniGraphNode,
    RelatedEntity,
    RelationsResponse,
    RenamePageRequest,
    ReorderChaptersRequest,
    ReorderChaptersResponse,
    SearchResponse,
    SearchResultItem,
    SimpleOkResponse,
    SourceRef,
    WikiIndex,
    WikiIndexEntry,
    WikiPageDetail,
    WikiPageUpdate,
)
from app.services.wiki.book_registry import add_book, list_declared_books
from app.services.wiki.book_tree import (
    build_book_index,
    build_book_tree,
    filter_graph_by_book,
)
from app.services.wiki.delete import delete_page as delete_page_service
from app.services.wiki.delete import delete_source as delete_source_service
from app.services.wiki.frontmatter_io import (
    merge_frontmatter,
    parse_text,
    serialize,
)
from app.services.wiki.fs_io import atomic_write, safe_join
from app.services.wiki.link_resolver import (
    extract_links,
    extract_section_links,
    find_backlinks,
    iter_wiki_pages,
    topic_member_slugs,
)
from app.services.wiki.slug_redirect import lookup as redirect_lookup
from app.services.wiki.slug_redirect import resolve_chain
from app.services.wiki.version_history import (
    compute_diff,
    list_versions,
    write_version,
)

router = APIRouter()


_KIND_DIR = {
    "entity": "entities",
    "topic": "topics",
    "source": "sources",
    "query": "queries",
    "synthesis": "synthesis",
    "comparison": "comparisons",
}


def _resolve_path(slug: str, wiki_root: Path, hint_kind: str | None = None) -> Path | None:
    if hint_kind and hint_kind in _KIND_DIR:
        candidate = safe_join(wiki_root, "wiki", _KIND_DIR[hint_kind], f"{slug}.md")
        if candidate.exists():
            return candidate
    for sub in _KIND_DIR.values():
        p = safe_join(wiki_root, "wiki", sub, f"{slug}.md")
        if p.exists():
            return p
    return None


def _path_to_kind(rel: str) -> str:
    parts = rel.split("/")
    if len(parts) >= 2 and parts[0] == "wiki":
        return next((k for k, d in _KIND_DIR.items() if d == parts[1]), "entity")
    return "entity"


def _enrich_links(slugs: list[str], wiki_root: Path) -> list[WikiIndexEntry]:
    """把 [slug, slug, ...] 富化成 [{slug, title, kind}],缺页用 slug 兜底。"""
    out: list[WikiIndexEntry] = []
    seen: set[str] = set()
    for slug in slugs:
        if not slug or slug in seen:
            continue
        seen.add(slug)
        p = _resolve_path(slug, wiki_root)
        if p is None:
            out.append(WikiIndexEntry(slug=slug, title=slug, kind="entity"))
            continue
        try:
            fm, _ = parse_text(p.read_text(encoding="utf-8"))
            rel = str(p.relative_to(wiki_root)).replace("\\", "/")
            out.append(WikiIndexEntry(
                slug=slug,
                title=str(fm.get("title") or slug),
                kind=str(fm.get("kind") or _path_to_kind(rel)),
            ))
        except Exception:  # noqa: BLE001
            out.append(WikiIndexEntry(slug=slug, title=slug, kind="entity"))
    return out


@router.get("/wiki/books", response_model=BookIndex)
async def get_books_index(user_id: CurrentUserId) -> BookIndex:
    """全局概览:每本书的章节/主题/实体计数 + 总计。前端「Wiki 浏览」初始页用。"""
    wiki_root = settings.wiki_root_path
    payload = build_book_index(wiki_root)
    return BookIndex(**payload)


@router.get("/wiki/books/{book}/tree", response_model=BookTree)
async def get_book_tree(book: str, user_id: CurrentUserId) -> BookTree:
    """单本书的目录树:source(章节) → topic(主题) → entity(实体) 三层 + orphan_entities。"""
    wiki_root = settings.wiki_root_path
    payload = build_book_tree(book, wiki_root)
    return BookTree(**payload)


@router.get("/wiki/books/{book}/graph")
async def get_book_graph(book: str, user_id: CurrentUserId) -> dict:
    """该书范围的知识图谱子集:复用 graph-data.json 在响应层按 book 过滤,不重建。

    若 graph-data.json 不存在,返回空骨架 — 由 ingest / 重建流程兜底。
    """
    wiki_root = settings.wiki_root_path
    from app.services.graph.builder import load_graph_data_json
    full = await load_graph_data_json(wiki_root=wiki_root)
    if full is None:
        return {"nodes": [], "edges": [], "communities": {}, "insights": [], "generated_at": None}
    return filter_graph_by_book(full, book, wiki_root)


@router.get("/wiki/nav-map")
async def get_nav_map(user_id: CurrentUserId, book: str | None = None) -> dict[str, Any]:
    """知识库导航 map(md 大纲),供智能问答检索路由。

    - 不带 `book`:返回全局总览 nav-map.md(各书计数 + 指向各书导航文件)。
    - 带 `book`:实时构建该书的章节→主题→实体三层大纲(带 slug + 简介)。

    全局文件在 ingest / 删除 / 改名后由后台重建;若尚未生成则即时回退构建。
    """
    wiki_root = settings.wiki_root_path
    from app.services.wiki.nav_map import build_nav_map_md

    if book:
        md = await asyncio.to_thread(build_nav_map_md, book, wiki_root)
        return {"book": book, "markdown": md}

    p = Path(wiki_root) / "nav-map.md"
    if p.exists():
        return {"book": None, "markdown": p.read_text(encoding="utf-8")}
    # 全局文件缺失:即时重建一次再读
    from app.services.wiki.nav_map import write_nav_maps

    await write_nav_maps(wiki_root)
    md = p.read_text(encoding="utf-8") if p.exists() else "# 知识库导航总览\n\n_(暂无数据)_\n"
    return {"book": None, "markdown": md}


@router.post("/wiki/nav-map/rebuild")
async def rebuild_nav_map(user_id: AdminUserId) -> dict[str, Any]:
    """手动触发导航 map 全量重建(管理员)。"""
    wiki_root = settings.wiki_root_path
    from app.services.wiki.nav_map import write_nav_maps

    stat = await write_nav_maps(wiki_root)
    return {"ok": True, **stat}


def _sync_after_fs_change(db: Any, wiki_root: Path, rel_paths: list[str]) -> None:
    """编辑 frontmatter 后同步 DB 索引;失败回滚但不阻塞。"""
    try:
        from app.services.ingestion.pipeline import _sync_db_indices

        _sync_db_indices(db, wiki_root=wiki_root, written_paths=rel_paths)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


async def _patch_frontmatter(path: Path, updates: dict[str, Any]) -> None:
    """就地更新某页 frontmatter 的若干标量字段(不动 body、不 bump version)。"""
    fm, body = parse_text(path.read_text(encoding="utf-8"))
    for k, v in updates.items():
        fm[k] = v
    await atomic_write(path, serialize(fm, body))


@router.post("/wiki/books", response_model=CreateBookResponse)
async def create_book(
    payload: CreateBookRequest, user_id: AdminUserId
) -> CreateBookResponse:
    """管理员新建知识库(空书):登记到 books.json,概览页即可见、可点进去放章节。"""
    wiki_root = settings.wiki_root_path
    name = payload.name.strip()
    if not name or name == "未分类":
        raise HTTPException(status_code=400, detail="书名不合法")
    created = add_book(wiki_root, name)
    return CreateBookResponse(ok=True, book=name, created=created)


@router.put("/wiki/books/{book}/chapters/order", response_model=ReorderChaptersResponse)
async def reorder_chapters(
    book: str,
    payload: ReorderChaptersRequest,
    user_id: AdminUserId,
    db: DbSession,
) -> ReorderChaptersResponse:
    """调整某书章节顺序:按传入 slug 顺序把 order=0,1,2... 写到各 source frontmatter。"""
    wiki_root = settings.wiki_root_path
    updated = 0
    rel_paths: list[str] = []
    for idx, slug in enumerate(payload.slugs):
        p = safe_join(wiki_root, "wiki", "sources", f"{slug}.md")
        if not p.exists():
            continue
        await _patch_frontmatter(p, {"order": idx})
        rel_paths.append(f"wiki/sources/{slug}.md")
        updated += 1
    if rel_paths:
        _sync_after_fs_change(db, wiki_root, rel_paths)
        _schedule_graph_rebuild(wiki_root)
    return ReorderChaptersResponse(ok=True, book=book, updated=updated)


@router.put("/wiki/pages/{slug}/rename", response_model=SimpleOkResponse)
async def rename_page(
    slug: str,
    payload: RenamePageRequest,
    user_id: AdminUserId,
    db: DbSession,
    kind: str | None = Query(default=None),
) -> SimpleOkResponse:
    """重命名节点标题(主题/章节/实体均可):只改 frontmatter.title,slug 不变。

    kind 用于消歧同名页(实体与主题可同名):点主题改主题页,点实体改实体页。
    """
    wiki_root = settings.wiki_root_path
    p = _resolve_path(slug, wiki_root, hint_kind=kind)
    if p is None:
        raise HTTPException(status_code=404, detail=f"wiki page {slug!r} not found")
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    await _patch_frontmatter(p, {"title": title})
    rel = str(p.relative_to(wiki_root)).replace("\\", "/")
    _sync_after_fs_change(db, wiki_root, [rel])
    _schedule_graph_rebuild(wiki_root)
    return SimpleOkResponse(ok=True, slug=slug, title=title)


@router.put("/wiki/chapters/{slug}/book", response_model=SimpleOkResponse)
async def move_chapter(
    slug: str,
    payload: MoveChapterRequest,
    user_id: AdminUserId,
    db: DbSession,
) -> SimpleOkResponse:
    """把某章节(source)移动到另一本书:改 source frontmatter.book。

    topic/entity 运行时从 sources[0] 继承 book,故无需逐一改下游页面。
    """
    wiki_root = settings.wiki_root_path
    target = payload.book.strip()
    if not target:
        raise HTTPException(status_code=400, detail="目标书名不能为空")
    p = safe_join(wiki_root, "wiki", "sources", f"{slug}.md")
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"chapter {slug!r} not found")
    await _patch_frontmatter(p, {"book": target})
    rel = f"wiki/sources/{slug}.md"
    _sync_after_fs_change(db, wiki_root, [rel])
    _schedule_graph_rebuild(wiki_root)
    return SimpleOkResponse(ok=True, slug=slug, book=target)


@router.get("/wiki/index", response_model=WikiIndex)
async def get_index(user_id: CurrentUserId, db: DbSession) -> WikiIndex:
    """读取 wiki 全部页面 → 按 kind 分组 + 每项含 title(供前端 chip 展示)。"""
    wiki_root = settings.wiki_root_path
    by_kind: dict[str, list[WikiIndexEntry]] = {k: [] for k in _KIND_DIR}
    last = None
    for page in iter_wiki_pages(wiki_root):
        rel = str(page.relative_to(wiki_root)).replace("\\", "/")
        kind = _path_to_kind(rel)
        slug = page.stem
        # 解 frontmatter 拿 title;失败则用 slug 兜底,不阻塞索引生成
        try:
            fm, _ = parse_text(page.read_text(encoding="utf-8"))
            title = str(fm.get("title") or slug)
        except Exception:  # noqa: BLE001
            title = slug
        by_kind.setdefault(kind, []).append(
            WikiIndexEntry(slug=slug, title=title, kind=kind)
        )
        try:
            mtime = datetime.fromtimestamp(page.stat().st_mtime, tz=timezone.utc)
            if last is None or mtime > last:
                last = mtime
        except OSError:
            continue
    # 每组按 title 排序
    for k in by_kind:
        by_kind[k].sort(key=lambda e: e.title)
    counts = {k: len(v) for k, v in by_kind.items()}
    counts["total"] = sum(counts.values())
    return WikiIndex(by_kind=by_kind, counts=counts, last_updated=last)


@router.get("/wiki/search", response_model=SearchResponse)
async def search_wiki(
    user_id: CurrentUserId,
    db: DbSession,
    q: str = Query(min_length=1, max_length=100),
    kind: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> SearchResponse:
    """百科搜索:按标题 / 别名命中 wiki 页面。

    - 主路径走 wiki_pages 表 title LIKE + wiki_aliases alias LIKE(canonical 关联回页面)
    - 表为空(开发期未同步)时降级到全盘扫描标题
    - 排序:标题精确 > 标题前缀 > 别名命中 > 标题子串;同级按标题长度升序
    - kind 过滤:entity / topic / source(也接受其它正式 kind)
    """
    query = q.strip()
    if not query:
        return SearchResponse(query=q, items=[], total=0)
    q_lower = query.lower()
    allowed_kinds = set(_KIND_DIR.keys())
    kind_filter = kind if kind in allowed_kinds else None

    # rank: 越小越靠前
    def _rank(via: str) -> int:
        return {"title_exact": 0, "title_prefix": 1, "alias": 2, "title_substr": 3}.get(via, 4)

    # (slug) -> SearchResultItem,保留 rank 最小的命中
    hits: dict[str, tuple[int, SearchResultItem]] = {}

    def _consider(slug: str, title: str, page_kind: str, via: str, alias_hit: str | None = None) -> None:
        if kind_filter and page_kind != kind_filter:
            return
        r = _rank(via)
        prev = hits.get(slug)
        if prev is not None and prev[0] <= r:
            return
        hits[slug] = (r, SearchResultItem(
            slug=slug, title=title, kind=page_kind, matched_via=via, alias_hit=alias_hit,
        ))

    def _via_for_title(title: str) -> str:
        t = title.lower()
        if t == q_lower:
            return "title_exact"
        if t.startswith(q_lower):
            return "title_prefix"
        return "title_substr"

    page_rows = (
        db.query(WikiPage)
        .filter(WikiPage.title.ilike(f"%{query}%"))
        .limit(200)
        .all()
    )
    db_has_pages = db.query(WikiPage.id).first() is not None

    if db_has_pages:
        for p in page_rows:
            _consider(p.slug, p.title, p.kind, _via_for_title(p.title))
        # 别名命中:wiki_aliases.alias LIKE → canonical 关联到同名页面
        alias_rows = (
            db.query(WikiAlias)
            .filter(WikiAlias.alias.ilike(f"%{query}%"))
            .limit(200)
            .all()
        )
        canon_set = {a.canonical for a in alias_rows}
        if canon_set:
            canon_pages = (
                db.query(WikiPage).filter(WikiPage.title.in_(canon_set)).all()
            )
            by_title = {cp.title: cp for cp in canon_pages}
            for a in alias_rows:
                cp = by_title.get(a.canonical)
                if cp is not None:
                    _consider(cp.slug, cp.title, cp.kind, "alias", alias_hit=a.alias)
    else:
        # 降级:全盘扫描标题(wiki_pages 未同步时)
        wiki_root = settings.wiki_root_path
        for page in iter_wiki_pages(wiki_root):
            rel = str(page.relative_to(wiki_root)).replace("\\", "/")
            page_kind = _path_to_kind(rel)
            slug = page.stem
            try:
                fm, _ = parse_text(page.read_text(encoding="utf-8"))
                title = str(fm.get("title") or slug)
            except Exception:  # noqa: BLE001
                title = slug
            if q_lower in title.lower() or q_lower in slug.lower():
                _consider(slug, title, page_kind, _via_for_title(title))

    ranked = sorted(hits.values(), key=lambda rc: (rc[0], len(rc[1].title), rc[1].title))
    items = [item for _r, item in ranked][:limit]
    return SearchResponse(query=q, items=items, total=len(items))


@router.get("/wiki/pages/{slug}/relations", response_model=RelationsResponse)
async def get_relations(
    slug: str,
    user_id: CurrentUserId,
    db: DbSession,
    kind: str | None = Query(default=None),
) -> RelationsResponse:
    """百科条目关系聚合,按 kind 分层构图(层层递进):

    - entity:related_entities = 语义相关实体(wiki_links 双向);
      mini_graph = graph-data.json ego 子图,只保留实体↔实体语义边(排除 topic/source)。
    - topic:related_entities = 成员实体(frontmatter.related_entities,edge_kind=containment);
      mini_graph = 中心主题虚线辐射成员实体(不叠加成员间语义边)。
    - source:related_entities = 衍生主题(sources 反查 topic,kind=topic,edge_kind=containment);
      mini_graph = 中心素材虚线辐射衍生主题。

    其余:mentioned_by_topics(实体被哪些主题提及)+ sources(本页直接来源素材)。
    """
    wiki_root = settings.wiki_root_path
    p = _resolve_path(slug, wiki_root, hint_kind=kind)
    if p is None:
        raise HTTPException(status_code=404, detail=f"wiki page {slug!r} not found")
    fm, body = parse_text(p.read_text(encoding="utf-8"))
    rel = str(p.relative_to(wiki_root)).replace("\\", "/")
    self_kind = str(fm.get("kind") or _path_to_kind(rel))
    self_title = str(fm.get("title") or slug)

    # ---- 页面元信息查表(title/kind/book),DB 为主、缺失回退文件 ----
    def _page_meta(s: str) -> tuple[str, str, str | None] | None:
        """返回 (title, kind, book);找不到返回 None。"""
        row = db.query(WikiPage).filter(WikiPage.slug == s).one_or_none()
        if row is not None:
            book = None
            try:
                book = (json.loads(row.frontmatter_json or "{}") or {}).get("book")
            except (json.JSONDecodeError, TypeError):
                book = None
            return row.title, row.kind, (str(book) if book else None)
        fp = _resolve_path(s, wiki_root)
        if fp is None:
            return None
        try:
            ffm, _ = parse_text(fp.read_text(encoding="utf-8"))
            frel = str(fp.relative_to(wiki_root)).replace("\\", "/")
            return (
                str(ffm.get("title") or s),
                str(ffm.get("kind") or _path_to_kind(frel)),
                (str(ffm.get("book")) if ffm.get("book") else None),
            )
        except Exception:  # noqa: BLE001
            return None

    def _fm_of(s: str) -> dict | None:
        """取某 slug 的 frontmatter(DB 优先,回退文件)。"""
        row = db.query(WikiPage).filter(WikiPage.slug == s).one_or_none()
        if row is not None and row.frontmatter_json:
            try:
                return json.loads(row.frontmatter_json) or {}
            except (json.JSONDecodeError, TypeError):
                pass
        fp = _resolve_path(s, wiki_root)
        if fp is None:
            return None
        try:
            ffm, _ = parse_text(fp.read_text(encoding="utf-8"))
            return ffm or {}
        except Exception:  # noqa: BLE001
            return None

    def _meta_by_kind(s: str, prefer_kind: str) -> tuple[str, str, str | None] | None:
        """按指定 kind 优先从磁盘取 (title, kind, book)。

        同名页可能在 entities/ 与 topics/ 各有一份(如「作业测定」既是实体又是主题)。
        `_page_meta` 走 DB 查 slug,unique 约束下撞名时只剩一行,无法区分 kind。
        这里直接按 prefer_kind 命中对应目录的文件,命中不到再退回任意存在的页。
        """
        fp = _resolve_path(s, wiki_root, hint_kind=prefer_kind)
        if fp is None:
            return None
        try:
            ffm, _ = parse_text(fp.read_text(encoding="utf-8"))
            frel = str(fp.relative_to(wiki_root)).replace("\\", "/")
            return (
                str(ffm.get("title") or s),
                str(ffm.get("kind") or _path_to_kind(frel)),
                (str(ffm.get("book")) if ffm.get("book") else None),
            )
        except Exception:  # noqa: BLE001
            return None

    related_entities: list[RelatedEntity] = []
    mini = MiniGraph()
    center_id = f"{self_kind}:{slug}"

    if self_kind == "topic":
        # ---- 主题:成员实体 = 正文「## 关键实体」段的双链(唯一权威定义)。----
        # 不能用 fm.related_entities:LLM 单次生成易把别的主题名(如「方法研究」)塞进来,
        # 导致主题框里把另一个主题当成「包含实体」展示。正文段是目录树/图谱共用的真相。
        # 兜底:正文无该段(老页/极简页)时退回 fm.related_entities,仍走 entity 优先解析。
        members = topic_member_slugs(body)
        if not members:
            members = [str(x) for x in (fm.get("related_entities") or [])]
        mini_nodes = [MiniGraphNode(id=center_id, slug=slug, title=self_title, kind="topic", is_center=True)]
        mini_edges: list[MiniGraphEdge] = []
        for m in members:
            # 成员按「实体」优先解析:同名页若同时是 topic+entity(如「作业测定」),
            # 主题→实体语义下应指向 entity 页,而非把另一个主题当成员。
            meta = _meta_by_kind(m, "entity")
            mkind = meta[1] if meta else "entity"
            mtitle = meta[0] if meta else m
            related_entities.append(RelatedEntity(
                slug=m, title=mtitle, kind=mkind, rel_type="成员",
                direction="out", occurrences=1, exists=meta is not None,
                edge_kind="containment",
            ))
            mid = f"{mkind}:{m}"
            mini_nodes.append(MiniGraphNode(id=mid, slug=m, title=mtitle, kind=mkind, is_center=False))
            mini_edges.append(MiniGraphEdge(
                source=center_id, target=mid, rel_type="包含", weight=0.4, edge_kind="containment",
            ))
        mini = MiniGraph(nodes=mini_nodes, edges=mini_edges)

    elif self_kind == "source":
        # ---- 素材:衍生主题 = topics/*.md 中 frontmatter.sources 含本素材的页。----
        # 直接扫磁盘 topic 文件,不走 DB:wiki_pages.slug 唯一约束下,同名 topic/entity
        # (如「作业测定」)撞键时 topic 行会被 entity 行挤掉,DB 查 kind=='topic' 会漏。
        # 文件系统每个 .md 各自独立,是这层关系的真相。
        topic_slugs: list[str] = []
        tdir = wiki_root / "wiki" / "topics"
        if tdir.is_dir():
            for fp in sorted(tdir.glob("*.md")):
                try:
                    tfm, _ = parse_text(fp.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                if slug in [str(x) for x in (tfm.get("sources") or [])]:
                    topic_slugs.append(str(tfm.get("slug") or fp.stem))
        # 补充:素材正文「## 抽取的主题」段里列出但 frontmatter 尚未回填的主题
        for ts, _alias in extract_section_links(body, "抽取的主题"):
            if ts not in topic_slugs and (tdir / f"{ts}.md").exists():
                topic_slugs.append(ts)
        topic_slugs = sorted(set(topic_slugs))
        mini_nodes = [MiniGraphNode(id=center_id, slug=slug, title=self_title, kind="source", is_center=True)]
        mini_edges = []
        for ts in topic_slugs:
            meta = _meta_by_kind(ts, "topic")
            ttitle = meta[0] if meta else ts
            related_entities.append(RelatedEntity(
                slug=ts, title=ttitle, kind="topic", rel_type="衍生主题",
                direction="out", occurrences=1, exists=meta is not None,
                edge_kind="containment",
            ))
            tid = f"topic:{ts}"
            mini_nodes.append(MiniGraphNode(id=tid, slug=ts, title=ttitle, kind="topic", is_center=False))
            mini_edges.append(MiniGraphEdge(
                source=center_id, target=tid, rel_type="生成", weight=0.4, edge_kind="containment",
            ))
        mini = MiniGraph(nodes=mini_nodes, edges=mini_edges)

    else:
        # ---- 实体(及其它):wiki_links 双向语义相关实体 ----
        out_rows = db.query(WikiLink).filter(WikiLink.src_slug == slug).all()
        in_rows = db.query(WikiLink).filter(WikiLink.dst_slug == slug).all()
        rel_map: dict[str, RelatedEntity] = {}

        def _add_rel(other: str, rel_type: str, direction: str, occ: int) -> None:
            meta = _page_meta(other)
            kind = meta[1] if meta else "entity"
            if kind != "entity":
                return
            title = meta[0] if meta else other
            existing = rel_map.get(other)
            if existing is None:
                rel_map[other] = RelatedEntity(
                    slug=other, title=title, kind="entity", rel_type=rel_type or "related",
                    direction=direction, occurrences=int(occ or 1), exists=meta is not None,
                    edge_kind="semantic",
                )
            else:
                existing.occurrences += int(occ or 1)
                if existing.direction != direction:
                    existing.direction = "both"

        for r in out_rows:
            _add_rel(r.dst_slug, r.link_type or "related", "out", int(r.occurrences or 1))
        for r in in_rows:
            _add_rel(r.src_slug, r.link_type or "related", "in", int(r.occurrences or 1))

        related_entities = sorted(rel_map.values(), key=lambda e: (-e.occurrences, e.title))

        # mini_graph:与 related_entities 同一批节点(wiki_links 双向),不读 graph-data.json
        mini_nodes = [MiniGraphNode(id=center_id, slug=slug, title=self_title, kind="entity", is_center=True)]
        mini_edges: list[MiniGraphEdge] = []
        for r in related_entities:
            nid = f"entity:{r.slug}"
            mini_nodes.append(MiniGraphNode(
                id=nid, slug=r.slug, title=r.title, kind="entity", is_center=False,
            ))
            occ = max(int(r.occurrences or 1), 1)
            mini_edges.append(MiniGraphEdge(
                source=center_id, target=nid,
                rel_type=r.rel_type or "related",
                weight=min(1.0, 0.3 + 0.05 * occ),
                edge_kind="semantic",
            ))
        mini = MiniGraph(nodes=mini_nodes, edges=mini_edges)

    # ---- mentioned_by_topics:反链里 kind=topic 的页面 + 每个 topic 的 sources ----
    backlink_rows = db.query(WikiLink).filter(WikiLink.dst_slug == slug).all()
    backlink_srcs = sorted({r.src_slug for r in backlink_rows})
    if not backlink_srcs:
        backlink_srcs = find_backlinks(slug, wiki_root)
    mentioned: list[MentioningTopic] = []
    for src in backlink_srcs:
        meta = _page_meta(src)
        if meta is None or meta[1] != "topic":
            continue
        tfm = _fm_of(src) or {}
        topic_sources: list[SourceRef] = []
        for s in tfm.get("sources") or []:
            smeta = _page_meta(str(s))
            topic_sources.append(SourceRef(
                slug=str(s),
                title=smeta[0] if smeta else str(s),
                book=smeta[2] if smeta else None,
                exists=smeta is not None,
            ))
        mentioned.append(MentioningTopic(slug=src, title=meta[0], sources=topic_sources))
    mentioned.sort(key=lambda t: t.title)

    # ---- sources:本页 frontmatter.sources ----
    self_sources: list[SourceRef] = []
    for s in fm.get("sources") or []:
        smeta = _page_meta(str(s))
        self_sources.append(SourceRef(
            slug=str(s),
            title=smeta[0] if smeta else str(s),
            book=smeta[2] if smeta else None,
            exists=smeta is not None,
        ))

    return RelationsResponse(
        slug=slug,
        title=self_title,
        kind=self_kind,
        related_entities=related_entities,
        mentioned_by_topics=mentioned,
        sources=self_sources,
        mini_graph=mini,
    )


@router.get("/wiki/pages/{slug}", response_model=WikiPageDetail)
async def get_page(
    slug: str,
    user_id: CurrentUserId,
    db: DbSession,
    response: Response,
    follow_redirect: bool = Query(default=True),
    kind: str | None = Query(default=None),
) -> WikiPageDetail:
    """读单个 wiki 节点:md 内容 + frontmatter + 反链 + 引用。

    kind 用于消歧同名页(实体与主题可同名,如「程序分析」):
    带 ?kind=topic 时优先打开主题页,?kind=entity 时优先打开实体页。

    404 时尝试查 slug_redirects:
    - 命中 + new_slug != None + follow_redirect=True → 跟随到目标页
    - 命中 + new_slug == None → 410 Gone
    - 未命中 → 404
    `X-KGQA-Redirected-From` 响应头标识跟随过的旧 slug。
    """
    wiki_root = settings.wiki_root_path
    p = _resolve_path(slug, wiki_root, hint_kind=kind)
    if p is None:
        # 走 redirect 链
        final_slug, _hops = resolve_chain(db, slug=slug)
        if final_slug is None:
            # 终点是删除
            raise HTTPException(
                status_code=410,
                detail={
                    "code": "GONE",
                    "message": f"wiki page {slug!r} was deleted",
                    "old_slug": slug,
                },
            )
        if final_slug != slug and follow_redirect:
            p = _resolve_path(final_slug, wiki_root, hint_kind=kind)
            if p is not None:
                response.headers["X-KGQA-Redirected-From"] = slug
                slug = final_slug
        if p is None:
            raise HTTPException(status_code=404, detail=f"wiki page {slug!r} not found")

    fm, body = parse_text(p.read_text(encoding="utf-8"))
    rel = str(p.relative_to(wiki_root)).replace("\\", "/")
    outlinks_slugs = [t for t, _alias in extract_links(body)]
    backlinks_slugs = find_backlinks(slug, wiki_root)
    return WikiPageDetail(
        slug=slug,
        title=str(fm.get("title") or slug),
        kind=str(fm.get("kind") or _path_to_kind(rel)),
        file_path=rel,
        frontmatter=fm,
        body=body,
        content=body,  # 前端 MarkdownViewer 读 page.content
        backlinks=_enrich_links(backlinks_slugs, wiki_root),
        outlinks=_enrich_links(outlinks_slugs, wiki_root),
        version=int(fm.get("version") or 1),
    )


@router.get("/wiki/pages/{slug}/backlinks", response_model=BacklinksResponse)
async def get_backlinks(
    slug: str, user_id: CurrentUserId, db: DbSession
) -> BacklinksResponse:
    """从 wiki_links 表查反链(按 link_type 聚合,带 src 页面 title/kind)。

    比 link_resolver.find_backlinks 走全文扫描快得多;表为空时降级到全扫。
    """
    rows = (
        db.query(WikiLink)
        .filter(WikiLink.dst_slug == slug)
        .order_by(WikiLink.occurrences.desc())
        .all()
    )
    if not rows:
        # 降级:全文扫描(开发期 wiki_links 可能未同步)
        wiki_root = settings.wiki_root_path
        srcs = find_backlinks(slug, wiki_root)
        items: list[BacklinkItem] = []
        for src in srcs:
            page = db.query(WikiPage).filter(WikiPage.slug == src).one_or_none()
            items.append(
                BacklinkItem(
                    src_slug=src,
                    src_title=page.title if page else src,
                    src_kind=page.kind if page else "entity",
                    link_type="related",
                    occurrences=1,
                )
            )
        return BacklinksResponse(slug=slug, backlinks=items, total=len(items))

    src_slugs = sorted({r.src_slug for r in rows})
    pages = {
        p.slug: p
        for p in db.query(WikiPage).filter(WikiPage.slug.in_(src_slugs)).all()
    }
    items_db: list[BacklinkItem] = []
    for r in rows:
        page = pages.get(r.src_slug)
        items_db.append(
            BacklinkItem(
                src_slug=r.src_slug,
                src_title=(page.title if page else r.src_slug),
                src_kind=(page.kind if page else "entity"),
                link_type=r.link_type or "related",
                occurrences=int(r.occurrences or 1),
            )
        )
    return BacklinksResponse(slug=slug, backlinks=items_db, total=len(items_db))


@router.get("/wiki/pages/{slug}/versions", response_model=NodeVersionList)
async def get_versions(
    slug: str, user_id: CurrentUserId, db: DbSession
) -> NodeVersionList:
    """列出某 slug 的版本历史(按 version desc)。"""
    rows = list_versions(db, slug=slug, limit=100)
    from app.services.wiki.version_history import diff_stat

    items = [
        NodeVersionListItem(
            version=int(r.version),
            triggered_by=str(r.triggered_by),
            actor_user_id=r.actor_user_id,
            title_snapshot=r.title_snapshot,
            document_id=r.document_id,
            created_at=r.created_at,
            diff_stat=diff_stat(r.diff_json),
        )
        for r in rows
    ]
    return NodeVersionList(slug=slug, total=len(items), versions=items)


@router.get("/wiki/pages/{slug}/versions/{version}", response_model=NodeVersionDetail)
async def get_version_detail(
    slug: str, version: int, user_id: CurrentUserId, db: DbSession
) -> NodeVersionDetail:
    """读单个版本的完整 diff(unified text + frontmatter changes)。"""
    from app.services.wiki.version_history import get_version

    row = get_version(db, slug=slug, version=version)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"version {version} for {slug!r} not found"
        )
    try:
        diff = json.loads(row.diff_json)
    except (json.JSONDecodeError, TypeError):
        diff = {}
    return NodeVersionDetail(
        slug=slug,
        version=int(row.version),
        triggered_by=str(row.triggered_by),
        actor_user_id=row.actor_user_id,
        title_snapshot=row.title_snapshot,
        document_id=row.document_id,
        created_at=row.created_at,
        diff=diff,
    )


@router.put("/wiki/pages/{slug}", response_model=WikiPageDetail)
async def update_page(
    slug: str,
    payload: WikiPageUpdate,
    user_id: AdminUserId,
    db: DbSession,
) -> WikiPageDetail:
    """管理员手动编辑节点:写文件 + 同步索引 + (bump_version 时)写版本历史。"""
    wiki_root = settings.wiki_root_path
    p = _resolve_path(slug, wiki_root)
    if p is None:
        raise HTTPException(status_code=404, detail=f"wiki page {slug!r} not found")
    old_text = p.read_text(encoding="utf-8")
    existing_fm, old_body = parse_text(old_text)
    new_fm = merge_frontmatter(
        existing_fm,
        payload.frontmatter or {},
        bump_version=payload.bump_version,
    )
    new_body = payload.body
    text = serialize(new_fm, new_body)
    await atomic_write(p, text)

    # 同步 wiki_pages / wiki_links 索引 + 写版本(bump 时)
    try:
        from app.services.ingestion.pipeline import _sync_db_indices

        rel = str(p.relative_to(wiki_root)).replace("\\", "/")
        _sync_db_indices(db, wiki_root=wiki_root, written_paths=[rel])

        if payload.bump_version:
            diff = compute_diff(
                old_fm=existing_fm,
                old_body=old_body,
                new_fm=new_fm,
                new_body=new_body,
            )
            write_version(
                db,
                slug=slug,
                version=int(new_fm.get("version") or 1),
                diff=diff,
                triggered_by="manual",
                title_snapshot=str(new_fm.get("title") or slug),
                actor_user_id=user_id if user_id else None,
            )

        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()

    # fire-and-forget 重建 graph-data.json:wiki 编辑后图谱节点/边/标题立刻同步
    _schedule_graph_rebuild(wiki_root)

    fm, body = parse_text(p.read_text(encoding="utf-8"))
    rel = str(p.relative_to(wiki_root)).replace("\\", "/")
    return WikiPageDetail(
        slug=slug,
        title=str(fm.get("title") or slug),
        kind=str(fm.get("kind") or _path_to_kind(rel)),
        file_path=rel,
        frontmatter=fm,
        body=body,
        content=body,
        backlinks=_enrich_links(find_backlinks(slug, wiki_root), wiki_root),
        outlinks=_enrich_links([t for t, _alias in extract_links(body)], wiki_root),
        version=int(fm.get("version") or 1),
    )


@router.delete("/sources/{slug}", response_model=DeleteSourceResponse)
async def delete_source(
    slug: str, user_id: AdminUserId, db: DbSession
) -> DeleteSourceResponse:
    """删除素材:级联清理 + slug-redirect + 缓存失效 + 断链检查。

    P3 真级联版:
      1. 删 wiki/sources/{slug}.md
      2. 从所有引用此 source 的页面移除"### 来源:[[X]]"段 + frontmatter.sources
      3. 清理后变孤立的实体/主题页整页删 + 写 slug_redirects(reason=source_deleted)
      4. 缓存失效 + DB 同步(wiki_pages / wiki_links 出入边)
      5. 计算并返回断链
    """
    wiki_root = settings.wiki_root_path
    try:
        result = await delete_source_service(
            source_slug=slug,
            wiki_root=wiki_root,
            db=db,
            cascade=True,
            delete_orphans=True,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    db.commit()
    await _run_full_realign_after_delete(wiki_root)
    return DeleteSourceResponse(ok=True, source_slug=slug, **result)


@router.delete("/wiki/pages/{slug}", response_model=DeletePageResponse)
async def delete_page(
    slug: str, user_id: AdminUserId, db: DbSession
) -> DeletePageResponse:
    """删除单个 wiki 页面(管理员)。

    - 若是 source kind → 自动走级联清理(等价 DELETE /sources/{slug})
    - 其它 kind(entity/topic/synthesis/comparison/query)→ 仅删本页 +
      slug_redirect(reason=manual, new_slug=None) + 索引清理 + 计算断链
    """
    wiki_root = settings.wiki_root_path

    # 先看一眼是不是 source — 若是,走级联
    src_path = safe_join(wiki_root, "wiki", "sources", f"{slug}.md")
    if src_path.exists():
        try:
            cascade = await delete_source_service(
                source_slug=slug,
                wiki_root=wiki_root,
                db=db,
                cascade=True,
                delete_orphans=True,
            )
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        db.commit()
        await _run_full_realign_after_delete(wiki_root)
        return DeletePageResponse(
            ok=True,
            slug=slug,
            kind="source",
            cascaded=True,
            removed_path=f"wiki/sources/{slug}.md",
            removed_pages=cascade.get("removed_pages", []),
            updated_pages=cascade.get("updated_pages", []),
            broken_links=cascade.get("broken_links", []),
            redirects_written=cascade.get("redirects_written", 0),
        )

    try:
        result = await delete_page_service(
            slug=slug,
            wiki_root=wiki_root,
            db=db,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    db.commit()
    await _run_full_realign_after_delete(wiki_root)
    return DeletePageResponse(ok=True, slug=slug, **result)


def _schedule_graph_rebuild(wiki_root: Path) -> None:
    """fire-and-forget 重建 graph-data.json,使前端图谱页立刻反映删除。

    失败不阻塞主响应 — 下一次 ingest 也会重建。
    """
    try:
        from app.services.graph.builder import (
            build_graph_data,
            write_graph_data_json,
        )

        async def _run() -> None:
            try:
                payload = await build_graph_data(wiki_root=wiki_root)
                await write_graph_data_json(wiki_root=wiki_root, payload=payload)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"graph rebuild after delete failed (non-blocking): {e}")
            try:
                from app.services.wiki.nav_map import write_nav_maps

                await write_nav_maps(wiki_root)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"nav-map rebuild after fs change failed (non-blocking): {e}")
            try:
                from app.services.query.vector_index import sync_query_vector_index

                await sync_query_vector_index(wiki_root=wiki_root)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"query vector index sync after wiki edit failed (non-blocking): {e}")

        asyncio.create_task(_run())
    except RuntimeError:
        # 测试同步上下文里没 running loop,忽略
        pass


async def _run_full_realign_after_delete(wiki_root: Path) -> None:
    """阻塞式:删除后跑与 ingest 末段同源的「全量对齐套件」,跑完才返回。

    删除一个实体/主题/素材会留下三类残渣:断链(指向已删页的 [[X]])、
    孤儿(失去 source / topic 归属的实体)、图谱与 frontmatter 的陈旧统计。
    轻量的 _schedule_graph_rebuild 只重建 graph-data.json + nav-map,管不到这些。

    这里复用 pipeline._post_post_process(与 ingest 完成时同一套):
      alias 归一 → ensure_zero_void(fill_void=False:只 strip 不回填,避免复活被删页)
      → classify 全量/逐章孤儿归类 → graph 重建 + Neo4j 同步 → nav-map → frontmatter 回写。
    fill_void=False 是删除场景的关键:否则刚删的实体会被「缺页回填」LLM 重新建出来。

    设计:前端删除请求会 await 本函数(几秒),对齐跑完才回成功 —— 这样前端拿到
    200 时目录树/图谱已是干净对齐态,不会再看到已删节点的残留按钮。
    套件内部每步都是 best-effort(失败仅告警),整体失败也不抛(删除本身已 commit)。
    """
    try:
        from app.services.ingestion.pipeline import _post_post_process

        await _post_post_process(
            wiki_root=wiki_root, written_paths=[], fill_void=False
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"full realign after delete failed (non-blocking): {e}")
