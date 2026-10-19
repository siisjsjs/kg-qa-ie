"""step1_extract / step2_compose / merger 的单元测试。

LLM 与 embedding 通过 monkeypatch 替换 provider 实例,
不真打外部 API。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from app.core.exceptions import ValidationError
from app.services.ingestion import merger as merger_mod
from app.services.ingestion import step1_extract as step1_mod
from app.services.ingestion import step2_compose as step2_mod
from app.services.ingestion.merger import (
    apply_observation_block,
    cosine,
    find_alias_match,
    load_alias_groups,
    merge_or_create_entity,
    parse_alias_groups_from_schema,
)
from app.services.ingestion.step2_compose import parse_files
from app.services.llm.base import ChatMessage, LLMProvider


class _FakeLLM(LLMProvider):
    """用一个或多个固定响应配置的 LLM stub。"""

    name = "fake"

    def __init__(self, responses: list[str], *, capture: list | None = None):
        super().__init__(model="fake-model")
        self._responses = list(responses)
        self._capture = capture if capture is not None else []

    async def chat(self, messages, *, stream=False, **params) -> str | AsyncIterator[str]:
        self._capture.append([m.content for m in messages])
        if not self._responses:
            return ""
        out = self._responses.pop(0)
        if stream:
            async def _gen():
                yield out
            return _gen()
        return out

    async def test_connection(self) -> tuple[bool, str]:
        return True, "ok"


def _good_step1() -> dict:
    return {
        "source_summary": {
            "title": "测试素材",
            "kind": "note",
            "domain_tags": ["人因工程"],
            "key_takeaways": ["k1", "k2", "k3"],
        },
        "entities": [
            {
                "name": "工效学",
                "kind": "concept",
                "brief": "研究人在工作中的效率。",
                "aliases": ["人因工程"],
                "evidence": "原文",
                "confidence": "EXTRACTED",
                "domain_tags": ["人因工程"],
            }
        ],
        "topics": [],
        "connections": [],
        "contradictions": [],
        "new_vs_existing": {
            "new_entity_names": ["工效学"],
            "matched_entity_pages": [],
            "new_topic_names": [],
            "matched_topic_pages": [],
        },
    }


# ---------------------------------------------------------------------------
# step1_extract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step1_happy_path(monkeypatch):
    fake = _FakeLLM([json.dumps(_good_step1(), ensure_ascii=False)])
    monkeypatch.setattr(step1_mod, "get_llm_provider", lambda: fake)

    out = await step1_mod.step1_extract(
        raw_md="一段关于工效学的笔记",
        purpose_text="IE 知识库",
        index_summary="(空)",
        wiki_structure_summary="(空)",
    )
    assert out["source_summary"]["title"] == "测试素材"
    assert out["entities"][0]["name"] == "工效学"


@pytest.mark.asyncio
async def test_step1_extracts_json_from_fenced_code(monkeypatch):
    fenced = "```json\n" + json.dumps(_good_step1(), ensure_ascii=False) + "\n```"
    monkeypatch.setattr(step1_mod, "get_llm_provider", lambda: _FakeLLM([fenced]))
    out = await step1_mod.step1_extract(
        raw_md="x", purpose_text="", index_summary="", wiki_structure_summary=""
    )
    assert out["source_summary"]["title"] == "测试素材"


@pytest.mark.asyncio
async def test_step1_retries_once_on_invalid_then_succeeds(monkeypatch):
    bad = '{"source_summary": "missing object"}'
    good = json.dumps(_good_step1(), ensure_ascii=False)
    captured: list = []
    monkeypatch.setattr(
        step1_mod, "get_llm_provider", lambda: _FakeLLM([bad, good], capture=captured)
    )

    out = await step1_mod.step1_extract(
        raw_md="x", purpose_text="", index_summary="", wiki_structure_summary=""
    )
    assert out["source_summary"]["title"] == "测试素材"
    # 第二次的 prompt 应包含校验失败提醒
    assert "校验失败提醒" in captured[1][1]


@pytest.mark.asyncio
async def test_step1_raises_after_two_failures(monkeypatch):
    monkeypatch.setattr(
        step1_mod, "get_llm_provider", lambda: _FakeLLM(["not json", "still not json"])
    )
    with pytest.raises(ValidationError):
        await step1_mod.step1_extract(
            raw_md="x", purpose_text="", index_summary="", wiki_structure_summary=""
        )


@pytest.mark.asyncio
async def test_step1_empty_optional_arrays_are_normalized(monkeypatch):
    """LLM 不返回 entities/topics 数组时,normalize_step1 自动补空。"""
    minimal = {
        "source_summary": _good_step1()["source_summary"],
        "new_vs_existing": _good_step1()["new_vs_existing"],
    }
    monkeypatch.setattr(
        step1_mod,
        "get_llm_provider",
        lambda: _FakeLLM([json.dumps(minimal, ensure_ascii=False)]),
    )
    out = await step1_mod.step1_extract(
        raw_md="x", purpose_text="", index_summary="", wiki_structure_summary=""
    )
    assert out["entities"] == []
    assert out["topics"] == []


# ---------------------------------------------------------------------------
# step2_compose / parse_files
# ---------------------------------------------------------------------------


def test_parse_files_simple():
    text = """=====<file: wiki/sources/2026-05-19-x.md>=====

