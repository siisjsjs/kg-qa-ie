"""conversations:多轮对话会话与消息持久化的 REST 接口。

按 user_id 隔离,所有路由要求 CurrentUserId,会话不属于该用户时返回 404。

路由:
  GET    /conversations                      列出当前用户的会话
  POST   /conversations                      创建空会话
  GET    /conversations/{id}                 取会话详情(含消息和 summary)
  PATCH  /conversations/{id}                 改 title
  DELETE /conversations/{id}                 删会话(级联删消息)
  GET    /conversations/{id}/messages        分页拉历史(预留;详情已包含)
  POST   /conversations/{id}/summarize       手动重新生成摘要
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.core.deps import CurrentUserId, DbSession
from app.schemas.common import OkResponse
from app.schemas.conversations import (
    ConversationCreateRequest,
    ConversationDetail,
    ConversationMessage,
    ConversationPatchRequest,
    ConversationSummary,
    SummarizeResponse,
)
from app.services.conversations import (
    SUMMARIZE_TOKEN_THRESHOLD,
    create_session,
    delete_session,
    estimate_tokens,
    force_resummarize,
    get_session,
    list_messages,
    list_sessions,
    rename_session,
)
from app.services.query.citations import enrich_citation_meta

router = APIRouter()


def _msg_to_schema(m) -> ConversationMessage:
    try:
        meta = json.loads(m.meta_json or "{}")
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    meta = enrich_citation_meta(meta, wiki_root=settings.wiki_root_path)
    return ConversationMessage(
        id=m.id,
        role=m.role if m.role in ("user", "assistant", "system") else "user",
        content=m.content or "",
        created_at=m.created_at,
        meta=meta,
    )


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    db: DbSession,
    user_id: CurrentUserId,
) -> list[ConversationSummary]:
    rows = list_sessions(db, user_id=user_id)
    return [ConversationSummary(**r) for r in rows]


@router.post("/conversations", response_model=ConversationDetail)
async def create_conversation(
    payload: ConversationCreateRequest,
    db: DbSession,
    user_id: CurrentUserId,
) -> ConversationDetail:
    s = create_session(db, user_id=user_id, title=payload.title)
    return ConversationDetail(
        id=s.id,
        title=s.title or "新对话",
        summary=s.summary or "",
        messages=[],
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@router.get("/conversations/{session_id}", response_model=ConversationDetail)
async def get_conversation(
    session_id: int,
    db: DbSession,
    user_id: CurrentUserId,
) -> ConversationDetail:
    try:
        s = get_session(db, session_id=session_id, user_id=user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="conversation not found")
    msgs = list_messages(db, session_id=session_id, user_id=user_id)
    return ConversationDetail(
        id=s.id,
        title=s.title or "新对话",
        summary=s.summary or "",
        messages=[_msg_to_schema(m) for m in msgs],
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@router.patch("/conversations/{session_id}", response_model=ConversationSummary)
async def patch_conversation(
    session_id: int,
    payload: ConversationPatchRequest,
    db: DbSession,
    user_id: CurrentUserId,
) -> ConversationSummary:
    if payload.title is None:
        raise HTTPException(status_code=400, detail="nothing to update")
    try:
        s = rename_session(
            db, session_id=session_id, user_id=user_id, title=payload.title.strip()
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="conversation not found")
    return ConversationSummary(
        id=s.id,
        title=s.title or "新对话",
        summary=s.summary or "",
        last_message_preview="",
        message_count=0,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@router.delete("/conversations/{session_id}", response_model=OkResponse)
async def delete_conversation(
    session_id: int,
    db: DbSession,
    user_id: CurrentUserId,
) -> OkResponse:
    try:
        delete_session(db, session_id=session_id, user_id=user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="conversation not found")
    return OkResponse(ok=True)


@router.get("/conversations/{session_id}/messages", response_model=list[ConversationMessage])
async def list_conversation_messages(
    session_id: int,
    db: DbSession,
    user_id: CurrentUserId,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[ConversationMessage]:
    try:
        msgs = list_messages(db, session_id=session_id, user_id=user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="conversation not found")
    sliced = msgs[offset : offset + limit]
    return [_msg_to_schema(m) for m in sliced]


@router.post("/conversations/{session_id}/summarize", response_model=SummarizeResponse)
async def summarize_conversation(
    session_id: int,
    db: DbSession,
    user_id: CurrentUserId,
) -> SummarizeResponse:
    try:
        msgs_before = list_messages(db, session_id=session_id, user_id=user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="conversation not found")
    summary, count = await force_resummarize(
        db, session_id=session_id, user_id=user_id
    )
    token_est = estimate_tokens(summary or "") + sum(
        estimate_tokens(m.content or "") for m in msgs_before
    )
    return SummarizeResponse(
        summary=summary,
        message_count=count,
        token_estimate=token_est,
    )


@router.get("/conversations/_meta")
async def conversation_meta(user_id: CurrentUserId) -> dict:  # noqa: ARG001
    """暴露后端摘要触发阈值,前端可用作进度提示。"""
    return {"summarize_token_threshold": SUMMARIZE_TOKEN_THRESHOLD}
