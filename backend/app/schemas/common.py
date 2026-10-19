"""通用 schema：分页、错误响应、SSE 事件等。"""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageMeta(BaseModel):
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=200)
    total: int = 0


class Page(BaseModel, Generic[T]):
    items: list[T] = Field(default_factory=list)
    meta: PageMeta = Field(default_factory=PageMeta)


class ErrorResponse(BaseModel):
    code: str
    message: str


class OkResponse(BaseModel):
    ok: bool = True
    message: str | None = None
