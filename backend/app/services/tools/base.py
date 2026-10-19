"""工具统一接口 + 模块级熔断器(改造计划 §5 / §5.4)。"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from loguru import logger

# 熔断触发的 HTTP 状态码(鉴权失效/欠费/限流)
BREAKER_HTTP_CODES = {401, 402, 403, 429}
BREAKER_SECONDS = 600.0


class _CircuitBreaker:
    """模块级熔断器:记录 (tool_name → until_ts),期间全局不暴露该工具。"""

    def __init__(self) -> None:
        self._until: dict[str, float] = {}

    def trip(self, tool_name: str, *, reason: str = "", seconds: float = BREAKER_SECONDS) -> None:
        self._until[tool_name] = time.monotonic() + seconds
        logger.warning(
            "tool circuit breaker tripped: tool={} reason={} cooldown={}s",
            tool_name, reason or "unknown", int(seconds),
        )

    def is_open(self, tool_name: str) -> bool:
        until = self._until.get(tool_name)
        if until is None:
            return False
        if time.monotonic() >= until:
            self._until.pop(tool_name, None)
            return False
        return True

    def clear(self, tool_name: str) -> None:
        if self._until.pop(tool_name, None) is not None:
            logger.info("tool circuit breaker cleared: tool={}", tool_name)

    def maybe_trip_for_status(self, tool_name: str, status_code: int) -> bool:
        if status_code in BREAKER_HTTP_CODES:
            self.trip(tool_name, reason=f"HTTP {status_code}")
            return True
        return False


breaker = _CircuitBreaker()

# 工具运行失败时回给模型的统一话术(§5.4 运行层)
TOOL_UNAVAILABLE_RESULT = {"error": "工具暂不可用,请基于已有材料直接作答"}


class Tool(ABC):
    """Agent 工具统一接口。

    配置必须在 available()/run() 内部现读 config_store(改造计划 §7:
    管理后台改完立即生效,禁止在实例属性里缓存 key)。
    """

    name: str = "tool"
    timeout: float = 8.0

    @abstractmethod
    def spec(self) -> dict:
        """OpenAI function schema:{"type":"function","function":{...}}"""

    @abstractmethod
    def available(self) -> bool:
        """配置齐全且未熔断。"""

    @abstractmethod
    async def run(self, args: dict, ctx: dict) -> dict:
        """执行工具,返回给模型的结果(JSON 可序列化 dict)。

        ctx 由编排器提供,常用键:
          wiki_root / alias_groups / evidence(EvidencePool) /
          events(list,工具追加 SSE 事件 {"event","data"}) /
          image_tasks(dict image_id → asyncio.Task) / images(dict) /
          image_queue(asyncio.Queue,image_ready/image_failed 事件投递)
        """
