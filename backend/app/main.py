"""FastAPI 应用入口。

挂载所有 v1 路由、CORS、全局异常处理、启动/关闭事件。
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1 import (
    admin,
    announcements,
    auth,
    conversations,
    crystallize,
    digest,
    feedback,
    files,
    followups,
    graph,
    ingestion,
    lint,
    qa_data,
    query,
    scraper,
    status as status_router,
    trend,
    wiki,
)
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化日志 + 数据库迁移 + 收编孤儿任务,关闭时释放资源。"""
    configure_logging()
    _run_db_migrations()
    _reap_orphaned_running_jobs()
    _seed_default_announcement()
    try:
        from app.services.query.grep import retrievable_slugs

        await asyncio.to_thread(retrievable_slugs, settings.wiki_root_path)
    except Exception:  # noqa: BLE001
        pass
    yield


def _run_db_migrations() -> None:
    """启动时自动 alembic upgrade head;失败仅 warning,不阻塞启动(便于开发迭代)。

    生产容器场景:第一次启动 DB 是空的,这里把 schema 拉到 head;
    后续启动如果已经在 head,upgrade 是 no-op,不影响。
    """
    from loguru import logger

    try:
        from alembic import command
        from alembic.config import Config

        alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
        if not alembic_ini.exists():
            logger.warning(f"[startup] alembic.ini not found at {alembic_ini}, skip migrations")
            return
        cfg = Config(str(alembic_ini))
        command.upgrade(cfg, "head")
        logger.info("[startup] alembic upgrade head: ok")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[startup] alembic upgrade skipped: {e}")


_DEFAULT_ANNOUNCEMENT_TITLE = "KG-Wiki 测试阶段公告"
_DEFAULT_ANNOUNCEMENT_BODY = """## 欢迎使用 KG-Wiki 工业工程知识库

目前系统处于 **测试阶段**,完全 **免费使用**。

我们正在持续打磨问答质量、知识图谱和素材消化流水线。
使用过程中如果遇到问题,或者有任何建议,欢迎从 **主页底部的"用户反馈"** 提交 —— 我们会逐条处理。

感谢你帮我们一起把这个工具做得更好。
"""


def _seed_default_announcement() -> None:
    """启动时插入一条默认公告(按 title 幂等,已存在则跳过)。"""
    from loguru import logger

    from app.core.db import session_scope
    from app.models.announcement import Announcement
    from app.models.user import User

    try:
        with session_scope() as db:
            existing = (
                db.query(Announcement)
                .filter(Announcement.title == _DEFAULT_ANNOUNCEMENT_TITLE)
                .one_or_none()
            )
            if existing is not None:
                return
            admin = (
                db.query(User)
                .filter(User.role == "admin")
                .order_by(User.id)
                .first()
            )
            row = Announcement(
                title=_DEFAULT_ANNOUNCEMENT_TITLE,
                body_md=_DEFAULT_ANNOUNCEMENT_BODY,
                cover_image_url=None,
                pinned=True,
                visibility="published",
                created_by=admin.id if admin else None,
            )
            db.add(row)
            db.commit()
            logger.info(f"[startup] seeded default announcement id={row.id}")
    except Exception as e:  # noqa: BLE001
        from loguru import logger as _l
        _l.warning(f"[startup] seed_default_announcement skipped: {e}")


def _reap_orphaned_running_jobs() -> None:
    """启动时把所有 status=running 的 documents / ingest_jobs 标 failed。

    BackgroundTasks 是 in-process 的:进程被杀(重启 / 崩溃 / Ctrl-C)后任务就没了,
    但 DB 行不会回滚到 failed,导致前端轮询永远看到 running。这里在新进程拉起的
    第一时间把这些孤儿状态收编,避免"卡在页面生成"假象。
    """
    from loguru import logger

    from app.core.db import session_scope
    from app.models.document import Document
    from app.models.ingest_job import IngestJob

    try:
        with session_scope() as db:
            # ingest_jobs 行
            jobs = db.query(IngestJob).filter(IngestJob.status == "running").all()
            for j in jobs:
                j.status = "failed"
                j.error = "server_restart_orphan: 上次后台任务在进程结束时被中断"
            # documents 行
            docs = db.query(Document).filter(Document.parse_status == "running").all()
            for d in docs:
                d.parse_status = "failed"
                d.parse_error = "server_restart_orphan: 上次后台任务在进程结束时被中断"
            if jobs or docs:
                logger.warning(
                    f"[startup] 收编孤儿任务: jobs={len(jobs)} docs={len(docs)} (server_restart_orphan)"
                )
    except Exception as e:  # noqa: BLE001
        # 数据库未就绪时不要阻塞启动 — 仅打 warning
        from loguru import logger as _l
        _l.warning(f"[startup] reap_orphaned_running_jobs skipped: {e}")


def create_app() -> FastAPI:
    app = FastAPI(
        title="KG-QA-IE",
        description="工业工程知识库问答系统（基于 llm-wiki 方法论）",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    # 静态文件挂载（公告图片等用户上传内容）
    uploads_dir = Path(settings.cache_dir).resolve().parent / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/static/uploads",
        StaticFiles(directory=str(uploads_dir)),
        name="uploads",
    )

    @app.get("/health", tags=["meta"])
    async def health():
        """健康检查：仅确认进程存活，不检查 DB / Neo4j / Chroma 连通性。"""
        return {"status": "ok", "service": "kg-qa-ie", "version": app.version}

    # API v1 路由挂载
    api_v1_prefix = "/api/v1"
    app.include_router(auth.router, prefix=api_v1_prefix, tags=["auth"])
    app.include_router(ingestion.router, prefix=api_v1_prefix, tags=["ingestion"])
    app.include_router(query.router, prefix=api_v1_prefix, tags=["query"])
    app.include_router(conversations.router, prefix=api_v1_prefix, tags=["conversations"])
    app.include_router(digest.router, prefix=api_v1_prefix, tags=["digest"])
    app.include_router(graph.router, prefix=api_v1_prefix, tags=["graph"])
    app.include_router(lint.router, prefix=api_v1_prefix, tags=["lint"])
    app.include_router(status_router.router, prefix=api_v1_prefix, tags=["status"])
    app.include_router(trend.router, prefix=api_v1_prefix, tags=["trend"])
    app.include_router(followups.router, prefix=api_v1_prefix, tags=["followups"])
    app.include_router(crystallize.router, prefix=api_v1_prefix, tags=["crystallize"])
    app.include_router(wiki.router, prefix=api_v1_prefix, tags=["wiki"])
    app.include_router(scraper.router, prefix=api_v1_prefix, tags=["scraper"])
    app.include_router(admin.router, prefix=api_v1_prefix, tags=["admin"])
    app.include_router(announcements.router, prefix=api_v1_prefix, tags=["announcements"])
    app.include_router(feedback.router, prefix=api_v1_prefix, tags=["feedback"])
    app.include_router(qa_data.router, prefix=api_v1_prefix, tags=["qa-data"])
    app.include_router(files.router, prefix=api_v1_prefix, tags=["files"])

    return app


app = create_app()
