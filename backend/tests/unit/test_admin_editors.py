"""schema_editor / purpose_editor 单元测试。"""

from __future__ import annotations

import pytest

from app.services.admin import purpose_editor as purpose_mod
from app.services.admin import schema_editor as schema_mod
from app.services.admin.purpose_editor import read_purpose, render_purpose, write_purpose
from app.services.admin.schema_editor import read_schema, render_schema, write_schema


# ---------------------------------------------------------------------------
# schema_editor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_schema_parses_aliases_and_relations(tmp_path):
    (tmp_path / ".wiki-schema.md").write_text(
        "---\ntitle: schema\n---\n\n"
        "## 主题\n工业工程\n\n"
        "## 别名词表\n\n- 工效学 = 人因工程 = ergonomics\n- VSM = 价值流图\n\n"
        "## 关系类型词汇表\n\n- 实现:A 是 B 的方法\n- 依赖:A 需要 B\n\n"
        "## 成长配置\n\n```yaml\nhot_topic_threshold: 5\nauto_merge_cosine_threshold: 0.92\n```\n",
        encoding="utf-8",
    )
    data = await read_schema(tmp_path)
    aliases = {a["canonical"]: a["members"] for a in data["aliases"]}
    assert aliases["工效学"] == ["工效学", "人因工程", "ergonomics"]
    assert aliases["VSM"] == ["VSM", "价值流图"]

    rels = {r["name"]: r["description"] for r in data["relation_types"]}
    assert rels["实现"] == "A 是 B 的方法"
    assert rels["依赖"] == "A 需要 B"

    assert data["growth_config"]["hot_topic_threshold"] == 5
    assert data["growth_config"]["auto_merge_cosine_threshold"] == 0.92


@pytest.mark.asyncio
async def test_read_schema_missing_returns_empty(tmp_path):
    out = await read_schema(tmp_path)
    assert out["raw_markdown"] == ""
    assert out["aliases"] == []
    assert out["relation_types"] == []
    assert out["growth_config"] == {}


def test_render_schema_uses_raw_when_provided():
    out = render_schema({"raw_markdown": "## H\n\nbody"})
    assert out == "## H\n\nbody"


def test_render_schema_from_structured_payload():
    out = render_schema({
        "aliases": [{"canonical": "工效学", "members": ["工效学", "人因工程"]}],
        "relation_types": [{"name": "实现", "description": "A 是 B 的方法"}],
        "growth_config": {"hot_topic_threshold": 3},
    })
    assert "## 别名词表" in out
    assert "- 工效学 = 人因工程" in out
    assert "## 关系类型词汇表" in out
    assert "- 实现:A 是 B 的方法" in out
    assert "## 成长配置" in out
    assert "hot_topic_threshold: 3" in out


@pytest.mark.asyncio
async def test_write_schema_round_trips(tmp_path):
    payload = {
        "aliases": [{"canonical": "VSM", "members": ["VSM", "价值流图"]}],
        "relation_types": [{"name": "对比", "description": "A 与 B 同层"}],
        "growth_config": {"merge_threshold_auto": 0.92},
    }
    await write_schema(wiki_root=tmp_path, payload=payload)
    data = await read_schema(tmp_path)
    aliases = {a["canonical"]: a["members"] for a in data["aliases"]}
    assert aliases["VSM"] == ["VSM", "价值流图"]
    assert data["growth_config"]["merge_threshold_auto"] == 0.92


# ---------------------------------------------------------------------------
# purpose_editor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_purpose_parses_sections(tmp_path):
    (tmp_path / "purpose.md").write_text(
        "---\ntitle: 研究目的\n---\n\n"
        "# 研究目标\n\n建立 IE 知识库\n\n"
        "# 关键问题\n\n1. 工效学是什么\n2. VSM 怎么实施\n\n"
        "# 研究范围\n\n## 在范围内\n\n- 作业测定\n- VSM\n\n"
        "## 暂不深入\n\n- 财务\n",
        encoding="utf-8",
    )
    data = await read_purpose(tmp_path)
    assert "建立 IE 知识库" in data["research_goals"]
    assert data["key_questions"] == ["工效学是什么", "VSM 怎么实施"]
    assert data["in_scope"] == ["作业测定", "VSM"]
    assert data["out_of_scope"] == ["财务"]


@pytest.mark.asyncio
async def test_write_then_read_purpose_round_trips(tmp_path):
    payload = {
        "research_goals": ["目标 A", "目标 B"],
        "key_questions": ["Q1", "Q2"],
        "in_scope": ["作业测定", "VSM"],
        "out_of_scope": ["财务"],
    }
    await write_purpose(wiki_root=tmp_path, payload=payload)
    data = await read_purpose(tmp_path)
    assert data["key_questions"] == ["Q1", "Q2"]
    assert data["in_scope"] == ["作业测定", "VSM"]
    assert data["out_of_scope"] == ["财务"]


@pytest.mark.asyncio
async def test_read_purpose_missing_file(tmp_path):
    out = await read_purpose(tmp_path)
    assert out["raw_markdown"] == ""
    assert out["research_goals"] == []


def test_render_purpose_uses_raw_when_present():
    out = render_purpose({"raw_markdown": "# Direct\n\nbody"})
    assert out == "# Direct\n\nbody"
