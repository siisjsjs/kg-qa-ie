"""wiki/slug_redirect.py:slug 重定向表读写。

旧 slug → 新 slug 的映射(或 new_slug=None 表示永久删除)。

用途:
- 重命名节点 → write_redirect(old, new, kind, reason='rename')
- 删除某 source 后,实体页变孤立整页删 → write_redirect(old, None, kind, reason='source_deleted')
- 合并节点 → write_redirect(old, target, kind, reason='merge')

API 层在 GET /wiki/pages/{slug} 404 时查 redirect:
- 命中且 new_slug != None → 301 + Location 头
- 命中且 new_slug == None → 410 Gone
- 未命中 → 404
"""

from __future__ import annotations

from typing import Iterable, Literal

from sqlalchemy.orm import Session

from app.models.slug_redirect import SlugRedirect

RedirectReason = Literal["rename", "source_deleted", "merge", "manual"]


def write_redirect(
    db: Session,
    *,
    old_slug: str,
    new_slug: str | None,
    kind: str,
    reason: RedirectReason = "rename",
    note: str | None = None,
) -> SlugRedirect:
    """写一条重定向。同 (old_slug, kind) 已存在时覆盖 new_slug/reason/note。"""
    existing = (
        db.query(SlugRedirect)
        .filter(SlugRedirect.old_slug == old_slug, SlugRedirect.kind == kind)
        .one_or_none()
    )
    if existing is not None:
        existing.new_slug = new_slug
        existing.reason = reason
        existing.note = note
        return existing
    row = SlugRedirect(
        old_slug=old_slug,
        new_slug=new_slug,
        kind=kind,
        reason=reason,
        note=note,
    )
    db.add(row)
    db.flush()
    return row


def lookup(
    db: Session, *, old_slug: str, kind: str | None = None
) -> SlugRedirect | None:
    """查一条 redirect。kind 为空时跨 kind 取最新一条。"""
    q = db.query(SlugRedirect).filter(SlugRedirect.old_slug == old_slug)
    if kind:
        q = q.filter(SlugRedirect.kind == kind)
    return q.order_by(SlugRedirect.created_at.desc()).first()


def resolve_chain(
    db: Session, *, slug: str, kind: str | None = None, max_hops: int = 5
) -> tuple[str | None, list[str]]:
    """跟随多跳重定向直到稳定 / 删除 / 超限。

    返回 (final_slug | None, hops_path)。
    - final_slug = None 表示链终点是删除(reason=source_deleted, new_slug=None)
    - hops_path 不含起点 slug,只列经过的节点

    防环:已访问的 slug 再出现 → 终止链
    """
    path: list[str] = []
    cur = slug
    visited: set[str] = {cur}
    for _ in range(max_hops):
        row = lookup(db, old_slug=cur, kind=kind)
        if row is None:
            return cur, path
        if row.new_slug is None:
            return None, path
        if row.new_slug in visited:
            return cur, path
        path.append(row.new_slug)
        visited.add(row.new_slug)
        cur = row.new_slug
    return cur, path


def list_redirects(
    db: Session, *, kind: str | None = None, limit: int = 100
) -> list[SlugRedirect]:
    q = db.query(SlugRedirect)
    if kind:
        q = q.filter(SlugRedirect.kind == kind)
    return q.order_by(SlugRedirect.created_at.desc()).limit(limit).all()


def remove_redirect(db: Session, *, old_slug: str, kind: str) -> bool:
    """删一条 redirect;返回是否实际删除。"""
    row = (
        db.query(SlugRedirect)
        .filter(SlugRedirect.old_slug == old_slug, SlugRedirect.kind == kind)
        .one_or_none()
    )
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def write_many(
    db: Session,
    rows: Iterable[dict],
) -> int:
    """批量写;每个 dict 必含 old_slug/kind,可选 new_slug/reason/note。"""
    n = 0
    for r in rows:
        write_redirect(
            db,
            old_slug=r["old_slug"],
            new_slug=r.get("new_slug"),
            kind=r["kind"],
            reason=r.get("reason", "rename"),
            note=r.get("note"),
        )
        n += 1
    return n
