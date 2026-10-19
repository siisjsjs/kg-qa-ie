"""安全：密码 hash、JWT 编解码、当前用户依赖。"""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import settings

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(plain: str) -> str:
    """对明文密码进行 argon2 hash。"""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """验证明文密码与已 hash 值是否匹配。"""
    return _pwd_context.verify(plain, hashed)


def create_access_token(subject: str | int, *, extra_claims: dict[str, Any] | None = None) -> str:
    """签发 access token。subject 通常是 user_id。"""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_ttl_min)).timestamp()),
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str | int, *, extra_claims: dict[str, Any] | None = None) -> str:
    """签发 refresh token,用于在 access 过期后无感续期,保持持久化登录。"""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_refresh_ttl_days)).timestamp()),
        "type": "refresh",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any]:
    """解码 JWT。失败抛 jwt.PyJWTError;若指定 expected_type 且不匹配也抛错。"""
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if expected_type is not None and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"token type mismatch: expected {expected_type}")
    return payload
