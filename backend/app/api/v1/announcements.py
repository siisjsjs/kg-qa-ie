"""公告（Announcement）API。

读：所有登录用户可见已发布的公告。
写/删/上传图：仅管理员。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from loguru import logger
from sqlalchemy import desc

from app.core.config import settings
from app.core.deps import AdminUserId, CurrentUserId, DbSession
from app.models.announcement import Announcement
from app.schemas.announcement import (
    AnnouncementCreate,
    AnnouncementList,
    AnnouncementOut,
    AnnouncementUpdate,
    ImageUploadResult,
)
from app.schemas.common import OkResponse

router = APIRouter(prefix="/announcements")

_ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}
_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


def _uploads_dir() -> Path:
    """公告图片落盘目录:与 wiki/ cache/ chroma/ 平级。"""
    cache_dir = Path(settings.cache_dir).resolve()
    base = cache_dir.parent / "uploads" / "announcements"
    base.mkdir(parents=True, exist_ok=True)
    return base


@router.get("", response_model=AnnouncementList)
async def list_announcements(user_id: CurrentUserId, db: DbSession) -> AnnouncementList:
    """列出所有 published 公告;置顶在前,然后按创建时间倒序。"""
    rows = (
        db.query(Announcement)
        .filter(Announcement.visibility == "published")
        .order_by(desc(Announcement.pinned), desc(Announcement.created_at))
        .all()
    )
    return AnnouncementList(items=[AnnouncementOut.model_validate(r) for r in rows])


@router.get("/admin", response_model=AnnouncementList)
async def list_announcements_admin(
    user_id: AdminUserId, db: DbSession
) -> AnnouncementList:
    """管理端:含 draft。"""
    rows = (
        db.query(Announcement)
        .order_by(desc(Announcement.pinned), desc(Announcement.created_at))
        .all()
    )
    return AnnouncementList(items=[AnnouncementOut.model_validate(r) for r in rows])


@router.get("/{ann_id}", response_model=AnnouncementOut)
async def get_announcement(
    ann_id: int, user_id: CurrentUserId, db: DbSession
) -> AnnouncementOut:
    row = db.query(Announcement).filter(Announcement.id == ann_id).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="announcement not found")
    return AnnouncementOut.model_validate(row)


@router.post("", response_model=AnnouncementOut, status_code=201)
async def create_announcement(
    payload: AnnouncementCreate, user_id: AdminUserId, db: DbSession
) -> AnnouncementOut:
    row = Announcement(
        title=payload.title,
        body_md=payload.body_md or "",
        cover_image_url=payload.cover_image_url,
        pinned=payload.pinned,
        visibility=payload.visibility,
        created_by=user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(f"announcement created id={row.id} title={row.title}")
    return AnnouncementOut.model_validate(row)


@router.put("/{ann_id}", response_model=AnnouncementOut)
async def update_announcement(
    ann_id: int,
    payload: AnnouncementUpdate,
    user_id: AdminUserId,
    db: DbSession,
) -> AnnouncementOut:
    row = db.query(Announcement).filter(Announcement.id == ann_id).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="announcement not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    logger.info(f"announcement updated id={row.id}")
    return AnnouncementOut.model_validate(row)


@router.delete("/{ann_id}", response_model=OkResponse)
async def delete_announcement(
    ann_id: int, user_id: AdminUserId, db: DbSession
) -> OkResponse:
    row = db.query(Announcement).filter(Announcement.id == ann_id).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="announcement not found")
    db.delete(row)
    db.commit()
    logger.info(f"announcement deleted id={ann_id}")
    return OkResponse(ok=True, message="deleted")


@router.post("/upload-image", response_model=ImageUploadResult, status_code=201)
async def upload_image(
    user_id: AdminUserId, file: UploadFile = File(...)
) -> ImageUploadResult:
    """上传公告图片;返回可对外访问的 /static/uploads/announcements/<uuid>.<ext> URL。"""
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported image type: {ext}. allowed: {sorted(_ALLOWED_IMAGE_EXTS)}",
        )

    target_name = f"{uuid.uuid4().hex}{ext}"
    target_path = _uploads_dir() / target_name

    # 写文件 + 大小检查(边写边算,避免预读全部到内存)
    written = 0
    with target_path.open("wb") as f:
        while chunk := await file.read(1024 * 64):
            written += len(chunk)
            if written > _MAX_IMAGE_BYTES:
                f.close()
                target_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="image too large (>5MB)")
            f.write(chunk)

    url = f"/static/uploads/announcements/{target_name}"
    logger.info(f"announcement image uploaded: {url} ({written} bytes)")
    return ImageUploadResult(url=url)
