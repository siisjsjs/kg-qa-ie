"""认证:注册、登录、当前用户。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from loguru import logger
from sqlalchemy import or_

from app.core.config import settings
from app.core.deps import CurrentUserId, DbSession
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserInfo,
)
from app.schemas.common import OkResponse

router = APIRouter(prefix="/auth")


@router.post("/register", response_model=UserInfo, status_code=201)
async def register(payload: RegisterRequest, db: DbSession) -> UserInfo:
    """注册新用户。第一个注册的用户自动成为 admin;后续 role=user。"""
    conflict = (
        db.query(User)
        .filter(or_(User.email == payload.email, User.username == payload.username))
        .one_or_none()
    )
    if conflict is not None:
        if conflict.email == payload.email:
            logger.info(f"注册失败:邮箱已被注册 email={payload.email}")
            raise HTTPException(status_code=409, detail="该邮箱已被注册")
        logger.info(f"注册失败:用户名已被占用 username={payload.username}")
        raise HTTPException(status_code=409, detail="该用户名已被占用")

    user_count = db.query(User).count()
    role = "admin" if user_count == 0 else "user"

    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=role,
        status="active",
        display_name=payload.display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(f"注册成功 id={user.id} username={user.username} role={user.role}")
    return UserInfo(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,  # type: ignore[arg-type]
        status=user.status,  # type: ignore[arg-type]
        display_name=user.display_name,
        created_at=user.created_at,
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: DbSession) -> TokenResponse:
    """用户名或邮箱 + 密码登录,返回 access token。"""
    account = payload.account.strip()
    user = (
        db.query(User)
        .filter(or_(User.username == account, User.email == account))
        .one_or_none()
    )
    if user is None:
        logger.info(f"登录失败:账号不存在 account={account}")
        raise HTTPException(status_code=401, detail="账号不存在")
    if not verify_password(payload.password, user.password_hash):
        logger.info(f"登录失败:密码错误 username={user.username}")
        raise HTTPException(status_code=401, detail="密码错误")
    if user.status != "active":
        logger.info(f"登录失败:账号已禁用 username={user.username}")
        raise HTTPException(status_code=403, detail="账号已禁用")
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    token = create_access_token(user.id, extra_claims={"role": user.role})
    refresh = create_refresh_token(user.id, extra_claims={"role": user.role})
    logger.info(f"登录成功 id={user.id} username={user.username}")
    return TokenResponse(
        access_token=token,
        refresh_token=refresh,
        expires_in=settings.jwt_access_ttl_min * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: DbSession) -> TokenResponse:
    """用 refresh token 换取新 access token;签发新 refresh token 实现轮换,保证持久化登录。"""
    try:
        claims = decode_token(payload.refresh_token, expected_type="refresh")
    except Exception:
        logger.warning("刷新失败:refresh token 无效或已过期")
        raise HTTPException(status_code=401, detail="登录已过期,请重新登录")
    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="登录已过期,请重新登录")
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="账号不存在")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="账号已禁用")
    token = create_access_token(user.id, extra_claims={"role": user.role})
    refresh = create_refresh_token(user.id, extra_claims={"role": user.role})
    logger.info(f"刷新成功 id={user.id} username={user.username}")
    return TokenResponse(
        access_token=token,
        refresh_token=refresh,
        expires_in=settings.jwt_access_ttl_min * 60,
    )


@router.get("/me", response_model=UserInfo)
async def me(user_id: CurrentUserId, db: DbSession) -> UserInfo:
    """获取当前登录用户信息。"""
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return UserInfo(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,  # type: ignore[arg-type]
        status=user.status,  # type: ignore[arg-type]
        display_name=user.display_name,
        created_at=user.created_at,
    )


@router.post("/logout", response_model=OkResponse)
async def logout() -> OkResponse:
    """登出（幂等）。JWT 无状态,服务端仅确认成功,由前端负责丢弃本地 token。"""
    logger.info("登出请求:清除本地凭证即可")
    return OkResponse(ok=True, message="已退出登录")


@router.post("/change-password", response_model=OkResponse)
async def change_password(
    payload: ChangePasswordRequest, user_id: CurrentUserId, db: DbSession
) -> OkResponse:
    """修改当前登录用户密码。"""
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not verify_password(payload.old_password, user.password_hash):
        logger.info(f"修改密码失败:旧密码错误 username={user.username}")
        raise HTTPException(status_code=400, detail="旧密码不正确")
    if payload.old_password == payload.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与旧密码相同")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    logger.info(f"密码已更新 id={user.id} username={user.username}")
    return OkResponse(ok=True, message="密码已更新")
