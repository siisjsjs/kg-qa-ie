"""web_search 工具:Tavily 联网搜索(改造计划 §5.2,httpx 直调不装 SDK)。"""

from __future__ import annotations

import httpx

from app.core.exceptions import ProviderError
from app.services.admin import config_store
from app.services.tools.base import Tool, breaker

TAVILY_URL = "https://api.tavily.com/search"


class WebSearchTool(Tool):
    name = "web_search"
    timeout = 8.0

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "联网搜索最新公开信息。仅当问题涉及时效性内容(最新趋势/新标准/近年数据)"
                    "或知识库明显未覆盖时调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索查询词(可中可英)"},
                    },
                    "required": ["query"],
                },
            },
        }

    def available(self) -> bool:
        if breaker.is_open(self.name):
            return False
        return bool((config_store.get_sync("tavily.api_key") or "").strip())

    async def run(self, args: dict, ctx: dict) -> dict:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query 不能为空"}
        api_key = (config_store.get_sync("tavily.api_key") or "").strip()
        if not api_key:
            return {"error": "联网搜索未配置"}
        payload = {
            "api_key": api_key,
            "query": query,
            "max_results": 5,
            "search_depth": "basic",
            "include_answer": False,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout)) as client:
            resp = await client.post(TAVILY_URL, json=payload)
        if resp.status_code >= 400:
            breaker.maybe_trip_for_status(self.name, resp.status_code)
            raise ProviderError(f"Tavily HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        results = [
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "content": str(item.get("content") or "")[:600],
            }
            for item in (data.get("results") or [])
            if item.get("url")
        ]
        if not results:
            return {"result": "联网搜索无结果。"}
        evidence = ctx["evidence"]
        added = evidence.add_web_results(results)
        # 给前端的网络来源事件数据(引用解析也从 evidence pool 走)
        ctx.setdefault("events", []).append(
            {
                "event": "web_citations",
                "data": {
                    "results": [
                        {"ref": it["ref"], "title": it["title"], "url": it["url"]}
                        for it in added
                    ]
                },
            }
        )
        return {
            "result": (
                f"联网搜索到 {len(added)} 条结果,编号如下。"
                "引用时在句末写 [[web#Wn]]:\n\n"
                + evidence.render_for_prompt(items=added)
            ),
        }
