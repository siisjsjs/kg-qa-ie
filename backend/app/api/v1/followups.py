"""followups:待跟进队列。"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.core.deps import CurrentUserId, DbSession
from app.schemas.misc import FollowupItem
from app.services.growth.followups import (
    list_followups as list_followups_db,
    refresh_followups_sync,
)

router = APIRouter()


@router.get("/followups", response_model=list[FollowupItem])
async def list_followups(
    user_id: CurrentUserId,  # noqa: ARG001
    db: DbSession,
    refresh: bool = False,
    limit: int = 100,
) -> list[FollowupItem]:
    """聚合 wiki/queries / wiki/synthesis / wiki/comparisons 的 followups frontmatter。"""
    if refresh:
        try:
            refresh_followups_sync(db=db, wiki_root=settings.wiki_root_path)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    rows = list_followups_db(db, limit=limit)
    return [FollowupItem(**r) for r in rows]
