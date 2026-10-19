"""日志：loguru 结构化日志，统一格式。"""

import sys

from loguru import logger


def configure_logging() -> None:
    """配置 loguru：移除默认 handler，安装统一格式。

    在 main.py 的 lifespan 启动事件中调用一次。
    """
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        backtrace=True,
        diagnose=False,
    )