---
title: x
slug: 2026-05-19-x
kind: source
---

正文 source.

=====<file: wiki/entities/工效学.md>=====

---
title: 工效学
slug: 工效学
kind: entity
---

正文 entity.
"""
    out = parse_files(text)
    assert "wiki/sources/2026-05-19-x.md" in out
    assert "wiki/entities/工效学.md" in out
    assert "title: x" in out["wiki/sources/2026-05-19-x.md"]
    assert "title: 工效学" in out["wiki/entities/工效学.md"]


def test_parse_files_strips_markdown_fences():
    text = """=====<file: wiki/entities/X.md>=====
```markdown
---
title: X
---

正文。
```
"""
    out = parse_files(text)
    assert "title: X" in out["wiki/entities/X.md"]
    assert "```" not in out["wiki/entities/X.md"]


def test_parse_files_returns_empty_dict_when_no_delimiter():
    assert parse_files("纯解释文本") == {}


@pytest.mark.asyncio
async def test_step2_compose_happy_path(monkeypatch):
    body = (
        "=====<file: wiki/sources/2026-05-19-x.md>=====\n\n"
        "---\ntitle: x\nslug: 2026-05-19-x\nkind: source\n---\n\n正文。\n\n"
        "=====<file: wiki/entities/工效学.md>=====\n\n"
        "---\ntitle: 工效学\nslug: 工效学\nkind: entity\n---\n\n简介。\n"
    )
    monkeypatch.setattr(step2_mod, "get_llm_provider", lambda: _FakeLLM([body]))
    out = await step2_mod.step2_compose(
        raw_md="x", step1_result=_good_step1(), related_pages={}
    )
    assert len(out) == 2
    assert "wiki/entities/工效学.md" in out


@pytest.mark.asyncio
async def test_step2_compose_raises_on_no_delimiter(monkeypatch):
    monkeypatch.setattr(step2_mod, "get_llm_provider", lambda: _FakeLLM(["纯文字无分隔符"]))
    with pytest.raises(ValueError) as e:
        await step2_mod.step2_compose(
            raw_md="x", step1_result=_good_step1(), related_pages={}
        )
    assert "分隔符" in str(e.value)


# ---------------------------------------------------------------------------
# merger
# ---------------------------------------------------------------------------


def test_cosine_basic():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([], [1, 1]) == 0.0
    assert cosine([0, 0], [1, 1]) == 0.0
    assert cosine([1, 1], [0, 0]) == 0.0


def test_parse_alias_groups_from_schema():
    schema = """## 主题
工业工程

## 别名词表

> 在前端 SchemaEditor 中维护

- 工效学 = 人因工程 = ergonomics
- 价值流图 = VSM = value stream mapping

## 关系类型词汇表

