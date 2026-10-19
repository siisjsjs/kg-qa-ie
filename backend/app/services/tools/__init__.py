"""Agent 工具箱(改造计划 §5)。

统一接口 Tool + 三层降级防护:
  1. 配置层:available() 检查配置,key 未配的工具不进 tools 列表
  2. 运行层:run() 异常/超时 → 错误文本作为 tool 结果,流程继续
  3. 熔断层:HTTP 401/402/403/429 → 10 分钟内全局不暴露该工具
"""

from app.services.tools.base import Tool, breaker
from app.services.tools.registry import build_tools

__all__ = ["Tool", "breaker", "build_tools"]
