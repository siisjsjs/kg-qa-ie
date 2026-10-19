"""ingestion:上传 / 流水线 / 批量。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    document_id: int
    file_path: str
    content_hash: str
    cached: bool = False
    privacy_warnings: list[str] = Field(default_factory=list)


class RunIngestRequest(BaseModel):
    force: bool = False  # 命中缓存时是否强制重跑


class IngestJobInfo(BaseModel):
    job_id: int
    document_id: int
    stage: Literal["upload", "cache_check", "step1_extract", "step2_compose", "post_process"]
    status: Literal["queued", "running", "succeeded", "failed", "skipped"]
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class BatchIngestRequest(BaseModel):
    document_ids: list[int] = Field(min_length=1)
    pause_every: int = Field(default=5, ge=1, le=50)


class BatchResultItem(BaseModel):
    document_id: int
    final_status: str
    source_slug: str | None = None
    error: str | None = None


class BatchIngestResponse(BaseModel):
    batch_id: str
    total: int
    next_pause_at: int
    status: str = "running"
    next_index: int = 0
    results: list[BatchResultItem] = Field(default_factory=list)


class BatchStateResponse(BaseModel):
    batch_id: str
    document_ids: list[int]
    pause_every: int
    next_index: int
    status: str
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    results: list[BatchResultItem] = Field(default_factory=list)


class BatchSummary(BaseModel):
    batch_id: str
    status: str
    total: int
    next_index: int
    succeeded: int = 0
    skipped: int = 0
    failed: int = 0
    started_at: str | None = None
    updated_at: str | None = None