- 实现:A 是 B 的方法
"""
    groups = parse_alias_groups_from_schema(schema)
    assert groups == [
        ["工效学", "人因工程", "ergonomics"],
        ["价值流图", "VSM", "value stream mapping"],
    ]


def test_find_alias_match():
    groups = [["工效学", "人因工程", "ergonomics"], ["VSM", "价值流图"]]
    assert find_alias_match("ergonomics", alias_groups=groups) == "工效学"
    assert find_alias_match("VSM", alias_groups=groups) == "VSM"  # group[0]
    assert find_alias_match("路径", alias_groups=groups) is None


def test_load_alias_groups_missing_returns_empty(tmp_path):
    assert load_alias_groups(tmp_path) == []


def test_load_alias_groups_reads_real_file(tmp_path):
    (tmp_path / ".wiki-schema.md").write_text(
        "## 别名词表\n\n- 工效学 = 人因工程\n", encoding="utf-8"
    )
    groups = load_alias_groups(tmp_path)
    assert groups == [["工效学", "人因工程"]]


def _seed_entity_page(wiki_root, slug: str, brief: str):
    p = wiki_root / "wiki" / "entities" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntitle: {slug}\nslug: {slug}\nkind: entity\n---\n\n## 简介\n\n{brief}\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_merge_exact_slug(tmp_path, monkeypatch):
    _seed_entity_page(tmp_path, "工效学", "研究人机交互。")
    slug, action, _ = await merge_or_create_entity(
        candidate_name="工效学",
        candidate_brief="任意",
        wiki_root=tmp_path,
    )
    assert slug == "工效学"
    assert action == "merged"


@pytest.mark.asyncio
async def test_merge_via_alias(tmp_path):
    _seed_entity_page(tmp_path, "工效学", "研究人机交互。")
    slug, action, _ = await merge_or_create_entity(
        candidate_name="ergonomics",
        candidate_brief="任意",
        wiki_root=tmp_path,
        alias_groups=[["工效学", "ergonomics"]],
    )
    assert slug == "工效学"
    assert action == "merged_via_alias"


@pytest.mark.asyncio
async def test_merge_via_similarity(tmp_path, monkeypatch):
    _seed_entity_page(tmp_path, "工效学", "研究人机交互的学科")

    class _SimEmb:
        async def embed(self, texts):
            # 让候选与"工效学"高度相似
            return [[1.0, 0.0]] + [[0.99, 0.01] for _ in texts[1:]]

    monkeypatch.setattr(merger_mod, "get_embedding_provider", lambda: _SimEmb())

    slug, action, meta = await merge_or_create_entity(
        candidate_name="ergonomics",
        candidate_brief="ergonomics 是研究人机交互的学科",
        wiki_root=tmp_path,
        alias_groups=[],
    )
    assert action == "merged_via_similarity"
    assert slug == "工效学"
    assert meta["cosine"] >= 0.92


@pytest.mark.asyncio
async def test_merge_suggested(tmp_path, monkeypatch):
    _seed_entity_page(tmp_path, "工效学", "研究人在工作中的效率")

    class _MidEmb:
        async def embed(self, texts):
            # 余弦 0.8 的"疑似"相似度
            return [[1.0, 0.0]] + [[0.8, 0.6] for _ in texts[1:]]

    monkeypatch.setattr(merger_mod, "get_embedding_provider", lambda: _MidEmb())

    slug, action, meta = await merge_or_create_entity(
        candidate_name="人体工学",
        candidate_brief="人体工学概念",
        wiki_root=tmp_path,
    )
    assert action == "suggested_merge"
    assert slug == "人体工学"
    assert 0.75 <= meta["cosine"] < 0.92


@pytest.mark.asyncio
async def test_merge_raises_when_embedding_fails(tmp_path, monkeypatch):
    """质量优先(2026-06-11):embedding 不可用 → 判重失效 → 抛 IngestQualityError,
    不再静默降级 created_no_embedding(那会让同义实体重复建页污染图谱)。"""
    from app.core.exceptions import IngestQualityError

    _seed_entity_page(tmp_path, "工效学", "研究人因")

    class _BadEmb:
        async def embed(self, texts):
            from app.core.exceptions import ProviderError as PE
            raise PE("no api key")

    monkeypatch.setattr(merger_mod, "get_embedding_provider", lambda: _BadEmb())
    with pytest.raises(IngestQualityError):
        await merge_or_create_entity(
            candidate_name="新概念",
            candidate_brief="一些描述",
            wiki_root=tmp_path,
        )


@pytest.mark.asyncio
async def test_merge_creates_when_no_existing(tmp_path):
    slug, action, _ = await merge_or_create_entity(
        candidate_name="新概念",
        candidate_brief="描述",
        wiki_root=tmp_path,
    )
    assert action == "created"
    assert slug == "新概念"


# ---------------------------------------------------------------------------
# apply_observation_block
# ---------------------------------------------------------------------------


def test_apply_observation_block_appends_when_section_missing():
    existing = (
        "---\ntitle: 工效学\nslug: 工效学\nkind: entity\nversion: 1\n---\n\n"
        "## 简介\n\n研究人在工作中的效率。\n"
    )
    out = apply_observation_block(
        existing,
        source_slug="2026-05-19-x",
        new_observation="本素材强调测量方法。",
    )
    assert "## 不同素材中的观点" in out
    assert "### 来源:[[2026-05-19-x]]" in out
    assert "本素材强调测量方法" in out


def test_apply_observation_block_appends_to_existing_section():
    existing = (
        "---\ntitle: 工效学\nslug: 工效学\nkind: entity\nversion: 1\n---\n\n"
        "## 简介\n\n研究人因。\n\n"
        "## 不同素材中的观点\n\n"
        "### 来源:[[old-source]]\n\n旧观点。\n"
    )
    out = apply_observation_block(
        existing,
        source_slug="new-source",
        new_observation="新观点。",
    )
    assert "### 来源:[[old-source]]" in out
    assert "### 来源:[[new-source]]" in out
    assert "旧观点" in out
    assert "新观点" in out


def test_apply_observation_block_replaces_existing_subsection():
    """同 source_slug 已存在 → 覆盖旧子段。"""
    existing = (
        "---\ntitle: 工效学\nslug: 工效学\nkind: entity\nversion: 1\n---\n\n"
        "## 不同素材中的观点\n\n"
        "### 来源:[[2026-05-19-x]]\n\n旧观点。\n"
    )
    out = apply_observation_block(
        existing,
        source_slug="2026-05-19-x",
        new_observation="覆盖后的新观点。",
    )
    assert out.count("### 来源:[[2026-05-19-x]]") == 1
    assert "旧观点" not in out
    assert "覆盖后的新观点" in out


def test_apply_observation_block_with_contradictions():
    existing = (
        "---\ntitle: 工效学\nslug: 工效学\nkind: entity\n---\n\n## 简介\n\n基本信息。\n"
    )
    out = apply_observation_block(
        existing,
        source_slug="new-src",
        new_observation="另一种观点。",
        contradicts_with=["old-src-a", "old-src-b"],
    )
    assert "⚠ 与来源" in out
    assert "[[old-src-a]]" in out
    assert "[[old-src-b]]" in out
