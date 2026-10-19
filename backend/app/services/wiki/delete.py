"""wiki/delete.py:删除素材级联清理 + 孤立页处置 + slug-redirect 记录。

对应规划文档 §F6:删除一份 source 时,需要:
  1. 删 wiki/sources/{slug}.md
  2. 从所有引用此 source 的页面 frontmatter.sources 中移除该 slug
  3. 删除该页正文中"## 不同素材中的观点 / ### 来源:[[slug]]"子段
  4. 若实体页/主题页变孤立(sources 为空 OR 仅靠此 source 创建)→ 整页删除 + 写 slug_redirects
  5. 失效 .wiki-cache.json 中所有指向此 source_slug 的条目
  6. 同步 wiki_pages / wiki_links 表
  7. 写一条 slug_redirects(old=source_slug, new_slug=None, reason='source_deleted')
  8. 调用方负责 commit

返回 DeleteResult dict:
{
  "removed_pages":     ["source/2026-...", "entities/孤立"],   # 整页删
  "updated_pages":     ["entities/工效学", "topics/精益生产"],   # 仅清理段
  "broken_links":      [{src_slug, dst_slug}],                 # 删除后产生的断链
  "cache_evicted":     N,
  "redirects_written": N,
}
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from app.models.wiki_link import WikiLink
from app.models.wiki_page import WikiPage
from app.services.wiki.cache import evict_by_source_slug
from app.services.wiki.frontmatter_io import parse_text, serialize
from app.services.wiki.fs_io import safe_join
from app.services.wiki.link_resolver import (
    aggregate_edges,
    extract_links_with_meta,
    iter_wiki_pages,
)
from app.services.wiki.slug_redirect import write_redirect


_KIND_BY_DIR = {
    "entities": "entity",
    "topics": "topic",
    "sources": "source",
    "queries": "query",
    "synthesis": "synthesis",
    "comparisons": "comparison",
}


def _kind_from_path(rel: str) -> str:
    parts = rel.split("/")
    if len(parts) >= 2 and parts[0] == "wiki":
        return _KIND_BY_DIR.get(parts[1], parts[1])
    return "unknown"


# 匹配 "### 来源:[[X]]" 子段;吃到下一个 "### " 或 "## " 之前
def _strip_source_subsection(body: str, source_slug: str) -> tuple[str, bool]:
    """从正文中删除指向某 source 的 H3 子段。

    匹配:`### 来源:[[source_slug]]` 或 `### 来源: [[source_slug]]` 或 `### 来源 [[source_slug]]`,
    全角冒号也支持。

    Returns:
        (new_body, removed_bool)
    """
    # H3 子段头(几种写法都尝试)
    head_pattern = re.compile(
        r"^###\s*来源\s*[::]?\s*\[\[" + re.escape(source_slug) + r"(?:\|[^\]]*?)?\]\]\s*$",
        re.MULTILINE,
    )
    m = head_pattern.search(body)
    if m is None:
        return body, False
    start = m.start()
    after = body[m.end():]
    # 找下一个 "### " 或 "## "
    next_h3 = after.find("\n### ")
    next_h2 = after.find("\n## ")
    candidates = [n for n in (next_h3, next_h2) if n != -1]
    rel_end = min(candidates) if candidates else -1
    end = m.end() + rel_end if rel_end != -1 else len(body)
    cleaned = body[:start].rstrip() + ("\n\n" + body[end:].lstrip() if end < len(body) else "\n")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, True


def _strip_empty_observations_section(body: str) -> str:
    """若清理子段后,"## 不同素材中的观点" H2 段下没有任何 H3 → 删整 H2 段。"""
    h2 = "## 不同素材中的观点"
    if h2 not in body:
        return body
    h2_start = body.find(h2)
    next_h2 = body.find("\n## ", h2_start + len(h2))
    end = next_h2 if next_h2 != -1 else len(body)
    section = body[h2_start:end]
    # 该 H2 段内必须还有 "### " 才算非空
    body_inside = section[len(h2):].strip()
    has_h3 = re.search(r"^###\s+", body_inside, re.MULTILINE) is not None
    if has_h3:
        return body
    head = body[:h2_start].rstrip()
    tail = body[end:]
    return (head + ("\n\n" + tail.lstrip() if tail else "\n")).rstrip() + "\n"


def _orphan_after_removal(fm: dict[str, Any]) -> bool:
    """frontmatter.sources 移除目标 slug 后是否变孤立(空)。"""
    sources = fm.get("sources") or []
    return len(sources) == 0


async def delete_source(
    *,
    source_slug: str,
    wiki_root: Path,
    db: Session,
    cascade: bool = True,
    delete_orphans: bool = True,
) -> dict:
    """删除一份素材并级联清理。

    Args:
        source_slug: wiki/sources/{slug} 的 slug
        wiki_root: 根目录
        db: SA session(本函数不 commit,留给上层)
        cascade: 是否级联清理引用此 source 的实体/主题页
        delete_orphans: 清理后变孤立的实体/主题页是否整页删

    Returns:
        DeleteResult dict
    """
    wiki_root = Path(wiki_root)
    src_path = safe_join(wiki_root, "wiki", "sources", f"{source_slug}.md")
    src_exists = src_path.exists()

    removed_pages: list[str] = []
    updated_pages: list[str] = []

    # 1. 删 source 文件本身;文件已不存在(半删坏掉的"虚空 source")时不报错,
    #    仍继续清理引用它的页面 + DB 残留 + 缓存 + redirect,确保能彻底删干净。
    if src_exists:
        src_path.unlink()
        removed_pages.append(f"wiki/sources/{source_slug}.md")
    else:
        logger.warning(
            f"delete_source: source file missing, continue cascade cleanup: {source_slug}"
        )

    # 2-4. 级联清理 + 孤立页处置
    if cascade:
        for page in iter_wiki_pages(wiki_root):
            rel = str(page.relative_to(wiki_root)).replace("\\", "/")
            if rel.startswith("wiki/sources/"):
                continue  # source 之间不互引
            try:
                fm, body = parse_text(page.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"delete_source: parse fail {rel}: {e}")
                continue

            sources = list(fm.get("sources") or [])
            had_source_in_fm = source_slug in sources
            if had_source_in_fm:
                sources = [s for s in sources if s != source_slug]
                fm["sources"] = sources

            new_body, removed_subsec = _strip_source_subsection(body, source_slug)
            if removed_subsec:
                new_body = _strip_empty_observations_section(new_body)

            if not had_source_in_fm and not removed_subsec:
                continue

            # 清理后是否孤立?
            slug = page.stem
            kind = _kind_from_path(rel)
            should_delete_page = delete_orphans and _orphan_after_removal(fm) and kind in ("entity", "topic")

            if should_delete_page:
                page.unlink()
                removed_pages.append(rel)
                # 记 redirect:孤立页永久删除
                write_redirect(
                    db,
                    old_slug=slug,
                    new_slug=None,
                    kind=kind,
                    reason="source_deleted",
                    note=f"orphaned after deleting source {source_slug}",
                )
                continue

            # 否则写回(用 serialize,version 不强制 bump)
            new_text = serialize(fm, new_body)
            page.write_text(new_text, encoding="utf-8")
            updated_pages.append(rel)

    # 5. 缓存失效
    cache_evicted = evict_by_source_slug(wiki_root, source_slug)

    # 6. 同步 DB:删 source 页 + 删孤立页 + 重抽 updated 页的 wiki_links
    redirects_written = 0
    for rel in removed_pages:
        slug = Path(rel).stem
        # 删 wiki_pages 行(无论 entity/topic/source)
        n = db.query(WikiPage).filter(WikiPage.slug == slug).delete()
        # 删该 slug 出边
        db.query(WikiLink).filter(WikiLink.src_slug == slug).delete()
        # 删指向该 slug 的入边(因为页面已没了)
        db.query(WikiLink).filter(WikiLink.dst_slug == slug).delete()

    # source 自身的 DB 残留兜底清理:文件缺失时 source 不在 removed_pages,
    # 上面循环不会碰到它,这里按 source_slug 再删一次 wiki_pages 行 + 出入边,
    # 确保"虚空 source"的索引残留被彻底清掉。
    db.query(WikiPage).filter(WikiPage.slug == source_slug).delete()
    db.query(WikiLink).filter(WikiLink.src_slug == source_slug).delete()
    db.query(WikiLink).filter(WikiLink.dst_slug == source_slug).delete()

    # source 自身写一条 redirect(reason=source_deleted, new_slug=None)
    write_redirect(
        db,
        old_slug=source_slug,
        new_slug=None,
        kind="source",
        reason="source_deleted",
    )
    redirects_written += 1
    redirects_written += sum(1 for r in removed_pages if not r.startswith("wiki/sources/"))

    # 7. 重抽 updated_pages 的 wiki_links(出边)
    for rel in updated_pages:
        abs_p = safe_join(wiki_root, *rel.split("/"))
        if not abs_p.exists():
            continue
        try:
            _, body = parse_text(abs_p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        slug = abs_p.stem
        # 同步 wiki_pages 的 frontmatter_json
        fm, _ = parse_text(abs_p.read_text(encoding="utf-8"))
        page_row = db.query(WikiPage).filter(WikiPage.slug == slug).one_or_none()
        if page_row is not None:
            page_row.frontmatter_json = json.dumps(fm, ensure_ascii=False, default=str)
            page_row.body_excerpt = body.strip()[:1000]

        db.query(WikiLink).filter(WikiLink.src_slug == slug).delete(synchronize_session=False)
        edges_meta = extract_links_with_meta(body)
        agg = aggregate_edges(
            [
                {
                    "src_slug": slug,
                    "dst_slug": e["target"],
                    "link_type": e["rel_type"],
                    "occurrences": e["occurrences"],
                    "confidence": e["confidence"],
                }
                for e in edges_meta
            ]
        )
        # 二次去重(case-insensitive):防止 'A' 'a' 在 utf8mb4_unicode_ci 下撞 UNIQUE
        case_dedup: dict[tuple[str, str, str], dict] = {}
        for edge in agg:
            ci_key = (
                (edge["src_slug"] or "").lower(),
                (edge["dst_slug"] or "").lower(),
                (edge["link_type"] or "related").lower(),
            )
            if ci_key in case_dedup:
                case_dedup[ci_key]["occurrences"] += int(edge["occurrences"] or 1)
            else:
                case_dedup[ci_key] = dict(edge)
        for edge in case_dedup.values():
            db.add(
                WikiLink(
                    src_slug=edge["src_slug"],
                    dst_slug=edge["dst_slug"],
                    link_type=edge["link_type"] or "related",
                    occurrences=edge["occurrences"] or 1,
                    confidence=edge["confidence"] or "EXTRACTED",
                )
            )

    # 8. 计算断链:出边 dst 不在现存页面 slug 集合
    existing_slugs = {p.stem for p in iter_wiki_pages(wiki_root)}
    broken_links: list[dict] = []
    for link in db.query(WikiLink).all():
        if link.dst_slug not in existing_slugs:
            broken_links.append({"src_slug": link.src_slug, "dst_slug": link.dst_slug})

    db.flush()
    return {
        "removed_pages": removed_pages,
        "updated_pages": updated_pages,
        "broken_links": broken_links,
        "cache_evicted": cache_evicted,
        "redirects_written": redirects_written,
    }


# ---------------------------------------------------------------------------
# 单页删除(非 source 类型;source 走 delete_source 走级联)
# ---------------------------------------------------------------------------


def _resolve_page_path(
    wiki_root: Path, slug: str, hint_kind: str | None = None
) -> tuple[Path, str] | None:
    """按 kind 优先匹配,否则跨 kind 尝试。返回 (abs_path, kind)。"""
    kind_dirs = {v: k for k, v in _KIND_BY_DIR.items()}  # entity → entities
    if hint_kind and hint_kind in kind_dirs:
        sub = kind_dirs[hint_kind]
        p = safe_join(wiki_root, "wiki", sub, f"{slug}.md")
        if p.exists():
            return p, hint_kind
    for sub, kind in _KIND_BY_DIR.items():
        p = safe_join(wiki_root, "wiki", sub, f"{slug}.md")
        if p.exists():
            return p, kind
    return None


async def delete_page(
    *,
    slug: str,
    wiki_root: Path,
    db: Session,
    hint_kind: str | None = None,
) -> dict:
    """删除单个 wiki 页面(非 source);写 slug_redirect + 清理索引。

    若是 source kind,调用方应改用 delete_source 走级联清理。
    返回 {removed_path, kind, broken_links, redirects_written}。
    """
    wiki_root = Path(wiki_root)
    resolved = _resolve_page_path(wiki_root, slug, hint_kind)
    if resolved is None:
        raise FileNotFoundError(f"wiki page not found: {slug}")
    abs_path, kind = resolved

    if kind == "source":
        raise ValueError("use delete_source for source kind (cascading cleanup)")

    rel = str(abs_path.relative_to(wiki_root)).replace("\\", "/")
    abs_path.unlink()

    # DB 索引清理
    db.query(WikiPage).filter(WikiPage.slug == slug).delete()
    db.query(WikiLink).filter(WikiLink.src_slug == slug).delete()
    db.query(WikiLink).filter(WikiLink.dst_slug == slug).delete()

    # slug 重定向(访问时显示 410 Gone)
    write_redirect(
        db,
        old_slug=slug,
        new_slug=None,
        kind=kind,
        reason="manual",
        note=f"manually deleted via wiki page delete API",
    )

    # 计算因此次删除新增的断链(其它页面引用 → 该 slug)
    existing_slugs = {p.stem for p in iter_wiki_pages(wiki_root)}
    broken_links: list[dict] = []
    for link in db.query(WikiLink).all():
        if link.dst_slug not in existing_slugs:
            broken_links.append({"src_slug": link.src_slug, "dst_slug": link.dst_slug})

    db.flush()
    return {
        "removed_path": rel,
        "kind": kind,
        "broken_links": broken_links,
        "redirects_written": 1,
    }
