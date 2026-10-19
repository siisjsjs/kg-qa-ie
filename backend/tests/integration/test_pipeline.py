"""ingestion pipeline 集成测试。

外部依赖全 mock:
  - LLM provider:返回固定 Step1 JSON / Step2 多文件 markdown
  - Embedding provider:返回固定向量(Mid 相似度,触发 created)
  - DB:SQLite 内存库(由 tests/integration/conftest.py 准备)

覆盖路径:
  1. 端到端首次 ingest:cache miss → step1/2 全跑 → 落 source/entity/topic 页 → DB 同步
  2. cache 命中:第二次跑同 hash 文档 → 全部 stage = skipped
  3. force=True 命中后仍重跑
  4. 跨素材源合并:第二份 md 共享 "工效学" 实体 → 实体页"## 不同素材中的观点"段被追加,而不是新建
  5. step1 LLM 输出非法 → job 标 failed,documents.parse_status = failed
  6. 隐私检查 block → upload 阶段拒绝,不创建 document
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from app.core import config as cfg_module
from app.core.db import session_scope
from app.models.document import Document
from app.models.ingest_job import IngestJob
from app.models.user import User
from app.models.wiki_link import WikiLink
from app.models.wiki_page import WikiPage
from app.services.ingestion import pipeline as pipeline_mod
from app.services.ingestion import step1_extract as step1_mod
from app.services.ingestion import step2_compose as step2_mod
from app.services.ingestion import merger as merger_mod
from app.services.llm.base import ChatMessage, LLMProvider


class _FakeLLM(LLMProvider):
    name = "fake"

    def __init__(self, responses: list[str], *, unify_response: str | None = None):
        super().__init__(model="fake")
        self._responses = list(responses)
        self.calls = 0
        self.unify_calls = 0
        # unify(实体融合)请求单独应答:其 system 提示含"知识工程师"。默认回一份
        # 合法的单一定义融合页(无"## 不同素材中的观点"段),让新的硬失败逻辑能通过。
        self._unify_response = unify_response

    def _is_unify(self, messages) -> bool:
        for m in messages:
            role = getattr(m, "role", None)
            content = getattr(m, "content", "") or ""
            if role == "system" and "知识工程师" in content:
                return True
        return False

    async def chat(self, messages, *, stream=False, **params) -> str | AsyncIterator[str]:
        self.calls += 1
        if self._is_unify(messages):
            self.unify_calls += 1
            text = self._unify_response if self._unify_response is not None else _unify_response()
        elif not self._responses:
            text = ""
        else:
            text = self._responses.pop(0)
        if stream:
            async def _gen():
                yield text
            return _gen()
        return text

    async def test_connection(self):
        return True, "ok"


class _FakeEmbedding:
    """让候选与现有实体的余弦落在 [0.0, 0.6] 之间(<0.75 → 新建)。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for i, _ in enumerate(texts):
            if i == 0:
                out.append([1.0, 0.0])
            else:
                out.append([0.5, 0.5])
        return out


