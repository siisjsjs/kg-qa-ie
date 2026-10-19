"""conversations:多轮对话会话与消息 schemas。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ConversationMessage(BaseModel):
    """对话中的一条消息(用户或助手)。"""

    id: int
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime
    # assistant 消息可能带 quality/citations/path/persist 等元数据
    meta: dict = Field(default_factory=dict)


class ConversationSummary(BaseModel):
    """会话列表项(不含消息体)。"""

    id: int
    title: str
    summary: str = ""
    last_message_preview: str = ""
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class ConversationDetail(BaseModel):
    """单个会话详情(含消息)。"""

    id: int
    title: str
    summary: str = ""
    messages: list[ConversationMessage] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ConversationCreateRequest(BaseModel):
    title: str | None = None


class ConversationPatchRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class SummarizeResponse(BaseModel):
    summary: str
    message_count: int
    token_estimate: int
