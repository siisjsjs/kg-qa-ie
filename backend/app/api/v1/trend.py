"""trend:热门主题与对比对。"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.core.deps import CurrentUserId, DbSession
from app.schemas.misc import TrendItem, TrendReport
from app.services.growth.hot_topics import list_hot_topics, refresh_hot_topics_sync

router = APIRouter()


@router.get("/trend", response_model=TrendReport)
async def get_trend(
    user_id: CurrentUserId,  # noqa: ARG001
    db: DbSession,
    refresh: bool = False,
    window_days: int = 30,
) -> TrendReport:
    """周期性聚合产出热门主题、潜在对比对、自动晋升候选。

    - refresh=true 时重新跑聚合;否则直接读 hot_topics 表
    - 自动晋升候选默认 query_count >= 阈值 且 gap_share > 0.5
    """
    if refresh:
        try:
            refresh_hot_topics_sync(
                db=db, window_days=window_days, wiki_root=settings.wiki_root_path
            )
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    items = list_hot_topics(db, limit=30)
    hot_topics = [
        TrendItem(
            theme=it["theme"],
            query_count_30d=it["query_count_30d"],
            gap_share=it["gap_share"],
            auto_promote_candidate=it["auto_promote_candidate"],
        )
        for it in items
    ]

    # 简单算"对比对":auto_promote_candidate=True 的两两组合
    promoted = [it["theme"] for it in items if it["auto_promote_candidate"]][:6]
    pairs: list[tuple[str, str]] = []
    for i in range(len(promoted)):
        for j in range(i + 1, len(promoted)):
            pairs.append((promoted[i], promoted[j]))

    return TrendReport(hot_topics=hot_topics, comparison_pairs=pairs[:10])
