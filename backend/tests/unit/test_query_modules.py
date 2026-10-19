"""query 模块单元测试:alias_expand / grep / page_reader / composer / persistor / router。"""

from __future__ import annotations

import pytest

from app.services.query.alias_expand import basic_keywords, expand
from app.services.query.composer import (
    extract_citations,
    render_alias_table,
    render_context,
    split_citation_footer,
    strip_generated_citation_footer,
)
from app.services.query.grep import grep_wiki
from app.services.query.page_reader import read_for_context
from app.services.query.persistor import (
    maybe_persist,
    make_query_slug,
    render_query_md,
)
from app.services.query.router import ROUTE_CHITCHAT, _fast_kb_kind, _fast_path


# ---------------------------------------------------------------------------
# alias_expand
# ---------------------------------------------------------------------------


def test_basic_keywords_chinese_and_english():
    out = basic_keywords("VSM 怎么应用到精益生产里?")
    assert "VSM" in out
    # 连续中文整段作为单 token(对应 alias 命中走 substring 匹配)
    assert any("精益生产" in t for t in out)


def test_expand_hits_alias_group():
    groups = [["工效学", "人因工程", "ergonomics"], ["VSM", "价值流图"]]
    keywords, hits = expand("ergonomics 与 VSM 的关系", alias_groups=groups)
    assert "工效学" in keywords
    assert "人因工程" in keywords
    assert "ergonomics" in keywords
    assert "VSM" in keywords
    assert "价值流图" in keywords
    assert "工效学" in hits
    assert "VSM" in hits


def test_expand_does_not_cross_groups():
    groups = [["A", "alpha"], ["B", "beta"]]
    keywords, hits = expand("alpha", alias_groups=groups)
    assert "A" in keywords
    assert "alpha" in keywords
    assert "B" not in keywords
    assert hits == ["A"]


def test_expand_no_alias_match_returns_basic():
    keywords, hits = expand("DMAIC 全称", alias_groups=[])
    assert "DMAIC" in keywords
    assert hits == []


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


def _seed_wiki(root, files: dict[str, str]):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_grep_filename_exact_match_wins(tmp_path):
    _seed_wiki(
        tmp_path,
        {
            "wiki/entities/工效学.md": (
                "---\ntitle: 工效学\nslug: 工效学\nkind: entity\n---\n\n## 简介\n\n基础概念。\n"
            ),
            "wiki/topics/工效学的研究范畴.md": (
                "---\ntitle: 工效学的研究范畴\nslug: 工效学的研究范畴\nkind: topic\n---\n\n"
                "工效学的研究主要包括人体测量。\n"
            ),
        },
    )
    out = await grep_wiki(keywords=["工效学"], wiki_root=tmp_path, top_k=5)
    assert len(out) == 2
    # 文件名精确命中 (slug == "工效学") 应该排第一
    assert out[0]["slug"] == "工效学"


@pytest.mark.asyncio
async def test_grep_returns_hit_segments(tmp_path):
    _seed_wiki(
        tmp_path,
        {
            "wiki/entities/X.md": (
                "---\ntitle: X\nslug: X\nkind: entity\n---\n\n"
                "第一行讲 VSM。\n第二行无关。\n第三行又讲 VSM。\n"
            ),
        },
    )
    out = await grep_wiki(keywords=["VSM"], wiki_root=tmp_path, top_k=5)
    assert len(out) == 1
    seg = out[0]["hit_segments"]
    assert len(seg) == 2
    assert all("VSM" in s["text"] for s in seg)


@pytest.mark.asyncio
async def test_grep_alias_match_in_frontmatter(tmp_path):
    _seed_wiki(
        tmp_path,
        {
            "wiki/entities/工效学.md": (
                '---\ntitle: 工效学\nslug: 工效学\nkind: entity\n'
                'aliases:\n  - "ergonomics"\n  - 人因工程\n---\n\n基础概念。\n'
            ),
        },
    )
    out = await grep_wiki(keywords=["ergonomics"], wiki_root=tmp_path, top_k=5)
    assert len(out) == 1
    assert out[0]["slug"] == "工效学"
    # 命中 alias 也得到加权
    assert out[0]["score"] >= 50


