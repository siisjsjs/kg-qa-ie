"""search_kb 工具:chunk 级混合检索(改造计划 §5.1)。

必须走别名扩展(用户硬性要求,与投机预取同一条路径):
query → alias_expand.expand → hybrid_search → 新证据并入 EvidencePool。
"""

from __future__ import annotations

from pathlib import Path

from app.services.query.alias_expand import expand as expand_aliases
from app.services.query.hybrid import hybrid_search
from app.services.tools.base import Tool


class SearchKBTool(Tool):
    name = "search_kb"
    timeout = 5.0

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "检索内部 IE 知识库(wiki)。当已有证据不足以回答问题的某个方面时调用,"
                    "query 用具体的中文关键词或短语(如「帕累托图 实施步骤」)。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索查询词"},
                    },
                    "required": ["query"],
                },
            },
        }

    def available(self) -> bool:
        # 本地检索无外部依赖,恒可用(熔断不适用)
        return True

    async def run(self, args: dict, ctx: dict) -> dict:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query 不能为空"}
        wiki_root = Path(ctx["wiki_root"])
        alias_groups = ctx.get("alias_groups") or []
        keywords, _ = expand_aliases(query, alias_groups=alias_groups)
        chunks = await hybrid_search(
            question=query,
            keywords=keywords,
            wiki_root=wiki_root,
            top_k=8,
            candidate_k=20,
        )
        evidence = ctx["evidence"]
        added = evidence.add_chunks(chunks)
        if not added:
            return {"result": "未检索到新的知识库素材(可能已在现有证据中)。"}
        return {
            "result": (
                f"检索到 {len(added)} 条新证据,编号接续如下。"
                "引用时在句末写 [[slug#En]]:\n\n"
                + evidence.render_for_prompt(items=added)
            ),
        }
