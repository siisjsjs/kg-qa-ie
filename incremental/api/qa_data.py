"""qa_data:管理员专属的问答数据汇总(query / synthesis / comparison)。

设计:
  - 直接扫 wiki/queries / wiki/comparisons / wiki/synthesis,读 frontmatter 字段
  - synthesis 缺 user_question / asked_by → 用 title 兜底,asked_by=None(展示为系统综合)
  - 排序:created desc,缺失放最后
  - 过滤:?kind=query|synthesis|comparison
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from app.core.config import settings
from app.core.deps import AdminUserId, DbSession
from app.models.user import User
from app.schemas.wiki import QaDataItem, QaDataList
from app.services.wiki.frontmatter_io import parse_text

router = APIRouter()


_QA_DIRS = {
    "query": "queries",
    "synthesis": "synthesis",
    "comparison": "comparisons",
}


def _coerce_dt(v) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _coerce_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _coerce_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _coerce_bool(v) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"true", "1", "yes", "y"}:
        return True
    if s in {"false", "0", "no", "n"}:
        return False
    return None


def _load_user_map(db) -> dict[int, str]:
    """users 表 id → 显示名映射(优先 display_name,回退 username)。DB 异常时降级为空映射。"""
    try:
        rows = db.query(User).all()
        return {u.id: (u.display_name or u.username) for u in rows}
    except Exception:  # noqa: BLE001
        return {}


def _scan_kind(wiki_root: Path, kind: str, user_map: dict[int, str]) -> list[QaDataItem]:
    sub = _QA_DIRS[kind]
    base = wiki_root / "wiki" / sub
    if not base.exists():
        return []
    items: list[QaDataItem] = []
    for p in base.glob("*.md"):
        # 跳过 synthesis/sessions/* 等子目录(只接受顶层 md)
        if p.parent != base:
            continue
        try:
            fm, _ = parse_text(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        slug = p.stem
        title = str(fm.get("title") or slug)
        citations = [str(c) for c in (fm.get("citations") or []) if c]
        asked_by = _coerce_int(fm.get("asked_by"))
        items.append(QaDataItem(
            slug=slug,
            kind=kind,  # type: ignore[arg-type]
            title=title,
            user_question=(fm.get("user_question") or (title if kind == "synthesis" else None)),
            asked_by=asked_by,
            asked_by_username=(user_map.get(asked_by) if asked_by is not None else None),
            created=_coerce_dt(fm.get("created")),
            updated=_coerce_dt(fm.get("updated")),
            citations=citations,
            citation_count=len(citations),
            answer_quality=_coerce_float(fm.get("answer_quality")),
            tier=(str(fm.get("tier")) if fm.get("tier") is not None else None),
            gap_flag=_coerce_bool(fm.get("gap_flag")),
            session_id=_coerce_int(fm.get("session_id")),
        ))
    return items


@router.get("/qa-data", response_model=QaDataList)
async def list_qa_data(
    user_id: AdminUserId,
    db: DbSession,
    kind: Optional[str] = Query(default=None, description="过滤 kind: query|synthesis|comparison"),
) -> QaDataList:
    wiki_root = settings.wiki_root_path
    user_map = _load_user_map(db)
    kinds = [kind] if kind in _QA_DIRS else list(_QA_DIRS.keys())
    all_items: list[QaDataItem] = []
    for k in kinds:
        all_items.extend(_scan_kind(wiki_root, k, user_map))

    # created desc,缺失放最后
    all_items.sort(key=lambda x: (x.created is None, -(x.created.timestamp() if x.created else 0)))

    counts_by_kind: dict[str, int] = {k: 0 for k in _QA_DIRS}
    for it in all_items:
        counts_by_kind[it.kind] = counts_by_kind.get(it.kind, 0) + 1

    return QaDataList(items=all_items, total=len(all_items), counts_by_kind=counts_by_kind)