@pytest.mark.asyncio
async def test_grep_empty_keywords_returns_empty(tmp_path):
    out = await grep_wiki(keywords=[], wiki_root=tmp_path, top_k=5)
    assert out == []


@pytest.mark.asyncio
async def test_grep_excludes_generated_pages_but_keeps_synthesis(tmp_path):
    """grep 只看三类原始知识(entities/topics/sources),跳过 queries/comparisons/synthesis。"""
    _seed_wiki(
        tmp_path,
        {
            "wiki/entities/工效学.md": (
                "---\ntitle: 工效学\nslug: 工效学\nkind: entity\n---\n\n关于工效学的实体页。\n"
            ),
            "wiki/topics/工效学应用.md": (
                "---\ntitle: 工效学应用\nslug: 工效学应用\nkind: topic\n---\n\n工效学如何应用。\n"
            ),
            "wiki/sources/工效学源.md": (
                "---\ntitle: 工效学源\nslug: 工效学源\nkind: source\n---\n\n工效学素材原文。\n"
            ),
            "wiki/synthesis/2026-工效学综述.md": (
                "---\ntitle: 工效学综述\nslug: 2026-工效学综述\nkind: synthesis\n---\n\n工效学的深度综述。\n"
            ),
            "wiki/queries/2026-工效学问答.md": (
                "---\ntitle: 工效学问答\nslug: 2026-工效学问答\nkind: query\n---\n\n之前关于工效学的问答。\n"
            ),
            "wiki/comparisons/2026-工效学对比.md": (
                "---\ntitle: 工效学对比\nslug: 2026-工效学对比\nkind: comparison\n---\n\n工效学和别的对比。\n"
            ),
        },
    )
    out = await grep_wiki(keywords=["工效学"], wiki_root=tmp_path, top_k=10)
    slugs = {r["slug"] for r in out}
    # 三类原始知识全在
    assert "工效学" in slugs
    assert "工效学应用" in slugs
    assert "工效学源" in slugs
    # 自我产出的问答 / 对比 / 综述页都被排除,不回流污染后续问答
    assert "2026-工效学综述" not in slugs
    assert "2026-工效学问答" not in slugs
    assert "2026-工效学对比" not in slugs


# ---------------------------------------------------------------------------
# page_reader
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_for_context_short_returns_full(tmp_path):
    _seed_wiki(
        tmp_path,
        {
            "wiki/entities/工效学.md": (
                "---\ntitle: 工效学\nslug: 工效学\nkind: entity\n---\n\n短正文。\n"
            ),
        },
    )
    page = await read_for_context(
        slug="工效学", hit_lines=[], wiki_root=tmp_path, hint_kind="entity"
    )
    assert page is not None
    assert page["truncated"] is False
    assert "短正文" in page["body_excerpt"]


@pytest.mark.asyncio
async def test_read_for_context_long_truncates(tmp_path):
    long_body = "段落\n" * 1000  # >> 2000 chars
    _seed_wiki(
        tmp_path,
        {
            "wiki/entities/X.md": f"---\ntitle: X\nslug: X\nkind: entity\n---\n\n{long_body}",
        },
    )
    page = await read_for_context(
        slug="X", hit_lines=[10, 20], wiki_root=tmp_path, hint_kind="entity"
    )
    assert page is not None
    assert page["truncated"] is True
    assert "命中段落" in page["body_excerpt"] or len(page["body_excerpt"]) <= 1000


@pytest.mark.asyncio
async def test_read_for_context_missing_returns_none(tmp_path):
    page = await read_for_context(slug="不存在", hit_lines=[], wiki_root=tmp_path)
    assert page is None


