"""admin：LLM/Embedding 配置、schema 编辑、purpose 编辑、gap 队列。"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    """LLM / Embedding 通用配置。"""

    provider: str  # deepseek / openai_compat / ollama / anthropic / ...
    api_key: str | None = None  # 写入时明文，读取时屏蔽
    base_url: str | None = None
    model: str
    extra_params: dict = Field(default_factory=dict)


class LLMConfigRequest(ProviderConfig):
    temperature: float = 0.3
    max_tokens: int = 4096


class EmbeddingConfigRequest(ProviderConfig):
    dim: int = 1024


class LLMConfigView(LLMConfigRequest):
    """读视图：api_key 已被屏蔽（前 4 位 + ****）。"""


class EmbeddingConfigView(EmbeddingConfigRequest):
    """读视图：api_key 已被屏蔽。"""


class ConfigSummary(BaseModel):
    llm: LLMConfigView
    embedding: EmbeddingConfigView
    available_llm_providers: list[str] = Field(default_factory=list)
    available_embedding_providers: list[str] = Field(default_factory=list)


class ConnectionTestRequest(BaseModel):
    target: Literal["llm", "embedding", "firecrawl", "tavily", "imagegen", "zhihuitu"]


class ConnectionTestResponse(BaseModel):
    ok: bool
    latency_ms: int | None = None
    sample_output: str | None = None
    error: str | None = None


class SchemaEditorPayload(BaseModel):
    """`.wiki-schema.md` 全文 + 结构化字段。"""

    raw_markdown: str
    aliases: list[dict] = Field(default_factory=list)  # [{canonical, members: [...]}]
    relation_types: list[dict] = Field(default_factory=list)  # [{name, description}]
    growth_config: dict = Field(default_factory=dict)


class PurposePayload(BaseModel):
    raw_markdown: str
    research_goals: list[str] = Field(default_factory=list)
    key_questions: list[str] = Field(default_factory=list)
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)


class GapQueueItem(BaseModel):
    id: int
    record_date: date | datetime
    origin: str
    user_question: str | None = None
    query_page: str | None = None
    digest_page: str | None = None
    gap_reasons: list[str] = Field(default_factory=list)
    suggested_seeds: list[str] = Field(default_factory=list)
    status: str
    resolved_by: int | None = None
    resolved_at: datetime | None = None
    resolved_via_ingest_job_id: int | None = None
    created_at: datetime
    updated_at: datetime | None = None


class GapQueueUpdate(BaseModel):
    status: str  # open / in_progress / resolved / dismissed / reopened
    resolved_via_ingest_job_id: int | None = None
