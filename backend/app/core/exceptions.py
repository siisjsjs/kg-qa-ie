"""统一异常类型与 FastAPI 异常处理器。"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger


class AppError(Exception):
    """业务错误基类。"""

    status_code: int = 400
    code: str = "APP_ERROR"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "PERMISSION_DENIED"


class ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"


class ProviderError(AppError):
    status_code = 502
    code = "PROVIDER_ERROR"


class IngestQualityError(AppError):
    """抽取流水线的某一步达不到「最佳质量」要求。

    设计原则(2026-06-11):流水线不再静默降级/回退/产占位页。任一环节(实体融合、
    实体/主题成稿、embedding 判重、slug 对齐)拿不到最佳结果时,抛本异常 → 整篇文档
    job 失败,且失败前不写任何磁盘/DB,杜绝半成品污染数据。
    """

    status_code = 502
    code = "INGEST_QUALITY_ERROR"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError):
        logger.warning(f"AppError [{exc.code}]: {exc.message}")
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(NotImplementedError)
    async def _handle_not_implemented(_: Request, exc: NotImplementedError):
        return JSONResponse(
            status_code=501,
            content={"code": "NOT_IMPLEMENTED", "message": str(exc) or "endpoint not implemented yet"},
        )