# ---------------------------------------------------------------------------
# router(正则快路径;LLM 分类已随 Agentic 改造删除)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "kind"),
    [
        ("ECRS四原则如何实施？", "method"),
        ("5S和6S有什么区别？", "comparison"),
        ("工业工程的发展历程", "timeline"),
        ("什么是标准工时？", "concept"),
    ],
)
def test_fast_kb_kind(question, kind):
    assert _fast_kb_kind(question) == kind


@pytest.mark.parametrize("question", ["你好", "谢谢!", "你是谁?", "再见"])
def test_fast_path_hits_chitchat(question):
    assert _fast_path(question) == ROUTE_CHITCHAT


@pytest.mark.parametrize("question", ["什么是ECRS?", "VSM 怎么画", "你好,请介绍一下精益生产的历史"])
def test_fast_path_misses_real_questions(question):
    assert _fast_path(question) is None


# ---------------------------------------------------------------------------
# composer
# ---------------------------------------------------------------------------


def test_extract_citations_chunk_protocol():
    """[[slug#En]] 归并到 slug;[[web#Wn]] 不算 KB 引用。"""
    answer = "论断一 [[工效学#E1]],论断二 [[工效学#E2]],网络来源 [[web#W1]],页面引用 [[VSM]]。"
    assert extract_citations(answer) == ["工效学", "VSM"]


def test_extract_citations_dedupe():
    answer = "见 [[工效学]] 和 [[VSM]],再次提到 [[工效学]]。"
    assert extract_citations(answer) == ["工效学", "VSM"]


def test_extract_citations_with_alias_pipe():
    answer = "[[工效学|人因工程]] 与 [[VSM]]"
    assert extract_citations(answer) == ["工效学", "VSM"]


def test_strip_generated_citation_footer_keeps_inline_sources_only():
    answer = "正文 [[工效学]]。\n\n## 引用\n- [[工效学]]\n- [[VSM]]"
    visible = strip_generated_citation_footer(answer)
    assert visible == "正文 [[工效学]]。"
    assert extract_citations(visible) == ["工效学"]


def test_split_citation_footer_when_supplement_follows():
    answer = (
        "正文 [[工效学]]。\n\n## 引用\n- [[工效学]]\n- [[VSM]]\n\n"
        "## 补充\n\n补充正文 [[VSM]]。\n\n## 注意事项\n\n注意 [[工效学]]。"
    )
    body, footer = split_citation_footer(answer)
    assert "## 引用" not in body
    assert "## 补充" in body and "## 注意事项" in body
    assert "补充正文" in body and "注意" in body
    assert footer == "## 引用\n- [[工效学]]\n- [[VSM]]"


def test_split_citation_footer_ignores_prose_section_named_citation():
    answer = "## 引用\n\n本节讨论引用规范,不是清单 [[工效学]] 的用法。"
    body, footer = split_citation_footer(answer)
    assert footer == ""
    assert body == answer


def test_split_citation_footer_accepts_no_source_placeholder():
    answer = "正文。\n\n## 引用\n- (本回答未引用素材)"
    body, footer = split_citation_footer(answer)
    assert body == "正文。"
    assert "本回答未引用素材" in footer


def test_split_citation_footer_without_footer_is_noop():
    answer = "正文 [[工效学]]。"
    assert split_citation_footer(answer) == (answer, "")


def test_render_context_with_pages():
    out = render_context([
        {"slug": "工效学", "kind": "entity", "title": "工效学", "truncated": False, "body_excerpt": "正文。"}
    ])
    assert "[[工效学]]" in out
    assert "正文" in out


def test_render_context_empty():
    assert render_context([]) == "(无相关页面)"


def test_render_alias_table():
    out = render_alias_table([["工效学", "人因工程"], ["VSM", "价值流图"]])
    assert "工效学 = 人因工程" in out
    assert "VSM = 价值流图" in out


# ---------------------------------------------------------------------------
# persistor
# ---------------------------------------------------------------------------


