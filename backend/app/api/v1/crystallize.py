"""crystallize:用户文本 → synthesis 页。"""

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.core.deps import CurrentUserId, DbSession
from app.schemas.misc import CrystallizeRequest, CrystallizeResponse
from app.services.crystallize import crystallize_text

router = APIRouter()


@router.post("/crystallize", response_model=CrystallizeResponse, status_code=201)
async def crystallize(
    payload: CrystallizeRequest,
    user_id: CurrentUserId,
    db: DbSession,
) -> CrystallizeResponse:
    """把用户的笔记/对话总结结晶为 synthesis 页(或 comparison 页)。

    流程(规划文档 F7):
      - LLM 把零散文本凝练为结构化 markdown
      - 写 wiki/synthesis/{date}-{title}.md(或 wiki/comparisons/)
      - 抽 [[X]] 引用,wiki/ 下不存在的节点写入 gap_queue(origin=merge_suggestion)
      - 同步 wiki_pages/wiki_links + 写一条 wiki_node_versions(triggered_by=crystallize)
    """
    try:
        slug, title, candidates = await crystallize_text(
            text=payload.text,
            target_topic=payload.target_topic,
            kind=payload.kind,
            wiki_root=settings.wiki_root_path,
            db=db,
            user_id=user_id or None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.commit()
    # fire-and-forget 重建 graph-data.json,让前端图谱页立刻看到结晶出的新页
    try:
        import asyncio

        from app.services.graph.builder import (
            build_graph_data,
            write_graph_data_json,
        )

        async def _rebuild() -> None:
            try:
                payload = await build_graph_data(wiki_root=settings.wiki_root_path)
                await write_graph_data_json(wiki_root=settings.wiki_root_path, payload=payload)
            except Exception:  # noqa: BLE001
                pass

        asyncio.create_task(_rebuild())
    except RuntimeError:
        pass
    return CrystallizeResponse(slug=slug, title=title, candidates_added=candidates)
