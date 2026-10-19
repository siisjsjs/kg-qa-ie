"""query SSE 端到端集成测试(Agentic 主链路)。

外部依赖 mock:
  - LLM provider(agent 循环):chat_tools / chat 返回固定 markdown 答案
  - 文件系统:tmp_path 下手工 seed 几个 wiki 页(不走 ingestion,聚焦 query 链路)
  - 语义腿:测试环境无 chroma 索引,hybrid 自动降级纯 grep(既有行为)

事件流验证:progress(retrieval) → [tool_start → tool_result]* → token* →
            evidence_validation → citations → path → score → persist → done
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.core import config as cfg_module
from app.services.llm.base import LLMProvider, ToolCall
from app.services.query import agent as agent_mod


class _StaticLLM(LLMProvider):
    """chat_tools 直接流出固定答案(不调工具);chat 兼容 chitchat/纯流式。"""

    name = "static"

    def __init__(self, answer: str):
        super().__init__(model="static")
        self.answer = answer
        self.called = 0

    async def chat(self, messages, *, stream=False, **params) -> str | AsyncIterator[str]:
        self.called += 1
        if stream:
            tokens = list(self.answer)

            async def _gen():
                buf = ""
                for ch in tokens:
                    buf += ch
                    if len(buf) >= 5:
                        yield buf
                        buf = ""
                if buf:
                    yield buf

            return _gen()
        return self.answer

    async def chat_tools(self, messages, *, tools, tool_choice="auto", stats=None):
        self.called += 1
        # 一次吐 5 个字符,模拟流式
        buf = ""
        for ch in self.answer:
            buf += ch
            if len(buf) >= 5:
                yield {"type": "token", "text": buf}
                buf = ""
        if buf:
            yield {"type": "token", "text": buf}
        if stats is not None:
            stats["finish_reason"] = "stop"
        yield {"type": "finish", "finish_reason": "stop"}

    async def test_connection(self):
        return True, "ok"


class _ToolCallingLLM(LLMProvider):
    """第一轮调 search_kb,第二轮流出最终答案。"""

    name = "toolcall"

    def __init__(self, tool_query: str, final_answer: str):
        super().__init__(model="toolcall")
        self.tool_query = tool_query
        self.final_answer = final_answer
        self.rounds = 0

    async def chat(self, messages, *, stream=False, **params):
        if stream:
            async def _gen():
                yield self.final_answer

            return _gen()
        return self.final_answer

    async def chat_tools(self, messages, *, tools, tool_choice="auto", stats=None):
        self.rounds += 1
        if self.rounds == 1:
            yield {"type": "token", "text": "让我查一下知识库。"}
            yield {"type": "tool_call_begin"}
            yield {
                "type": "tool_calls",
                "calls": [
                    ToolCall(
                        id="call_1",
                        name="search_kb",
                        arguments=json.dumps({"query": self.tool_query}, ensure_ascii=False),
                    )
                ],
            }
            yield {"type": "finish", "finish_reason": "tool_calls"}
            return
        yield {"type": "token", "text": self.final_answer}
        yield {"type": "finish", "finish_reason": "stop"}

    async def test_connection(self):
        return True, "ok"


@pytest.fixture
def wiki_root(tmp_path, monkeypatch):
    from app.services.wiki.init import init_wiki

    root = tmp_path / "wiki_root"
    init_wiki(root)
    # 手 seed 三页:工效学(实体) / VSM(实体,有 alias) / 工效学的研究范畴(主题)
    (root / "wiki" / "entities" / "工效学.md").write_text(
        "---\ntitle: 工效学\nslug: 工效学\nkind: entity\n"
        "aliases:\n  - 人因工程\n  - ergonomics\n"
        "domains: [人因工程]\n---\n\n## 简介\n\n研究人在工作中的效率与舒适。\n",
        encoding="utf-8",
    )
    (root / "wiki" / "entities" / "VSM.md").write_text(
        "---\ntitle: VSM\nslug: VSM\nkind: entity\n"
        "aliases:\n  - 价值流图\n---\n\n## 简介\n\n价值流图分析。\n",
        encoding="utf-8",
    )
    (root / "wiki" / "topics" / "工效学的研究范畴.md").write_text(
        "---\ntitle: 工效学的研究范畴\nslug: 工效学的研究范畴\nkind: topic\n---\n\n"
        "工效学覆盖人体测量、作业测定。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg_module.settings, "wiki_root", str(root))
    return root


def _parse_sse(raw: bytes) -> list[tuple[str, dict]]:
    """从 SSE response body 解析 (event, data) 列表。"""
    events: list[tuple[str, dict]] = []
    cur_event: str | None = None
    cur_data_lines: list[str] = []
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("event:"):
            cur_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            cur_data_lines.append(line[len("data:"):].strip())
        elif line == "":
            if cur_event and cur_data_lines:
                events.append((cur_event, json.loads("\n".join(cur_data_lines))))
            cur_event = None
            cur_data_lines = []
    return events


@pytest.mark.asyncio
async def test_query_sse_end_to_end(wiki_root, monkeypatch):
    answer = (
        "工效学(也叫 [[工效学|人因工程]])研究人在工作中的效率。\n"
        "[[VSM]] 用于价值流分析。\n\n引用:\n- [[工效学]]\n- [[VSM]]\n"
    )
    fake = _StaticLLM(answer)
    monkeypatch.setattr(agent_mod, "get_llm_provider", lambda: fake)

    from app.main import app

    with TestClient(app) as client:
        with client.stream(
            "POST", "/api/v1/query", json={"question": "工效学是什么?", "domain_ie": False}
        ) as resp:
            assert resp.status_code == 200
            body = b"".join(resp.iter_bytes())

    events = _parse_sse(body)
    types = [e[0] for e in events]

    # 必备事件序列
    assert "progress" in types
    assert "token" in types
    assert "citations" in types
    assert "path" in types
    assert "persist" in types
    assert "done" in types
    assert types.index("token") < types.index("evidence_validation")
    assert types.index("citations") < types.index("path") < types.index("score")

    # retrieval 阶段带 chunk 摘要候选
    retrieval = next(
        e[1] for e in events if e[0] == "progress" and e[1].get("stage") == "retrieval"
    )
    assert retrieval["keywords"]
    assert any(c["slug"] == "工效学" for c in retrieval["candidates"])
    assert all("chunk_id" in c for c in retrieval["candidates"])

    # 页面级引用照常解析(chunk 协议兼容 [[slug]] 降级形态)
    citations_event = next(e[1] for e in events if e[0] == "citations")
    citation_slugs = [item["slug"] for item in citations_event["citations"]]
    assert "工效学" in citation_slugs
    assert "VSM" in citation_slugs
    validation_event = next(e[1] for e in events if e[0] == "evidence_validation")
    assert validation_event["invalid_citations"] == []
    assert "evidence_validation" in types

    # 这次只有 2 个 citation,应不触发持久化
    persist_event = next(e[1] for e in events if e[0] == "persist")
    assert persist_event["persisted_query_slug"] is None
    # 主回答一次 LLM(无代词不触发改写;追问建议走本地生成)
    assert fake.called == 1


@pytest.mark.asyncio
async def test_query_sse_chunk_citation_resolves_to_quote(wiki_root, monkeypatch):
    """[[slug#En]] 引用 → citations 事件带 type=chunk + quote + 行号。"""
    answer = "工效学研究效率与舒适 [[工效学#E1]]。"
    monkeypatch.setattr(agent_mod, "get_llm_provider", lambda: _StaticLLM(answer))

    from app.main import app

    with TestClient(app) as client:
        with client.stream(
            "POST", "/api/v1/query", json={"question": "工效学"}
        ) as resp:
            body = b"".join(resp.iter_bytes())

    events = _parse_sse(body)
    citations_event = next(e[1] for e in events if e[0] == "citations")
    validation_event = next(e[1] for e in events if e[0] == "evidence_validation")
    # E1 一定在证据池里(预取的第一个 chunk),要么 slug 正确 → chunk 详情,
    # 要么 slug 被纠正为真实 slug。无论哪种,详情必须带 quote 与行号。
    assert citations_event["citations"], validation_event
    detail = citations_event["citations"][0]
    assert detail["type"] == "chunk"
    assert detail["ref"] == "E1"
    assert detail["quote"]
    assert detail["line_start"] is not None


@pytest.mark.asyncio
async def test_query_sse_agent_tool_round_emits_tool_events(wiki_root, monkeypatch):
    """模型第一轮调 search_kb → tool_start/tool_result → 第二轮作答。"""
    fake = _ToolCallingLLM(tool_query="价值流图", final_answer="VSM 即价值流图 [[VSM]]。")
    monkeypatch.setattr(agent_mod, "get_llm_provider", lambda: fake)

    from app.main import app

    with TestClient(app) as client:
        with client.stream(
            "POST", "/api/v1/query", json={"question": "帮我全面讲讲VSM相关内容和应用场景"}
        ) as resp:
            body = b"".join(resp.iter_bytes())

    events = _parse_sse(body)
    types = [e[0] for e in events]
    assert "tool_start" in types
    assert "tool_result" in types
    assert types.index("tool_start") < types.index("tool_result")

    tool_start = next(e[1] for e in events if e[0] == "tool_start")
    assert tool_start["tool"] == "search_kb"
    assert tool_start["args"]["query"] == "价值流图"
    assert "查一下知识库" in tool_start["reason"]

    tool_result = next(e[1] for e in events if e[0] == "tool_result")
    assert tool_result["ok"] is True

    # 第二轮的最终答案正常流出且引用有效
    streamed = "".join(e[1].get("text", "") for e in events if e[0] == "token")
    assert "价值流图" in streamed
    citations_event = next(e[1] for e in events if e[0] == "citations")
    assert any(c["slug"] == "VSM" for c in citations_event["citations"])
    assert fake.rounds == 2


@pytest.mark.asyncio
async def test_query_sse_chitchat_fast_path(wiki_root, monkeypatch):
    """正则快路径:闲聊不检索、不调 agent,直接 chitchat 流式。"""
    from app.services.query import chitchat as chitchat_mod

    monkeypatch.setattr(chitchat_mod, "get_llm_provider", lambda: _StaticLLM("你好呀,我是小 IE!"))

    from app.main import app

    with TestClient(app) as client:
        with client.stream("POST", "/api/v1/query", json={"question": "你好"}) as resp:
            body = b"".join(resp.iter_bytes())

    events = _parse_sse(body)
    stages = [e[1].get("stage") for e in events if e[0] == "progress"]
    assert "route" in stages
    assert "compose_chitchat" in stages
    assert "retrieval" not in stages
    citations_event = next(e[1] for e in events if e[0] == "citations")
    assert citations_event["kind"] == "chitchat"
    assert citations_event["citations"] == []


@pytest.mark.asyncio
async def test_query_sse_persists_when_3plus_citations(wiki_root, monkeypatch):
    answer = "见 [[工效学]]、[[VSM]]、[[工效学的研究范畴]]。"
    monkeypatch.setattr(agent_mod, "get_llm_provider", lambda: _StaticLLM(answer))

    from app.main import app

    with TestClient(app) as client:
        with client.stream(
            "POST", "/api/v1/query", json={"question": "工效学有哪些?", "persist": True}
        ) as resp:
            body = b"".join(resp.iter_bytes())

    events = _parse_sse(body)
    persist_event = next(e[1] for e in events if e[0] == "persist")
    assert persist_event["persisted_query_slug"] is not None
    # 落盘检查
    queries_dir = wiki_root / "wiki" / "queries"
    assert any(p.suffix == ".md" for p in queries_dir.iterdir())


@pytest.mark.asyncio
async def test_query_sse_invalid_citation_cleaned_and_replaced(wiki_root, monkeypatch):
    """编造的引用被清洗:发 replace_answer,validation 记录 invalid。"""
    answer = "真论断 [[工效学]]。编造论断 [[不存在的页面]]。"
    monkeypatch.setattr(agent_mod, "get_llm_provider", lambda: _StaticLLM(answer))

    from app.main import app

    with TestClient(app) as client:
        with client.stream(
            "POST", "/api/v1/query", json={"question": "工效学是什么?"}
        ) as resp:
            body = b"".join(resp.iter_bytes())

    events = _parse_sse(body)
    validation_event = next(e[1] for e in events if e[0] == "evidence_validation")
    assert "不存在的页面" in validation_event["invalid_citations"]
    replaced = [e[1]["text"] for e in events if e[0] == "replace_answer"]
    assert replaced, "清洗后应发 replace_answer"
    assert "[[不存在的页面]]" not in replaced[-1]
    assert "[[工效学]]" in replaced[-1]


@pytest.mark.asyncio
async def test_get_query_page_404(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/queries/不存在")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_query_page_returns_persisted_content(wiki_root, monkeypatch):
    answer = "答案 [[工效学]] [[VSM]] [[工效学的研究范畴]]"
    monkeypatch.setattr(agent_mod, "get_llm_provider", lambda: _StaticLLM(answer))

    from app.main import app

    with TestClient(app) as client:
        with client.stream(
            "POST", "/api/v1/query", json={"question": "工效学概览", "persist": True}
        ) as resp:
            body = b"".join(resp.iter_bytes())
        events = _parse_sse(body)
        slug = next(e[1] for e in events if e[0] == "persist")["persisted_query_slug"]
        assert slug

        # GET 拿回去
        resp = client.get(f"/api/v1/queries/{slug}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["persisted_query_slug"] == slug
        assert data["citations"]
        assert "工效学" in [c["slug"] for c in data["citations"]]
