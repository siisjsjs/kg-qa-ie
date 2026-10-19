"""status:状态总览(页面数 + 素材分布 + 飞轮指标)。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from loguru import logger

from app.core.config import settings
from app.core.deps import CurrentUserId, DbSession
from app.schemas.misc import StatusReport
from app.services.query.vector_index import index_freshness
from app.services.status.aggregator import aggregate

router = APIRouter()


@router.get("/status", response_model=StatusReport)
async def get_status(user_id: CurrentUserId, db: DbSession) -> StatusReport:  # noqa: ARG001
    """生成全局状态报告。"""
    out = await aggregate(wiki_root=settings.wiki_root_path, db=db)
    try:
        freshness = await asyncio.to_thread(index_freshness, settings.wiki_root_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"status: vector index freshness unavailable: {exc!r}")
        freshness = {}
    return StatusReport(
        page_counts=out.get("page_counts", {}),
        source_counts=out.get("source_counts", {}),
        flywheel=out.get("flywheel", {}),
        coverage=out.get("coverage", {}),
        vector_index=freshness,
    )