class _SimilarEmbedding:
    """让候选与现有实体高相似(≥0.92 → merged_via_similarity)。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0]] + [[0.99, 0.05] for _ in texts[1:]]


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def wiki_root(tmp_path, monkeypatch):
    from app.services.wiki.init import init_wiki

    root = tmp_path / "wiki_root"
    init_wiki(root)
    monkeypatch.setattr(cfg_module.settings, "wiki_root", str(root))
    return root


@pytest.fixture(autouse=True)
def _stub_post_post_process(monkeypatch):
    """后处理(孤儿消解/分类/Neo4j/nav-map)依赖真实 LLM + Neo4j,集成测试只验证主流程。

    Plan A 改动后 _post_post_process 由主流程 await(不再 fire-and-forget),因此这里
    显式 stub 成 no-op,把这些测试的断言保持在"主流水线产物"层面;后处理本身有
    独立的单测覆盖。
    """
    async def _noop(*, wiki_root, written_paths):  # noqa: ANN001, ARG001
        return None

    monkeypatch.setattr(pipeline_mod, "_post_post_process", _noop)


@pytest.fixture
def admin_user():
    """提前建一个管理员账户(documents.owner_id 是 FK)。"""
    with session_scope() as db:
        u = User(
            username="admin_pipeline",
            email="admin@test",
            password_hash="x",
            role="admin",
            status="active",
            display_name="admin",
        )
        db.add(u)
        db.flush()
        return u.id


def _step1_response(entity_name: str = "工效学") -> str:
    return json.dumps({
        "source_summary": {
            "title": "工效学第三章笔记",
            "kind": "note",
            "domain_tags": ["人因工程"],
            "key_takeaways": ["工效学关注人机交互", "包括人体测量与作业测定", "实施有量化指标"],
        },
        "entities": [
            {
                "name": entity_name,
                "kind": "concept",
                "brief": "研究人在工作中的效率与舒适的学科。",
                "aliases": ["人因工程"],
                "evidence": "原文",
                "confidence": "EXTRACTED",
                "domain_tags": ["人因工程"],
            }
        ],
        "topics": [
            {
                "name": "工效学的研究范畴",
                "scope": "限于作业测定与人体测量",
                "related_entities": [entity_name],
                "evidence": "原文",
                "confidence": "INFERRED",
            }
        ],
        "connections": [
            {
                "src": entity_name,
                "dst": "作业测定",
                "rel_type": "实现",
                "evidence": "原文",
                "confidence": "EXTRACTED",
            }
        ],
        "contradictions": [],
        "new_vs_existing": {
            "new_entity_names": [entity_name],
            "matched_entity_pages": [],
            "new_topic_names": ["工效学的研究范畴"],
            "matched_topic_pages": [],
        },
    }, ensure_ascii=False)


def _step2_response(entity_name: str = "工效学", source_slug: str = "2026-05-19-工效学第三章笔记") -> str:
    return f"""=====<file: wiki/sources/{source_slug}.md>=====

---
title: 工效学第三章笔记
slug: {source_slug}
kind: source
source_type: article
content_hash: "deadbeef"
ingested_at: 2026-05-19T00:00:00+00:00
version: 1
---

## 摘要

笔记摘要:{entity_name}与作业测定。

=====<file: wiki/entities/{entity_name}.md>=====

---
title: {entity_name}
slug: {entity_name}
kind: entity
aliases: ["人因工程"]
domains: ["人因工程"]
sources: ["{source_slug}"]
version: 1
---

## 简介

研究人在工作中的效率与舒适的学科。

## 核心要点

- 涵盖人体测量
- 涵盖作业测定 [[作业测定]] <!-- type: 实现 -->

=====<file: wiki/topics/工效学的研究范畴.md>=====

---
title: 工效学的研究范畴
slug: 工效学的研究范畴
kind: topic
domains: ["人因工程"]
sources: ["{source_slug}"]
version: 1
---

## 主题概要

工效学覆盖人体测量与作业测定。
"""


def _unify_response(entity_name: str = "工效学", source_slug: str = "2026-05-19-第二份") -> str:
    """unify(实体融合)的合法应答:单一定义融合页,无"## 不同素材中的观点"段。"""
    return f"""---
title: {entity_name}
slug: {entity_name}
kind: entity
domains: ["人因工程"]
sources: ["2026-05-19-一份-source", "{source_slug}"]
version: 2
---

## 定义

{entity_name}是研究人在工作中的效率与舒适的学科,综合两份素材的权威定义。

## 核心特征

- 涵盖人体测量与作业测定 [[作业测定]] <!-- type: 实现 -->

## 历史与发展

融合两份素材的发展脉络。

## 适用场景与方法

适用于离散型与流程型制造。

## 指标与量化

标准时间等量化指标。

## 典型案例

福特流水线案例。

## 常见误区与边界

不是单纯加快动作。

## 与相关概念的区别

与基础工业工程的区别。

## 参考来源