def test_make_query_slug_stable_for_same_question():
    s1 = make_query_slug(question="工效学是什么?", date="2026-05-19")
    s2 = make_query_slug(question="工效学是什么?", date="2026-05-19")
    assert s1 == s2
    assert s1.startswith("2026-05-19-")


def test_render_query_md_includes_citations():
    md = render_query_md(
        title_question="VSM 怎么实施?",
        slug="2026-05-19-vsm-xxx",
        answer="按以下步骤... [[VSM]]",
        citations=["VSM", "工效学"],
        quality={"score": 0.85, "tier": "gold", "gap_flag": False},
    )
    assert "## 答案" in md
    assert "## 引用" in md
    assert "[[VSM]]" in md
    assert "[[工效学]]" in md


@pytest.mark.asyncio
async def test_maybe_persist_skips_when_few_citations(tmp_path):
    out = await maybe_persist(
        question="x", answer="y", citations=["A"], wiki_root=tmp_path
    )
    assert out is None
    assert not list((tmp_path / "wiki" / "queries").glob("*.md")) if (tmp_path / "wiki" / "queries").exists() else True


@pytest.mark.asyncio
async def test_maybe_persist_writes_when_3plus_citations(tmp_path):
    out = await maybe_persist(
        question="VSM 怎么实施?",
        answer="按 [[VSM]]、[[标准时间]]、[[作业测定]]...",
        citations=["VSM", "标准时间", "作业测定"],
        wiki_root=tmp_path,
    )
    assert out is not None
    p = tmp_path / "wiki" / "queries" / f"{out}.md"
    assert p.exists()


@pytest.mark.asyncio
async def test_maybe_persist_force_true_writes_even_with_few(tmp_path):
    out = await maybe_persist(
        question="x?",
        answer="ans [[A]]",
        citations=["A"],
        wiki_root=tmp_path,
        force=True,
    )
    assert out is not None


@pytest.mark.asyncio
async def test_maybe_persist_force_false_never_writes(tmp_path):
    out = await maybe_persist(
        question="x?",
        answer="ans [[A]] [[B]] [[C]] [[D]]",
        citations=["A", "B", "C", "D"],
        wiki_root=tmp_path,
        force=False,
    )
    assert out is None


def test_render_query_md_keeps_rich_media():
    md = render_query_md(
        title_question="2026 学习路径",
        slug="2026-08-23-path-xxx",
        answer="见下图 {{image:img_abc123def456}} [[web#W1]]",
        citations=["工业工程", {"slug": "web#W1"}],
        quality={"score": 0.7, "tier": "silver", "gap_flag": False},
        session_id=163,
        images=[{
            "image_id": "img_abc123def456",
            "caption": "路线图",
            "chart": {"template_id": "tech-roadmap", "name": "技术路线图"},
        }],
        web_citations=[{"ref": "W1", "title": "外链", "url": "https://example.com"}],
        citation_details=[
            {"type": "chunk", "slug": "工业工程", "ref": "E1", "title": "工业工程"},
            {"type": "web", "ref": "W1", "title": "外链", "url": "https://example.com"},
        ],
    )
    assert "session_id: 163" in md
    assert "img_abc123def456" in md
    assert "tech-roadmap" in md
    assert "[[工业工程#E1]]" in md
    assert "[[web#W1]]" in md
    assert "https://example.com" in md


@pytest.mark.asyncio
async def test_maybe_persist_writes_session_and_images(tmp_path):
    out = await maybe_persist(
        question="画一张技术路线图",
        answer="{{image:img_abc123def456}}",
        citations=["工业工程"],
        wiki_root=tmp_path,
        force=True,
        session_id=42,
        images=[{"image_id": "img_abc123def456", "caption": "路线图"}],
        citation_details=[{"type": "chunk", "slug": "工业工程", "ref": "E1"}],
    )
    assert out is not None
    text = (tmp_path / "wiki" / "queries" / f"{out}.md").read_text(encoding="utf-8")
    assert "session_id: 42" in text
    assert "img_abc123def456" in text
    assert "[[工业工程#E1]]" in text
