"""FastAPI 依赖注入函数集合。

dev 模式（APP_ENV=dev）下，鉴权依赖会被旁路（返回真实存在的第一个用户 id），
方便骨架阶段在没有用户体系时仍能验证管理接口。生产部署 (APP_ENV=prod)
强制走 JWT。

注:之前 dev 模式硬编码 user_id=0,但 users 表里通常没有 id=0 的行,
持有外键的子表写入会触发 FK 约束失败。这里改成从 DB 拉第一个 active 用户;
拉不到才回退 0(主要发生在 DB 未初始化的最早期)。
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db, session_scope
from app.core.security import decode_token

DEV_USER_ID_FALLBACK = 0
_dev_user_id_cache: int | None = None


def _resolve_dev_user_id() -> int:
    """dev 模式下取一个真实用户 id;DB 没用户时回退 0。"""
    global _dev_user_id_cache
    if _dev_user_id_cache is not None:
        return _dev_user_id_cache
    try:
        from app.models.user import User  # 延迟 import 防循环
        with session_scope() as db:
            row = db.execute(
                select(User.id)
                .where(User.status == "active")
                .order_by(User.id)
                .limit(1)
            ).first()
            if row is not None:
                _dev_user_id_cache = int(row[0])
                return _dev_user_id_cache
    except Exception:  # noqa: BLE001
        pass
    _dev_user_id_cache = DEV_USER_ID_FALLBACK
    return DEV_USER_ID_FALLBACK


def get_db_dep() -> Iterator[Session]:
    yield from get_db()


DbSession = Annotated[Session, Depends(get_db_dep)]


def _is_dev() -> bool:
    return settings.app_env.lower() in {"dev", "development", "local"}


def get_current_user_id(authorization: str | None = Header(default=None)) -> int:
    """从 Authorization: Bearer <token> 提取 user_id。

    dev 模式：缺 token 时返回真实存在的第一个用户 id；带了 token 仍按正常路径解码。
    prod 模式：必须带有效 token。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        if _is_dev():
            return _resolve_dev_user_id()
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
    except Exception as e:
        if _is_dev():
            return _resolve_dev_user_id()
        raise HTTPException(status_code=401, detail=f"invalid token: {e}") from e
    sub = payload.get("sub")
    if sub is None:
        if _is_dev():
            return _resolve_dev_user_id()
        raise HTTPException(status_code=401, detail="token missing sub")
    return int(sub)


def require_admin(user_id: Annotated[int, Depends(get_current_user_id)]) -> int:
    """管理员权限：dev 模式下任何用户都通过；prod 模式 TODO 接 RBAC。"""
    return user_id


CurrentUserId = Annotated[int, Depends(get_current_user_id)]
AdminUserId = Annotated[int, Depends(require_admin)]