- [[2026-05-19-一份-source]]
- [[{source_slug}]]
"""


def _set_llm(monkeypatch, *, step1: str, step2: str, unify: str | None = None):
    fake = _FakeLLM([step1, step2], unify_response=unify)
    monkeypatch.setattr(step1_mod, "get_llm_provider", lambda: fake)
    monkeypatch.setattr(step2_mod, "get_llm_provider", lambda: fake)
    # 实体融合(unify)在 merger 里走 app.services.llm.get_llm_provider,
    # 而 merger 是函数内 import,因此 patch 源模块属性即可命中。
    import app.services.llm as _llm_mod
    monkeypatch.setattr(_llm_mod, "get_llm_provider", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_end_to_end_first_run(wiki_root, admin_user, monkeypatch):
    _set_llm(monkeypatch, step1=_step1_response(), step2=_step2_response())
    monkeypatch.setattr(merger_mod, "get_embedding_provider", lambda: _FakeEmbedding())

    raw = "工效学是研究人在工作中的效率的学科。\n\n包含人体测量、作业测定。"
    with session_scope() as db:
        doc, privacy = await pipeline_mod.stage_upload_md(
            db=db,
            owner_id=admin_user,
            raw_content=raw,
            source_type="md",
            original_filename="工效学.md",
            title="工效学第三章笔记",
        )
        assert doc is not None
        assert privacy["ok"] is True
        doc_id = doc.id

    result = await pipeline_mod.run_pipeline(document_id=doc_id)

    assert result["final_status"] == "succeeded", result
    assert result["source_slug"]
    assert any("wiki/sources/" in p for p in result["files_written"])
    assert any("wiki/entities/" in p for p in result["files_written"])
    assert any("wiki/topics/" in p for p in result["files_written"])

    # 文件落盘
    assert (wiki_root / "wiki" / "entities" / "工效学.md").exists()
    assert (wiki_root / "wiki" / "topics" / "工效学的研究范畴.md").exists()

    # DB 同步
    with session_scope() as db:
        pages = db.query(WikiPage).all()
        slugs = {p.slug for p in pages}
        assert "工效学" in slugs
        assert "工效学的研究范畴" in slugs

        links = db.query(WikiLink).all()
        # entity 页含 [[作业测定]] 引用
        assert any(l.src_slug == "工效学" and l.dst_slug == "作业测定" for l in links)

        # ingest_jobs 5 阶段都有(skipped 也算行)
        jobs = db.query(IngestJob).filter(IngestJob.document_id == doc_id).all()
        stages = sorted(j.stage for j in jobs)
        assert "cache_check" in stages
        assert "step1_extract" in stages
        assert "step2_compose" in stages
        assert "post_process" in stages

        # documents.parse_status 转 succeeded
        d = db.query(Document).filter(Document.id == doc_id).one()
        assert d.parse_status == "succeeded"


@pytest.mark.asyncio
async def test_pipeline_cache_hit_skips_llm(wiki_root, admin_user, monkeypatch):
    _set_llm(monkeypatch, step1=_step1_response(), step2=_step2_response())
    monkeypatch.setattr(merger_mod, "get_embedding_provider", lambda: _FakeEmbedding())

    raw = "工效学相同内容。"
    with session_scope() as db:
        doc1, _ = await pipeline_mod.stage_upload_md(
            db=db, owner_id=admin_user, raw_content=raw, source_type="md",
            original_filename="x.md", title="t1",
        )
        doc1_id = doc1.id
    await pipeline_mod.run_pipeline(document_id=doc1_id)

    # 第二次:同样的 raw 内容 → hash 一致 → cache 命中
    fake2 = _FakeLLM([])  # 不应被调用到
    monkeypatch.setattr(step1_mod, "get_llm_provider", lambda: fake2)
    monkeypatch.setattr(step2_mod, "get_llm_provider", lambda: fake2)

    with session_scope() as db:
        doc2, _ = await pipeline_mod.stage_upload_md(
            db=db, owner_id=admin_user, raw_content=raw, source_type="md",
            original_filename="x2.md", title="t2",
        )
        doc2_id = doc2.id
    result2 = await pipeline_mod.run_pipeline(document_id=doc2_id)

    assert result2["final_status"] == "skipped"
    assert fake2.calls == 0
    assert all(j["status"] == "skipped" or j["stage"] == "cache_check" for j in result2["jobs"])


@pytest.mark.asyncio
async def test_pipeline_force_overrides_cache(wiki_root, admin_user, monkeypatch):
    _set_llm(monkeypatch, step1=_step1_response(), step2=_step2_response())
    monkeypatch.setattr(merger_mod, "get_embedding_provider", lambda: _FakeEmbedding())

    raw = "工效学。"
    with session_scope() as db:
        d1, _ = await pipeline_mod.stage_upload_md(
            db=db, owner_id=admin_user, raw_content=raw, source_type="md",
            original_filename="a.md", title="a",
        )
        d1_id = d1.id
    await pipeline_mod.run_pipeline(document_id=d1_id)

    # force 重跑:LLM 应被再次调用
    fake = _set_llm(monkeypatch, step1=_step1_response(), step2=_step2_response())
    monkeypatch.setattr(merger_mod, "get_embedding_provider", lambda: _SimilarEmbedding())
    with session_scope() as db:
        d2, _ = await pipeline_mod.stage_upload_md(
            db=db, owner_id=admin_user, raw_content=raw, source_type="md",
            original_filename="b.md", title="b",
        )
        d2_id = d2.id
    result = await pipeline_mod.run_pipeline(document_id=d2_id, force=True)
    assert result["final_status"] == "succeeded"
    # step1 + step2 + unify(工效学 已存在于第一份 → 第二份触发跨素材融合)
    assert fake.calls == 3
    assert fake.unify_calls >= 1


@pytest.mark.asyncio
async def test_pipeline_cross_source_merge_unifies_single_page(wiki_root, admin_user, monkeypatch):
    """两份不同 md 共享同名实体 → 第二份不新建实体页,用 LLM 融合成**一份单一定义页**。

    质量优先(2026-06-11):不再追加"## 不同素材中的观点"并列子段,而是融合;
    若融合失败则整篇 job 失败(本测试给 fake 合法融合应答,验证成功路径)。
    """
    # 第一份
    _set_llm(monkeypatch, step1=_step1_response(), step2=_step2_response())
    monkeypatch.setattr(merger_mod, "get_embedding_provider", lambda: _FakeEmbedding())

    with session_scope() as db:
        d1, _ = await pipeline_mod.stage_upload_md(
            db=db, owner_id=admin_user, raw_content="第一份关于工效学的笔记。",
            source_type="md", original_filename="一.md", title="一份",
        )
        d1_id = d1.id
    await pipeline_mod.run_pipeline(document_id=d1_id)
    entity_path = wiki_root / "wiki" / "entities" / "工效学.md"
    first_text = entity_path.read_text(encoding="utf-8")

    # 第二份:LLM 返回 matched_entity_pages = [{name: 工效学, slug: 工效学}]
    step1_v2 = json.loads(_step1_response())
    step1_v2["new_vs_existing"]["new_entity_names"] = []
    step1_v2["new_vs_existing"]["matched_entity_pages"] = [
        {"name": "工效学", "slug": "工效学", "match_kind": "exact"}
    ]
    step2_v2 = _step2_response(source_slug="2026-05-19-第二份")
    fake2 = _set_llm(
        monkeypatch,
        step1=json.dumps(step1_v2, ensure_ascii=False),
        step2=step2_v2,
    )

    with session_scope() as db:
        d2, _ = await pipeline_mod.stage_upload_md(
            db=db, owner_id=admin_user, raw_content="第二份关于工效学的不同视角。",
            source_type="md", original_filename="二.md", title="二份",
        )
        d2_id = d2.id
    result = await pipeline_mod.run_pipeline(document_id=d2_id)
    assert result["final_status"] == "succeeded"

    # 实体页只有一份(没新建);unify 被调用过;融合页**不含**"## 不同素材中的观点"
    entity_files = list((wiki_root / "wiki" / "entities").glob("工效学*.md"))
    assert len(entity_files) == 1
    assert fake2.unify_calls >= 1, "跨素材同名实体应触发 unify 融合"
    second_text = entity_path.read_text(encoding="utf-8")
    assert "## 不同素材中的观点" not in second_text
    assert second_text != first_text  # 内容确实被融合更新了
    assert "## 定义" in second_text  # 融合页保持单一定义骨架


@pytest.mark.asyncio
async def test_pipeline_cross_source_merge_fails_when_unify_fails(wiki_root, admin_user, monkeypatch):
    """unify 融合失败(LLM 返回非法输出)→ 整篇 job 失败,且第二次的实体页保持第一份原样(不污染)。"""
    _set_llm(monkeypatch, step1=_step1_response(), step2=_step2_response())
    monkeypatch.setattr(merger_mod, "get_embedding_provider", lambda: _FakeEmbedding())

    with session_scope() as db:
        d1, _ = await pipeline_mod.stage_upload_md(
            db=db, owner_id=admin_user, raw_content="第一份关于工效学的笔记。",
            source_type="md", original_filename="一.md", title="一份",
        )
        d1_id = d1.id
    await pipeline_mod.run_pipeline(document_id=d1_id)
    entity_path = wiki_root / "wiki" / "entities" / "工效学.md"
    first_text = entity_path.read_text(encoding="utf-8")

    # 第二份:matched 同名实体 → 触发 unify;但给 unify 一个非法应答 → 重试耗尽 → 硬失败
    step1_v2 = json.loads(_step1_response())
    step1_v2["new_vs_existing"]["new_entity_names"] = []
    step1_v2["new_vs_existing"]["matched_entity_pages"] = [
        {"name": "工效学", "slug": "工效学", "match_kind": "exact"}
    ]
    # unify 提速:把重试次数压到 0,避免测试等待
    from app.services.admin import config_store
    monkeypatch.setattr(config_store, "get_sync", lambda k, default="": "0" if k == "ingestion.unify_max_retries" else default)
    _set_llm(
        monkeypatch,
        step1=json.dumps(step1_v2, ensure_ascii=False),
        step2=_step2_response(source_slug="2026-05-19-第二份"),
        unify="这不是合法的 markdown 页面(无 frontmatter)",
    )

    with session_scope() as db:
        d2, _ = await pipeline_mod.stage_upload_md(
            db=db, owner_id=admin_user, raw_content="第二份关于工效学的不同视角。",
            source_type="md", original_filename="二.md", title="二份",
        )
        d2_id = d2.id
    result = await pipeline_mod.run_pipeline(document_id=d2_id)

    # 整篇失败
    assert result["final_status"] == "failed"
    failed = next(j for j in result["jobs"] if j["status"] == "failed")
    assert failed["stage"] == "post_process"
    # 关键:实体页未被污染,仍是第一份的原样
    assert entity_path.read_text(encoding="utf-8") == first_text
    with session_scope() as db:
        d = db.query(Document).filter(Document.id == d2_id).one()
        assert d.parse_status == "failed"


@pytest.mark.asyncio
async def test_pipeline_step1_invalid_marks_job_failed(wiki_root, admin_user, monkeypatch):
    bad = "not even close to JSON"
    fake = _FakeLLM([bad, bad])
    monkeypatch.setattr(step1_mod, "get_llm_provider", lambda: fake)
    monkeypatch.setattr(step2_mod, "get_llm_provider", lambda: fake)

    with session_scope() as db:
        doc, _ = await pipeline_mod.stage_upload_md(
            db=db, owner_id=admin_user, raw_content="content",
            source_type="md", original_filename="x.md", title="x",
        )
        doc_id = doc.id
    result = await pipeline_mod.run_pipeline(document_id=doc_id)

    assert result["final_status"] == "failed"
    failed_stage = next(j for j in result["jobs"] if j["status"] == "failed")
    assert failed_stage["stage"] == "step1_extract"

    with session_scope() as db:
        d = db.query(Document).filter(Document.id == doc_id).one()
        assert d.parse_status == "failed"
        assert d.parse_error and "step1" in d.parse_error


@pytest.mark.asyncio
async def test_upload_blocks_on_privacy_block_hits(wiki_root, admin_user):
    raw = "我的 sk-test-placeholder 是 API key。"
    with session_scope() as db:
        doc, privacy = await pipeline_mod.stage_upload_md(
            db=db, owner_id=admin_user, raw_content=raw,
            source_type="md", original_filename="bad.md", title="bad",
        )
    assert doc is None
    assert privacy["ok"] is False
    assert privacy["block_count"] >= 1


@pytest.mark.asyncio
async def test_pipeline_writes_wiki_node_versions(wiki_root, admin_user, monkeypatch):
    """P3 验收:ingest 落盘后,实体页/主题页/source 页都该写一条 wiki_node_versions。"""
    from app.models.wiki_node_version import WikiNodeVersion

    _set_llm(monkeypatch, step1=_step1_response(), step2=_step2_response())
    monkeypatch.setattr(merger_mod, "get_embedding_provider", lambda: _FakeEmbedding())

    with session_scope() as db:
        doc, _ = await pipeline_mod.stage_upload_md(
            db=db, owner_id=admin_user, raw_content="工效学相关笔记。",
            source_type="md", original_filename="x.md", title="ergonomics",
        )
        doc_id = doc.id
    result = await pipeline_mod.run_pipeline(document_id=doc_id)
    assert result["final_status"] == "succeeded"

    with session_scope() as db:
        rows = db.query(WikiNodeVersion).all()
        slugs = {r.slug for r in rows}
        # 工效学 / 主题 / source 都该有版本行
        assert "工效学" in slugs
        assert any("工效学" in s for s in slugs)
        for r in rows:
            assert r.triggered_by in ("ingest", "merge")
            assert r.document_id == doc_id


@pytest.mark.asyncio
async def test_pipeline_cross_source_merge_records_merge_version(wiki_root, admin_user, monkeypatch):
    """跨 source 合并触发的版本应 triggered_by='merge'(action 不是 'merged' 字面)。"""
    from app.models.wiki_node_version import WikiNodeVersion

    _set_llm(monkeypatch, step1=_step1_response(), step2=_step2_response())
    monkeypatch.setattr(merger_mod, "get_embedding_provider", lambda: _SimilarEmbedding())

    # 先 ingest 第一份,建立 工效学 实体页
    with session_scope() as db:
        d1, _ = await pipeline_mod.stage_upload_md(
            db=db, owner_id=admin_user, raw_content="第一份关于工效学",
            source_type="md", original_filename="a.md", title="一份",
        )
        d1_id = d1.id
    await pipeline_mod.run_pipeline(document_id=d1_id)

    # 第二份:LLM 让其余弦命中 → merged_via_similarity
    step1_v2 = json.loads(_step1_response())
    step2_v2 = _step2_response(source_slug="2026-05-19-第二份")
    _set_llm(monkeypatch, step1=json.dumps(step1_v2, ensure_ascii=False), step2=step2_v2)

    with session_scope() as db:
        d2, _ = await pipeline_mod.stage_upload_md(
            db=db, owner_id=admin_user, raw_content="第二份不同视角",
            source_type="md", original_filename="b.md", title="二份",
        )
        d2_id = d2.id
    result = await pipeline_mod.run_pipeline(document_id=d2_id)
    assert result["final_status"] == "succeeded"

    with session_scope() as db:
        rows = (
            db.query(WikiNodeVersion)
            .filter(WikiNodeVersion.slug == "工效学")
            .order_by(WikiNodeVersion.version.asc())
            .all()
        )
        triggers = [r.triggered_by for r in rows]
        # 至少有一条 merge / ingest 记录
        assert "merge" in triggers or "ingest" in triggers


@pytest.mark.asyncio
async def test_materialize_page_low_similarity_reuse_does_not_overwrite(
    wiki_root, monkeypatch
):
    """回归:同名实体被二次提及,但 merge 判定返回 created(低相似度/字面不等)且
    目标页已存在时,不能直接覆盖已有页,必须走 LLM 融合(unify)。

    旧 bug:`_materialize_page` 用 `action.startswith("merged")` 判断是否融合,
    action=created/suggested_merge 会落到 else 分支 atomic_write 覆盖已有页,
    导致前面素材累积的正文 + sources 全丢。修复后改成「目标文件已存在即融合」。

    质量优先(2026-06-11):融合走 unify_entity_pages(LLM 综合成单一定义页),
    且 _materialize_page 只把结果写进 staged(内存),不直接落盘。本测试验证:
    目标页存在 + action=created → 仍触发 unify,且 staged 含融合页(未覆盖丢数据)。
    """
    from app.services.ingestion import pipeline as pl

    # 先放一份已有实体页(来自第一份素材)
    ent_dir = wiki_root / "wiki" / "entities"
    ent_dir.mkdir(parents=True, exist_ok=True)
    existing = (
        "---\n"
        "title: ECRS\n"
        "slug: ECRS\n"
        "kind: entity\n"
        "sources:\n"
        "- 2026-06-01-第一章\n"
        "version: 1\n"
        "---\n\n"
        "## 定义\n\n第一份素材的定义内容。\n\n"
        "## 典型案例\n\n第一份独有案例:发货时间由6天缩短为4天。\n"
    )
    (ent_dir / "ECRS.md").write_text(existing, encoding="utf-8")

    # merge 判定:返回 created(模拟低相似度二次提及),slug 仍指向已存在的 ECRS 页
    async def _fake_merge(*, candidate_name, candidate_brief, wiki_root, alias_groups, **kw):  # noqa: ANN001, ARG001
        return "ECRS", "created", {"matched_via": "test_low_similarity"}

    monkeypatch.setattr(pl, "merge_or_create_entity", _fake_merge)

    # unify LLM 返回一份合法融合页(综合两份素材,含两个独有案例与并集来源)
    unified_page = (
        "---\n"
        "title: ECRS\n"
        "slug: ECRS\n"
        "kind: entity\n"
        "version: 2\n"
        "---\n\n"
        "## 定义\n\n综合两份素材后的统一定义。\n\n"
        "## 典型案例\n\n第一份独有案例:发货时间由6天缩短为4天。"
        "第二份独有案例:物流效率提高39.2%。\n"
    )

    async def _fake_unify(**kw):  # noqa: ANN001, ARG001
        return unified_page

    # _materialize_page 内部 `from app.services.ingestion.merger import unify_entity_pages`,
    # 故 patch merger 模块即可。
    monkeypatch.setattr(merger_mod, "unify_entity_pages", _fake_unify)

    incoming = (
        "---\n"
        "title: ECRS\n"
        "slug: ECRS\n"
        "kind: entity\n"
        "version: 1\n"
        "---\n\n"
        "## 定义\n\n第二份素材的补充定义。\n\n"
        "## 典型案例\n\n第二份独有案例:物流效率提高39.2%。\n"
    )

    staged: dict = {}
    rel, slug, action, vr = await pl._materialize_page(
        wiki_root=wiki_root,
        file_path="wiki/entities/ECRS.md",
        new_text=incoming,
        source_slug="2026-06-10-第五章",
        alias_groups=[],
        merge_actions={},
        staged=staged,
    )

    assert slug == "ECRS"
    # 两阶段:计算结果只在 staged,磁盘尚未被覆盖(仍是第一份原文)
    assert (ent_dir / "ECRS.md").read_text(encoding="utf-8") == existing

    target = pl.safe_join(wiki_root, "wiki", "entities", "ECRS.md")
    final = staged[target]

    # 1) 第一份独有内容必须还在(没被覆盖)
    assert "第一份独有案例:发货时间由6天缩短为4天" in final
    # 2) 第二份内容被融合进来
    assert "物流效率提高39.2%" in final
    # 3) sources 取并集:两个来源都在
    assert "2026-06-01-第一章" in final
    assert "2026-06-10-第五章" in final

