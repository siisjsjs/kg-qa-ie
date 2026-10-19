"""ingestion:上传 / 触发流水线 / 批量(可暂停) / 任务状态。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.deps import AdminUserId, DbSession
from app.core.db import session_scope
from app.models.document import Document
from app.models.ingest_job import IngestJob
from app.models.wiki_node_version import WikiNodeVersion
from app.schemas.ingestion import (
    BatchIngestRequest,
    BatchIngestResponse,
    BatchStateResponse,
    BatchSummary,
    IngestJobInfo,
    RunIngestRequest,
    UploadResponse,
)
from app.services.ingestion import batch_state as batch_state_mod
from app.services.ingestion import pipeline as pipeline_mod
from app.services.wiki.init import init_wiki as init_wiki_fs

router = APIRouter()


class InitResponse(BaseModel):
    wiki_root: str
    created: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


@router.post("/init", response_model=InitResponse, status_code=201)
async def init_wiki(user_id: AdminUserId) -> InitResponse:
    """初始化知识库目录(首次部署或重置时调用,幂等)。"""
    result = await asyncio.to_thread(init_wiki_fs, settings.wiki_root)
    return InitResponse(**result)


@router.post("/ingestion/upload", response_model=UploadResponse, status_code=201)
async def upload_md(
    user_id: AdminUserId,
    db: DbSession,
    file: UploadFile = File(...),
    source_type: str = "md",
    title: str | None = None,
    book: str | None = None,
) -> UploadResponse:
    """上传 .md 素材。

    流程:
      1. 隐私自查:block 类命中 → 422
      2. 内容 hash + raw 落盘 + documents 行

    `book`:上传时选定的目标知识库("书")。写入 documents.book,post_process
    阶段落到 source frontmatter.book;为空时章节归「未分类」。
    """
    raw_bytes = await file.read()
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail=f"file is not valid UTF-8: {e}") from e

    # multipart filename 的 header 默认按 latin-1 解码,中文会乱码 → 还原成 utf-8
    original_filename = file.filename or "untitled.md"
    try:
        original_filename = original_filename.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass  # 已经是 utf-8 / ascii,保持原样

    doc, privacy = await pipeline_mod.stage_upload_md(
        db=db,
        owner_id=user_id,
        raw_content=raw_text,
        source_type=source_type,
        original_filename=original_filename,
        title=title,
        book=book,
    )
    if doc is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PRIVACY_BLOCKED",
                "message": "内容含敏感信息(身份证 / API key / URL token),请脱敏后再上传",
                "hits": privacy["hits"],
            },
        )

    db.commit()
    return UploadResponse(
        document_id=doc.id,
        file_path=doc.file_path,
        content_hash=doc.content_hash,
        cached=False,  # cache 命中由 run_pipeline 阶段判定,这里只做上传
        privacy_warnings=[h["category"] for h in privacy["hits"]],
    )


@router.post("/ingestion/{document_id}/run", status_code=202)
async def run_ingest(
    document_id: int,
    payload: RunIngestRequest,
    user_id: AdminUserId,
    db: DbSession,
    background: BackgroundTasks,
) -> dict[str, Any]:
    """触发 5 阶段流水线(异步)。

    立即把 documents.parse_status 置 running,把 pipeline.run_pipeline 丢到
    BackgroundTasks 后台跑;接口立刻 202 返回,前端轮询 /progress 看实时阶段。
    payload.force=true 时即使 cache 命中也强制重跑。
    """
    doc = db.query(Document).filter(Document.id == document_id).one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail=f"document {document_id} not found")
    doc.parse_status = "running"
    doc.parse_error = None
    db.commit()
    background.add_task(_run_pipeline_bg, document_id, payload.force)
    return {"document_id": document_id, "status": "queued"}


async def _run_pipeline_bg(document_id: int, force: bool) -> None:
    """后台任务包装:run_pipeline 内部已经把异常捕获并落 ingest_jobs/documents,
    这里再加一层兜底 logger.exception 防止 BackgroundTasks 静默吞错。"""
    try:
        await pipeline_mod.run_pipeline(document_id=document_id, force=force)
    except Exception:  # noqa: BLE001
        logger.exception(f"run_pipeline_bg failed for doc#{document_id}")
        # 兜底把 documents.parse_status 置失败,避免前端轮询永远看到 running
        with session_scope() as db:
            d = db.query(Document).filter(Document.id == document_id).one_or_none()
            if d is not None and d.parse_status == "running":
                d.parse_status = "failed"
                d.parse_error = "background task crashed; see backend/logs/app.log"


# ---------------------------------------------------------------------------
# 批量摄入(P3:可暂停可恢复状态机)
# ---------------------------------------------------------------------------


@router.post("/ingestion/batch", response_model=BatchIngestResponse)
async def batch_ingest(
    payload: BatchIngestRequest,
    user_id: AdminUserId,
    db: DbSession,
) -> BatchIngestResponse:
    """启动一个批量摄入。

    P3 行为:
      - 校验所有 doc_id 存在
      - 登记 batch_state(running)
      - **同步**跑第一段(直到 pause_every 个或全跑完)
      - 返回最新 state(若仍 paused → 前端调 /batch/{id}/continue 继续)
    """
    docs = db.query(Document).filter(Document.id.in_(payload.document_ids)).all()
    if len(docs) != len(payload.document_ids):
        missing = set(payload.document_ids) - {d.id for d in docs}
        raise HTTPException(status_code=404, detail=f"documents not found: {sorted(missing)}")
    db.commit()

    batch_id = batch_state_mod.start_batch(
        document_ids=payload.document_ids, pause_every=payload.pause_every
    )
    state = await batch_state_mod.run_chunk(batch_id)
    return _state_to_response(state)


@router.get("/ingestion/batch/{batch_id}", response_model=BatchStateResponse)
async def get_batch_state(batch_id: str, user_id: AdminUserId) -> BatchStateResponse:
    state = batch_state_mod.get_state(batch_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"batch {batch_id!r} not found")
    return BatchStateResponse(**state)


@router.post("/ingestion/batch/{batch_id}/continue", response_model=BatchStateResponse)
async def continue_batch(batch_id: str, user_id: AdminUserId) -> BatchStateResponse:
    """继续一个 paused 批的下一段。"""
    try:
        state = await batch_state_mod.continue_batch(batch_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return BatchStateResponse(**state)


@router.post("/ingestion/batch/{batch_id}/cancel", response_model=BatchStateResponse)
async def cancel_batch(batch_id: str, user_id: AdminUserId) -> BatchStateResponse:
    try:
        state = batch_state_mod.cancel_batch(batch_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return BatchStateResponse(**state)


@router.get("/ingestion/batches", response_model=list[BatchSummary])
async def list_batches(user_id: AdminUserId) -> list[BatchSummary]:
    return [BatchSummary(**s) for s in batch_state_mod.list_batches()]


def _state_to_response(state: dict[str, Any]) -> BatchIngestResponse:
    total = len(state["document_ids"])
    next_index = int(state["next_index"])
    pause_every = int(state["pause_every"])
    next_pause_at = min(
        ((next_index // pause_every) + 1) * pause_every, total
    )
    return BatchIngestResponse(
        batch_id=state["batch_id"],
        total=total,
        next_pause_at=next_pause_at,
        status=state["status"],
        next_index=next_index,
        results=state.get("results", []),
    )


# ---------------------------------------------------------------------------
# job 状态查询
# ---------------------------------------------------------------------------


@router.get("/ingestion/jobs", response_model=list[IngestJobInfo])
async def list_recent_jobs(
    user_id: AdminUserId,  # noqa: ARG001
    db: DbSession,
    limit: int = 100,
) -> list[IngestJobInfo]:
    """最近的 ingestion job(按 id 倒序)。前端 onMounted 用来恢复流水线列表。"""
    n = max(1, min(limit, 500))
    rows = (
        db.query(IngestJob).order_by(IngestJob.id.desc()).limit(n).all()
    )
    return [
        IngestJobInfo(
            job_id=j.id,
            document_id=j.document_id,
            stage=j.stage,
            status=j.status,
            error=j.error,
            started_at=j.started_at,
            finished_at=j.finished_at,
        )
        for j in rows
    ]


@router.get("/ingestion/jobs/{job_id}", response_model=IngestJobInfo)
async def get_job(job_id: int, user_id: AdminUserId, db: DbSession) -> IngestJobInfo:
    job = db.query(IngestJob).filter(IngestJob.id == job_id).one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail=f"ingest_job {job_id} not found")
    return IngestJobInfo(
        job_id=job.id,
        document_id=job.document_id,
        stage=job.stage,
        status=job.status,
        error=job.error,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.get("/ingestion/documents/{document_id}/jobs")
async def list_document_jobs(
    document_id: int, user_id: AdminUserId, db: DbSession
) -> list[IngestJobInfo]:
    """查询文档对应的所有阶段 job(供前端进度面板用)。"""
    jobs = (
        db.query(IngestJob)
        .filter(IngestJob.document_id == document_id)
        .order_by(IngestJob.id.asc())
        .all()
    )
    return [
        IngestJobInfo(
            job_id=j.id,
            document_id=j.document_id,
            stage=j.stage,
            status=j.status,
            error=j.error,
            started_at=j.started_at,
            finished_at=j.finished_at,
        )
        for j in jobs
    ]


# ---------------------------------------------------------------------------
# 文档级进度聚合 + job 详情(P5+:Sources.vue 文档级 UX 用)
# ---------------------------------------------------------------------------


_PIPELINE_STAGES = ("cache_check", "step1_extract", "step2_compose", "post_process")


@router.get("/ingestion/documents/{document_id}/progress")
async def get_document_progress(
    document_id: int, user_id: AdminUserId, db: DbSession  # noqa: ARG001
) -> dict[str, Any]:
    """文档级进度聚合(供前端轮询):一次返回 parse_status / 当前阶段 / 已完成阶段数。

    返回字段:
      - parse_status:pending / running / succeeded / failed
      - current_stage:running 中的 stage 名,没有则取最后一行(失败优先)
      - stages_done / stages_total / progress(0-100):已完成阶段数 / 4
      - failed_job_id:失败时 != null,前端"详情"按钮调 /jobs/{id}/detail
      - source_slug:post_process 成功后从 payload 拿,前端"查看"按钮跳 wiki
    """
    doc = db.query(Document).filter(Document.id == document_id).one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail=f"document {document_id} not found")

    jobs = (
        db.query(IngestJob)
        .filter(IngestJob.document_id == document_id)
        .order_by(IngestJob.id.asc())
        .all()
    )
    # 同一 doc 多次 /run 会产生多组 jobs(每组共享 correlation_id)。
    # 进度面板只关心最新一组,否则旧轮次的 skipped/succeeded 行会被错算成 done。
    if jobs:
        latest_corr = jobs[-1].correlation_id
        run_jobs = [j for j in jobs if j.correlation_id == latest_corr]
    else:
        run_jobs = []
    # 同一 stage 重复(重试)取最新一行
    latest_by_stage: dict[str, IngestJob] = {}
    for j in run_jobs:
        if j.stage in _PIPELINE_STAGES:
            latest_by_stage[j.stage] = j  # 后写覆盖前写,id 顺序已升序
    done = sum(
        1 for j in latest_by_stage.values()
        if j.status in ("succeeded", "skipped", "failed")
    )
    latest_jobs = list(latest_by_stage.values())
    running = next((j for j in latest_jobs if j.status == "running"), None)
    failed = next((j for j in latest_jobs if j.status == "failed"), None)
    last = latest_jobs[-1] if latest_jobs else None
    current_stage = (running.stage if running else (failed.stage if failed else (last.stage if last else None)))

    # post_process 成功后,从其 payload_json 抽 source_slug;cache_check skipped 时也带
    source_slug: str | None = None
    pp = latest_by_stage.get("post_process")
    cc = latest_by_stage.get("cache_check")
    if pp is not None and pp.status == "succeeded":
        try:
            p = json.loads(pp.payload_json or "{}")
            source_slug = p.get("source_slug") or None
        except Exception:  # noqa: BLE001
            pass
    if not source_slug and cc is not None and cc.status == "skipped":
        try:
            p = json.loads(cc.payload_json or "{}")
            if p.get("hit"):
                source_slug = p.get("source_slug") or source_slug
        except Exception:  # noqa: BLE001
            pass

    total = len(_PIPELINE_STAGES)
    return {
        "document_id": document_id,
        "original_filename": doc.original_filename,
        "parse_status": doc.parse_status,
        "parse_error": doc.parse_error,
        "current_stage": current_stage,
        "stages_done": done,
        "stages_total": total,
        "progress": int(done / total * 100) if total else 0,
        "failed_job_id": failed.id if failed else None,
        "source_slug": source_slug,
    }


@router.delete("/ingestion/documents/{document_id}")
async def delete_document(
    document_id: int, user_id: AdminUserId, db: DbSession,  # noqa: ARG001
) -> dict[str, Any]:
    """删除一条 documents 行 + 其 ingest_jobs + raw 文件。

    用途:Sources.vue 上失败 / 取消的素材"删除"按钮 — 这类行没有 source_slug,
    所以无法走 /sources/{slug} 级联清理,只能按 document_id 清干净。
    **不动 wiki/*** — 该路径只清"上传痕迹",不影响已生成的 wiki 内容。
    """
    import os
    from pathlib import Path as _Path

    doc = db.query(Document).filter(Document.id == document_id).one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail=f"document {document_id} not found")

    # 删 raw 文件(失败也不阻塞)
    raw_removed = False
    if doc.file_path:
        try:
            p = _Path(doc.file_path)
            if p.exists():
                os.remove(p)
                raw_removed = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"delete_document: raw 删除失败 doc#{document_id} {e}")

    # 删 ingest_jobs
    n_jobs = (
        db.query(IngestJob).filter(IngestJob.document_id == document_id).delete(synchronize_session=False)
    )
    # wiki_node_versions.document_id 有 FK → documents.id;不删版本历史(属 wiki 内容),
    # 仅把外键置空,解开约束后才能删 documents 行。
    n_ver = (
        db.query(WikiNodeVersion)
        .filter(WikiNodeVersion.document_id == document_id)
        .update({WikiNodeVersion.document_id: None}, synchronize_session=False)
    )
    db.delete(doc)
    db.commit()

    return {
        "document_id": document_id,
        "raw_removed": raw_removed,
        "jobs_deleted": int(n_jobs or 0),
        "versions_unlinked": int(n_ver or 0),
    }


@router.get("/ingestion/documents/recent")
async def list_recent_documents(
    user_id: AdminUserId, db: DbSession,  # noqa: ARG001
    limit: int = 50,
    hours: int = 24,
    only_open: bool = False,
) -> list[dict[str, Any]]:
    """最近的 documents 行,供前端 Sources.vue bootstrap 恢复"本次会话流水线"列表用。

    - 默认返回最近 `hours` 小时内的所有 documents,按 created_at 倒序;
    - `only_open=true` 时只返回 parse_status ∈ pending/running,适合"接管未完成任务"。
    - 不返回 jobs 详情,前端拿到 doc_id 后自行调 /progress 拉每条进度。
    """
    from datetime import datetime, timezone, timedelta

    n = max(1, min(limit, 200))
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, hours))
    q = db.query(Document).filter(Document.created_at >= since)
    if only_open:
        q = q.filter(Document.parse_status.in_(("pending", "running")))
    rows = q.order_by(Document.id.desc()).limit(n).all()
    return [
        {
            "document_id": d.id,
            "original_filename": d.original_filename,
            "parse_status": d.parse_status,
            "parse_error": d.parse_error,
            "created_at": d.created_at,
            "updated_at": d.updated_at,
        }
        for d in rows
    ]


@router.get("/ingestion/jobs/{job_id}/detail")
async def get_job_detail(
    job_id: int, user_id: AdminUserId, db: DbSession  # noqa: ARG001
) -> dict[str, Any]:
    """job 详情(含 traceback)。前端失败行"详情"按钮调用。"""
    job = db.query(IngestJob).filter(IngestJob.id == job_id).one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail=f"ingest_job {job_id} not found")
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(job.payload_json or "{}")
    except Exception:  # noqa: BLE001
        pass
    return {
        "job_id": job.id,
        "document_id": job.document_id,
        "stage": job.stage,
        "status": job.status,
        "error": job.error,
        "exception_type": payload.get("exception_type"),
        "traceback": payload.get("traceback"),
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "correlation_id": job.correlation_id,
    }
