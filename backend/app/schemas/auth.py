"""auth：注册、登录、当前用户。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    # 用户名:支持中文 / 英文数字 / 下划线点横线;3-32 字符
    # 不允许:空白、@(防与邮箱混淆)、特殊符号
    username: str = Field(min_length=2, max_length=32, pattern=r"^[一-龥A-Za-z0-9_.\-]+$")
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = None


class LoginRequest(BaseModel):
    """支持邮箱或用户名登录:account 字段同时接受两种。"""

    account: str = Field(min_length=1, max_length=255)
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


class UserInfo(BaseModel):
    id: int
    username: str
    email: EmailStr
    role: Literal["admin", "user"]
    status: Literal["active", "disabled"]
    display_name: str | None = None
    created_at: datetime


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
