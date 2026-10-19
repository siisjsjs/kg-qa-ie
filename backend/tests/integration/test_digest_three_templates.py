"""digest 三模板端到端集成测试 — P4。

外部依赖 mock:
  - LLM provider:返回固定 markdown(deep_dive / comparison / timeline 三种)
  - 文件系统:tmp_path 下手 seed 几个 wiki 页(不走 ingestion)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.core import config as cfg_module
from app.services.digest import builder as builder_mod
from app.services.llm.base import ChatMessage, LLMProvider


class _StaticLLM(LLMProvider):
    name = "static"

    def __init__(self, answer: str):
        super().__init__(model="static")
        self.answer = answer

    async def chat(self, messages, *, stream=False, **params) -> str | AsyncIterator[str]:
        return self.answer

    async def test_connection(self):
        return True, "ok"


@pytest.fixture
def wiki_seed(tmp_path, monkeypatch):
    from app.services.wiki.init import init_wiki

    root = tmp_path / "wiki_root"
    init_wiki(root)
    (root / "wiki" / "entities" / "精益生产.md").write_text(
        "---\ntitle: 精益生产\nslug: 精益生产\nkind: entity\n"
        "aliases: [lean, TPS]\nsources: [s1]\n---\n\n## 简介\n精益主张消除浪费。\n",
        encoding="utf-8",
    )
    (root / "wiki" / "entities" / "六西格玛.md").write_text(
        "---\ntitle: 六西格玛\nslug: 六西格玛\nkind: entity\n"
        "aliases: [6sigma, Six Sigma]\nsources: [s2]\n---\n\n## 简介\nDMAIC 五步。\n",
        encoding="utf-8",
    )
    (root / "wiki" / "entities" / "VSM.md").write_text(
        "---\ntitle: VSM\nslug: VSM\nkind: entity\nsources: [s1]\n---\n\n## 简介\n价值流图。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg_module.settings, "wiki_root", str(root))
    return root


def _set_llm(monkeypatch, answer: str):
    monkeypatch.setattr(builder_mod, "get_llm_provider", lambda: _StaticLLM(answer))


def test_digest_deep_dive_creates_synthesis_page(wiki_seed, monkeypatch):
    md = (
        "## 背景概述\n\n精益生产的全面背景介绍,内容超过二十个字符以确保非占位段。\n\n"
        "## 核心观点\n\n核心观点详细描述,见 [[精益生产]] 与 [[VSM]],并展开多角度解读。\n\n"
        "## 不同视角对比\n\n精益与 [[六西格玛]] 形成对比,各有侧重点与适用场景说明。\n\n"
        "## 知识脉络\n\n知识脉络的铺陈和发展,涉及多个概念与跨方法关系的讨论。\n\n"
        "## 尚待解决的问题\n\n- 问题 1:数字化背景下精益还适用吗?\n- 问题 2:VSM 软件工具选型?\n\n"
        "## 相关页面\n- [[精益生产]]\n- [[VSM]]\n"
    )
    _set_llm(monkeypatch, md)
    from app.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/digest",
            json={"topic": "精益生产深度分析"},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "deep_dive"
    assert data["gap_flag"] is False
    assert "精益生产" in data["citations"]
    assert any("背景概述" in s["heading"] for s in data["sections"])
    # 落盘检查
    files = list((wiki_seed / "wiki" / "synthesis").glob("*.md"))
    assert files, "no synthesis md created"


def test_digest_deep_dive_missing_sections_triggers_gap(wiki_seed, monkeypatch):
    md = "## 背景概述\n内容\n\n## 核心观点\n内容\n"  # 缺 3 段
    _set_llm(monkeypatch, md)
    from app.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/digest",
            json={"topic": "精益生产", "mode": "deep_dive"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["gap_flag"] is True
    assert "不同视角对比" in data["sections_missing"]


def test_digest_comparison_creates_table_and_empty_cells_gap(wiki_seed, monkeypatch):
    md = (
        "## 对比对象\n精益生产 vs 六西格玛\n\n"
        "## 对比表\n"
        "| 维度 | 精益生产 | 六西格玛 |\n"
        "|------|---------|---------|\n"
        "| 核心观点 | 消除浪费 | 减少缺陷 |\n"
        "| 适用场景 |  | 制造质量管控 |\n"
        "| 量化指标 | 流程周期 |  |\n"
        "| 实施成本 | 低 | 高 |\n"
        "| 典型陷阱 |  |  |\n"
        "| 来源素材 | [[精益生产]] | [[六西格玛]] |\n\n"
        "## 关键差异\n\n精益偏流程,六西偏质量。\n"
    )
    _set_llm(monkeypatch, md)
    from app.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/digest",
            json={
                "topic": "对比 精益生产 和 六西格玛",
                "objects": ["精益生产", "六西格玛"],
            },
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "comparison"
    # 4 个空格 → fill_rate < 0.7 → gap_flag
    assert data["fill_rate"] == pytest.approx(8 / 12, abs=1e-3)
    assert data["gap_flag"] is True
    files = list((wiki_seed / "wiki" / "comparisons").glob("*.md"))
    assert files


def test_digest_timeline_creates_events(wiki_seed, monkeypatch):
    md = (
        "## 时间线(工业工程演进)\n\n"
        "## 事件说明\n\n"
        "- 1947 工业工程协会成立:标志性起点(来源:[[精益生产]])\n"
        "- 1990 精益生产概念提出:从丰田推广\n"
        "- 2000 六西格玛黑带认证流行(来源:[[六西格玛]])\n"
        "- 2010 数字精益化兴起\n\n"
        "## 相关页面\n- [[精益生产]]\n- [[六西格玛]]\n"
    )
    _set_llm(monkeypatch, md)
    from app.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/digest",
            json={"topic": "工业工程发展史"},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "timeline"
    assert data["gap_flag"] is False


def test_digest_timeline_few_events_triggers_gap(wiki_seed, monkeypatch):
    md = "## 时间线\n\n## 事件说明\n\n- 1990 一个事件\n"
    _set_llm(monkeypatch, md)
    from app.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/digest",
            json={"topic": "时间线 精益"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "timeline"
    assert data["gap_flag"] is True


def test_digest_get_synthesis_404(wiki_seed):
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/synthesis/不存在")
        assert resp.status_code == 404


def test_digest_get_after_create(wiki_seed, monkeypatch):
    md = (
        "## 背景概述\n背景。涵盖多种基础信息内容。\n\n"
        "## 核心观点\n核心。涵盖多种核心要点。\n\n"
        "## 不同视角对比\n各方。\n\n## 知识脉络\n脉络。\n\n"
        "## 尚待解决的问题\n- 问 1\n"
    )
    _set_llm(monkeypatch, md)
    from app.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/digest", json={"topic": "深度分析 测试主题"}
        )
        slug = resp.json()["slug"]
        get_resp = client.get(f"/api/v1/synthesis/{slug}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["mode"] == "deep_dive"
        assert data["slug"] == slug


def test_digest_comparison_writes_per_cell_gap_queue(wiki_seed, monkeypatch):
    """空格逐格入 gap_queue(origin=comparison_cell)。"""
    md = (
        "## 对比表\n"
        "| 维度 | A | B |\n|------|---|---|\n"
        "| 核心 |  |  |\n"
        "| 适用 |  |  |\n"
    )
    _set_llm(monkeypatch, md)

    from app.main import app
    from app.models.gap_queue import GapQueue
    from app.core.db import session_scope

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/digest",
            json={
                "topic": "对比 A 和 B",
                "objects": ["A", "B"],
            },
        )
    assert resp.status_code == 200
    with session_scope() as s:
        rows = list(
            s.query(GapQueue).filter(GapQueue.origin == "comparison_cell").all()
        )
    assert len(rows) == 4  # 2x2 全空
