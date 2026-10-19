"""status/aggregator.py:状态报告聚合(页面数 + 素材分布 + 飞轮指标)。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.gap_queue import GapQueue
from app.models.qa import QaMessage
from app.services.status.coverage import coverage as coverage_stats


def _count_pages(wiki_root: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    base = Path(wiki_root) / "wiki"
    if not base.exists():
        return out
    for sub in base.iterdir():
        if sub.is_dir():
            out[sub.name] = sum(1 for p in sub.glob("*.md") if p.is_file())
    return out


def _count_sources_by_type(wiki_root: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    raw = Path(wiki_root) / "raw"
    if not raw.exists():
        return out
    for sub in raw.iterdir():
        if sub.is_dir():
            out[sub.name] = sum(1 for p in sub.rglob("*.md") if p.is_file())
    return out


def _flywheel_from_db(db: Session) -> dict:
    """gap-queue / answer-quality 简单聚合。"""
    flywheel: dict = {}

    # gap-queue 按状态计数
    try:
        rows = db.execute(
            select(GapQueue.status, func.count(GapQueue.id)).group_by(GapQueue.status)
        ).all()
        flywheel["gap_queue"] = {status: int(n) for status, n in rows}
    except Exception:  # noqa: BLE001
        flywheel["gap_queue"] = {}

    # tier 分布
    try:
        rows = db.execute(
            select(QaMessage.tier, func.count(QaMessage.id)).group_by(QaMessage.tier)
        ).all()
        flywheel["answer_quality_tier"] = {(t or "unknown"): int(n) for t, n in rows}
    except Exception:  # noqa: BLE001
        flywheel["answer_quality_tier"] = {}

    # gap_flag 比例
    try:
        total = db.execute(select(func.count(QaMessage.id))).scalar_one() or 0
        flagged = db.execute(
            select(func.count(QaMessage.id)).where(QaMessage.gap_flag.is_(True))
        ).scalar_one() or 0
        flywheel["gap_flag_share"] = round((flagged / total) if total else 0.0, 4)
        flywheel["query_total"] = int(total)
    except Exception:  # noqa: BLE001
        flywheel["gap_flag_share"] = 0.0
        flywheel["query_total"] = 0

    return flywheel


async def aggregate(*, wiki_root: Path, db: Session | None = None) -> dict:
    wiki_root = Path(wiki_root).resolve()
    page_counts = _count_pages(wiki_root)
    source_counts = _count_sources_by_type(wiki_root)
    cov = await coverage_stats(wiki_root=wiki_root)

    flywheel: dict = {}
    if db is not None:
        try:
            flywheel = _flywheel_from_db(db)
        except Exception:  # noqa: BLE001
            flywheel = {}

    return {
        "page_counts": page_counts,
        "source_counts": source_counts,
        "flywheel": flywheel,
        "coverage": cov,
    }
