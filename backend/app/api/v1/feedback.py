"""用户反馈 API。

POST 任何登录用户(含 admin),GET/PUT/DELETE 仅管理员。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from loguru import logger
from sqlalchemy import desc

from app.core.deps import AdminUserId, CurrentUserId, DbSession
from app.models.feedback import Feedback
from app.models.user import User
from app.schemas.common import OkResponse
from app.schemas.feedback import (
    FeedbackCreate,
    FeedbackList,
    FeedbackOut,
    FeedbackUpdate,
)

router = APIRouter(prefix="/feedbacks")


def _to_out(row: Feedback, username: str | None) -> FeedbackOut:
    return FeedbackOut(
        id=row.id,
        user_id=row.user_id,
        username=username,
        category=row.category,
        content=row.content,
        contact=row.contact,
        status=row.status,
        admin_note=row.admin_note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("", response_model=FeedbackOut, status_code=201)
async def submit_feedback(
    payload: FeedbackCreate, user_id: CurrentUserId, db: DbSession
) -> FeedbackOut:
    """提交反馈;任何登录用户。"""
    row = Feedback(
        user_id=user_id if user_id else None,
        category=payload.category,
        content=payload.content,
        contact=payload.contact,
        status="open",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(f"feedback submitted id={row.id} user_id={user_id} category={row.category}")
    username = None
    if row.user_id:
        u = db.query(User).filter(User.id == row.user_id).one_or_none()
        username = u.username if u else None
    return _to_out(row, username)


@router.get("", response_model=FeedbackList)
async def list_feedbacks(
    user_id: AdminUserId,  # noqa: ARG001
    db: DbSession,
    status: str | None = None,
) -> FeedbackList:
    """管理员:列所有反馈,可按 status 过滤。"""
    q = db.query(Feedback)
    if status:
        q = q.filter(Feedback.status == status)
    rows = q.order_by(desc(Feedback.created_at)).all()

    # 一次拉相关 user 名,避免 N+1
    user_ids = {r.user_id for r in rows if r.user_id}
    name_map: dict[int, str] = {}
    if user_ids:
        for u in db.query(User).filter(User.id.in_(user_ids)).all():
            name_map[u.id] = u.username

    return FeedbackList(
        items=[_to_out(r, name_map.get(r.user_id) if r.user_id else None) for r in rows]
    )


@router.put("/{fb_id}", response_model=FeedbackOut)
async def update_feedback(
    fb_id: int,
    payload: FeedbackUpdate,
    user_id: AdminUserId,  # noqa: ARG001
    db: DbSession,
) -> FeedbackOut:
    row = db.query(Feedback).filter(Feedback.id == fb_id).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    username = None
    if row.user_id:
        u = db.query(User).filter(User.id == row.user_id).one_or_none()
        username = u.username if u else None
    return _to_out(row, username)


@router.delete("/{fb_id}", response_model=OkResponse)
async def delete_feedback(
    fb_id: int, user_id: AdminUserId, db: DbSession  # noqa: ARG001
) -> OkResponse:
    row = db.query(Feedback).filter(Feedback.id == fb_id).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    db.delete(row)
    db.commit()
    logger.info(f"feedback deleted id={fb_id}")
    return OkResponse(ok=True, message="deleted")
