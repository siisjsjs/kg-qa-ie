"""工具注册表:每次提问时现建列表(配置层降级在这里生效)。"""

from __future__ import annotations

from app.services.tools.base import Tool
from app.services.tools.chart_generate import GenerateChartTool
from app.services.tools.chart_templates import RecommendChartTemplatesTool
from app.services.tools.imagegen import GenerateImageTool
from app.services.tools.search_kb import SearchKBTool
from app.services.tools.web_search import WebSearchTool

_ALL_TOOLS: list[Tool] = [
    SearchKBTool(),
    WebSearchTool(),
    GenerateImageTool(),
    RecommendChartTemplatesTool(),
    GenerateChartTool(),
]


def build_tools(*, exclude: set[str] | None = None) -> list[Tool]:
    """返回当前可用工具(配置齐全且未熔断)。

    available() 内部现读 config_store:后台保存 key → 下一条提问立刻可用;
    清空 key → 下一条提问立刻从列表消失。
    exclude:本轮硬分流摘掉的工具名(如问题点名专业模板时去掉 generate_image)。
    """
    blocked = exclude or set()
    return [tool for tool in _ALL_TOOLS if tool.available() and tool.name not in blocked]
