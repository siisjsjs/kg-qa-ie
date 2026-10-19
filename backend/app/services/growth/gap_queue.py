"""growth/gap_queue.py:缺口队列读写。

镜像 wiki/_growth/gap-queue.md → MySQL gap_queue 表。

状态机(规划文档 §3.6.1):
    open → in_progress → resolved
                       └→ dismissed
    resolved → reopened(7 天后 answer-quality 仍 < 0.6 时由维护任务触发)
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import session_scope
from app.models.gap_queue import GapQueue

VALID_STATUS = {"open", "in_progress", "resolved", "dismissed", "reopened"}
VALID_ORIGIN = {
    "query",
    "digest",
    "comparison_cell",
    "graph_isolated",
    "graph_sparse_community",
    "merge_suggestion",
    "manual",
}


def _ensure_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return list(v)
    if isinstance(v, str):
        return [v] if v else []
    return list(v)


def _row_to_dict(row: GapQueue) -> dict:
    try:
        reasons = json.loads(row.gap_reasons) if row.gap_reasons else []
    except (json.JSONDecodeError, TypeError):
        reasons = [row.gap_reasons] if row.gap_reasons else []
    if not isinstance(reasons, list):
        reasons = [str(reasons)]
    try:
        seeds = json.loads(row.suggested_seeds_json) if row.suggested_seeds_json else []
    except (json.JSONDecodeError, TypeError):
        seeds = []
    if not isinstance(seeds, list):
        seeds = [str(seeds)]
    return {
        "id": row.id,
        "record_date": row.record_date,
        "origin": row.origin,
        "user_question": row.user_question,
        "query_page": row.query_page,
        "digest_page": row.digest_page,
        "gap_reasons": reasons,
        "suggested_seeds": seeds,
        "status": row.status,
        "resolved_by": row.resolved_by,
        "resolved_at": row.resolved_at,
        "resolved_via_ingest_job_id": getattr(row, "resolved_via_ingest_job_id", None),
        "created_at": row.created_at,
        "updated_at": getattr(row, "updated_at", None),
    }


async def append_gap(
    *,
    db: Session | None = None,
    record_date: date | None = None,
    origin: str,
    user_question: str | None = None,
    query_page: str | None = None,
    digest_page: str | None = None,
    gap_reasons: list[str] | None = None,
    suggested_seeds: list[str] | None = None,
    status: str = "open",
) -> int:
    """写一条 gap 记录,返回 id。同步刷新 wiki/_growth/gap-queue.md。

    Args:
        db: 可选 SA Session;不传时自动开 session_scope(commit/close)
    """
    if origin not in VALID_ORIGIN:
        raise ValueError(f"invalid gap origin: {origin}")
    if status not in VALID_STATUS:
        raise ValueError(f"invalid gap status: {status}")

    record_date = record_date or date.today()
    reasons = _ensure_list(gap_reasons)
    seeds = _ensure_list(suggested_seeds)

    row = GapQueue(
        record_date=record_date,
        origin=origin,
        user_question=user_question,
        query_page=query_page,
        digest_page=digest_page,
        gap_reasons=json.dumps(reasons, ensure_ascii=False),
        suggested_seeds_json=json.dumps(seeds, ensure_ascii=False),
        status=status,
    )

    if db is not None:
        db.add(row)
        db.flush()
        gap_id = row.id
    else:
        with session_scope() as s:
            s.add(row)
            s.flush()
            gap_id = row.id

    # 同步 markdown 镜像(失败不阻塞)
    try:
        write_gap_queue_markdown()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gap_queue: markdown sync failed: {e}")

    return gap_id


async def list_gaps(
    *,
    db: Session | None = None,
    status: str | None = "open",
    limit: int = 100,
    origin: str | None = None,
) -> list[dict]:
    """列出某状态的 gap;status=None 返回全部。"""
    def _query(s: Session) -> list[dict]:
        stmt = select(GapQueue)
        if status is not None:
            stmt = stmt.where(GapQueue.status == status)
        if origin is not None:
            stmt = stmt.where(GapQueue.origin == origin)
        stmt = stmt.order_by(GapQueue.created_at.desc()).limit(limit)
        rows = list(s.execute(stmt).scalars())
        return [_row_to_dict(r) for r in rows]

    if db is not None:
        return _query(db)
    with session_scope() as s:
        return _query(s)


async def list_open_gaps(limit: int = 100) -> list[dict]:
    return await list_gaps(status="open", limit=limit)


async def update_status(
    *,
    gap_id: int,
    status: str,
    resolved_by: int | None = None,
    resolved_via_ingest_job_id: int | None = None,
    db: Session | None = None,
) -> dict:
    """变更 gap 状态。返回更新后的字典。"""
    if status not in VALID_STATUS:
        raise ValueError(f"invalid gap status: {status}")

    def _do(s: Session) -> dict:
        row = s.get(GapQueue, gap_id)
        if row is None:
            raise LookupError(f"gap_queue id={gap_id} not found")
        row.status = status
        if status in ("resolved", "dismissed"):
            row.resolved_at = datetime.now(timezone.utc)
            if resolved_by is not None:
                row.resolved_by = resolved_by
            if resolved_via_ingest_job_id is not None and hasattr(row, "resolved_via_ingest_job_id"):
                row.resolved_via_ingest_job_id = resolved_via_ingest_job_id
        elif status == "reopened":
            row.resolved_at = None
        s.flush()
        return _row_to_dict(row)

    if db is not None:
        out = _do(db)
    else:
        with session_scope() as s:
            out = _do(s)

    try:
        write_gap_queue_markdown()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gap_queue: markdown sync failed: {e}")
    return out


def reopen_stale_resolved(
    *,
    db: Session,
    score_threshold: float = 0.6,
    age_days: int = 7,
) -> int:
    """扫 resolved 项关联的 query_page,如果 7 天后仍 score<阈值 → 改为 reopened。

    返回 reopened 条数。
    """
    from datetime import timedelta

    from app.models.qa import QaMessage

    cutoff = datetime.now(timezone.utc) - timedelta(days=age_days)
    stmt = select(GapQueue).where(GapQueue.status == "resolved")
    rows = list(db.execute(stmt).scalars())
    reopened = 0
    for r in rows:
        if r.resolved_at is None:
            continue
        if r.resolved_at.tzinfo is None:
            resolved_at = r.resolved_at.replace(tzinfo=timezone.utc)
        else:
            resolved_at = r.resolved_at
        if resolved_at > cutoff:
            continue
        if not r.query_page:
            continue
        msg = db.execute(
            select(QaMessage).where(QaMessage.persisted_query_slug == r.query_page).order_by(
                QaMessage.created_at.desc()
            ).limit(1)
        ).scalar_one_or_none()
        if msg is None:
            continue
        if msg.answer_quality is not None and msg.answer_quality < score_threshold:
            r.status = "reopened"
            r.resolved_at = None
            reopened += 1
    db.flush()
    return reopened


# ---------------------------------------------------------------------------
# markdown 镜像
# ---------------------------------------------------------------------------

def _format_row_md(item: dict) -> str:
    seeds = ", ".join(f"`{s}`" for s in item.get("suggested_seeds") or [])
    reasons = "; ".join(item.get("gap_reasons") or [])
    target = item.get("user_question") or item.get("digest_page") or item.get("query_page") or "-"
    target = str(target).replace("|", "/").replace("\n", " ")[:80]
    return (
        f"| {item['id']} | {item.get('record_date') or ''} | {item.get('origin')} | "
        f"{target} | {reasons} | {seeds} |"
    )


def write_gap_queue_markdown(*, wiki_root: Path | None = None) -> Path | None:
    """把当前 gap_queue 表内容写到 wiki/_growth/gap-queue.md。"""
    root = Path(wiki_root) if wiki_root else Path(settings.wiki_root_path)
    target = root / "wiki" / "_growth" / "gap-queue.md"
    if not target.parent.exists():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None

    try:
        with session_scope() as s:
            stmt = select(GapQueue).order_by(GapQueue.created_at.desc())
            rows = list(s.execute(stmt).scalars())
            buckets: dict[str, list[dict]] = {k: [] for k in (
                "open", "in_progress", "resolved", "dismissed", "reopened"
            )}
            for r in rows:
                buckets.setdefault(r.status, []).append(_row_to_dict(r))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gap_queue.write_gap_queue_markdown failed reading DB: {e}")
        return None

    now = datetime.now(timezone.utc).isoformat()
    lines: list[str] = [
        "---",
        "title: 知识缺口队列",
        "slug: gap-queue",
        "kind: growth",
        f"generated_at: {now}",
        "---",
        "",
        "# 知识缺口队列",
        "",
        "> 本文件由 services/growth/gap_queue.py 自动维护;管理员可在前端\"成长仪表盘\"挑选条目消化。",
        "",
    ]

    section_titles = [
        ("open", "待消化(status=open)"),
        ("in_progress", "处理中(status=in_progress)"),
        ("resolved", "已完成(status=resolved)"),
        ("dismissed", "已忽略(status=dismissed)"),
        ("reopened", "已重启(status=reopened)"),
    ]
    for key, title in section_titles:
        items = buckets.get(key) or []
        lines.append(f"## {title}")
        lines.append("")
        if not items:
            lines.append("_无_")
            lines.append("")
            continue
        lines.append("| ID | 日期 | 来源 | 用户问题/digest | 缺口原因 | 建议种子 |")
        lines.append("|----|------|------|------|---------|---------|")
        lines.extend(_format_row_md(it) for it in items)
        lines.append("")

    try:
        target.write_text("\n".join(lines), encoding="utf-8")
        return target
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gap_queue.write_gap_queue_markdown failed writing: {e}")
        return None
