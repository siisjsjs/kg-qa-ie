"""query SSE 评分事件 + gap_queue 入队 端到端集成测试 — P4。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.core import config as cfg_module
from app.core.db import session_scope
from app.models.gap_queue import GapQueue
from app.services.llm.base import LLMProvider
from app.services.query import agent as agent_mod


class _StaticLLM(LLMProvider):
    name = "static"

    def __init__(self, answer: str):
        super().__init__(model="static")
        self.answer = answer

    async def chat(self, messages, *, stream=False, **params) -> str | AsyncIterator[str]:
        if stream:
            async def _gen():
                yield self.answer
            return _gen()
        return self.answer

    async def chat_tools(self, messages, *, tools, tool_choice="auto", stats=None):
        yield {"type": "token", "text": self.answer}
        if stats is not None:
            stats["finish_reason"] = "stop"
        yield {"type": "finish", "finish_reason": "stop"}

    async def test_connection(self):
        return True, "ok"


@pytest.fixture
def wiki_root(tmp_path, monkeypatch):
    from app.services.wiki.init import init_wiki

    root = tmp_path / "wiki_root"
    init_wiki(root)
    (root / "wiki" / "entities" / "工效学.md").write_text(
        "---\ntitle: 工效学\nslug: 工效学\nkind: entity\n"
        "aliases:\n  - 人因工程\n"
        "confidence: EXTRACTED\n---\n\n## 简介\n研究效率与舒适。\n",
        encoding="utf-8",
    )
    (root / "wiki" / "entities" / "VSM.md").write_text(
        "---\ntitle: VSM\nslug: VSM\nkind: entity\nconfidence: EXTRACTED\n---\n## 简介\n价值流图。\n",
        encoding="utf-8",
    )
    (root / "wiki" / "entities" / "标准时间.md").write_text(
        "---\ntitle: 标准时间\nslug: 标准时间\nkind: entity\nconfidence: INFERRED\n---\n## 简介\n标时。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg_module.settings, "wiki_root", str(root))
    return root


def _parse_sse(raw: bytes) -> list[tuple[str, dict]]:
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


def test_query_sse_emits_score_event_high_quality(wiki_root, monkeypatch):
    answer = "见 [[工效学]] [[VSM]] [[标准时间]]"
    monkeypatch.setattr(agent_mod, "get_llm_provider", lambda: _StaticLLM(answer))

    from app.main import app

    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/v1/query",
            json={"question": "工效学是什么以及怎么应用?"},
        ) as resp:
            body = b"".join(resp.iter_bytes())

    events = _parse_sse(body)
    types = [e[0] for e in events]
    assert "score" in types
    score = next(e[1] for e in events if e[0] == "score")
    # 三个引用页都真实存在,未被检索到的两个由补读救回,都算有效来源。
    assert score["cited_sources"] == 3
    assert score["tier"] in ("bronze", "silver")
    assert "score" in score
    assert score["score"] > 0.5
    # confidence 分项不再因为标签不认识而全部退成中性值。
    assert score["confidence_mix"]["EXTRACTED"] == 2
    assert score["confidence_mix"]["INFERRED"] == 1
    assert score["confidence_mix"]["UNKNOWN"] == 0


def test_query_sse_low_quality_triggers_gap_queue(wiki_root, monkeypatch):
    """单引用 + AMBIGUOUS confidence + 含 hedge → gap_flag=True → 入 gap_queue。"""
    # 仅引用 1 页 + hedge phrase
    answer = "我不确定具体步骤,可能是 [[工效学]]"
    monkeypatch.setattr(agent_mod, "get_llm_provider", lambda: _StaticLLM(answer))

    from app.main import app

    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/v1/query",
            json={"question": "工效学到底是什么?"},
        ) as resp:
            body = b"".join(resp.iter_bytes())

    events = _parse_sse(body)
    score = next(e[1] for e in events if e[0] == "score")
    assert score["gap_flag"] is True
    assert score["cited_sources"] == 1

    # gap_queue 表里有一条 origin=query 的记录
    with session_scope() as s:
        rows = list(s.query(GapQueue).filter(GapQueue.origin == "query").all())
        assert len(rows) >= 1
        row = rows[-1]
        assert row.user_question == "工效学到底是什么?"


def test_query_sse_no_context_emits_score_with_gap(wiki_root, monkeypatch):
    monkeypatch.setattr(agent_mod, "get_llm_provider", lambda: _StaticLLM("不调用"))

    from app.main import app

    with TestClient(app) as client:
        with client.stream(
            "POST", "/api/v1/query", json={"question": "宇宙物理 与 量子纠缠"}
        ) as resp:
            body = b"".join(resp.iter_bytes())

    events = _parse_sse(body)
    score = next(e[1] for e in events if e[0] == "score")
    assert score["gap_flag"] is True
    assert score["cited_sources"] == 0
    assert any(reason.startswith("cited_sources<3") for reason in score["reasons"])


def test_query_sse_persisted_page_carries_quality(wiki_root, monkeypatch):
    answer = "见 [[工效学]] [[VSM]] [[标准时间]] 详细解读"
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

        resp = client.get(f"/api/v1/queries/{slug}")
        assert resp.status_code == 200
        data = resp.json()
        # quality 字段填充
        assert data["quality"]["score"] > 0
        assert data["quality"]["tier"] in ("gold", "silver", "bronze", "unknown")
