"""source_writer.py 单元测试。"""

import pytest

from app.services.ingestion.source_writer import (
    make_source_slug,
    render_source_md,
    write_source_page,
)
from app.services.wiki.frontmatter_io import parse_text


def test_make_source_slug_with_explicit_date():
    s = make_source_slug(title="VSM 案例:某汽配厂", date="2026-05-19")
    assert s.startswith("2026-05-19-")
    assert "VSM" in s


def test_make_source_slug_chinese_title_kept():
    s = make_source_slug(title="易树平基础工业工程笔记", date="2026-05-19")
    assert s == "2026-05-19-易树平基础工业工程笔记"


def test_render_source_md_contains_required_sections():
    md = render_source_md(
        title="工效学第三章笔记",
        slug="2026-05-19-工效学第三章笔记",
        summary="本章讨论人体测量与作业测定。",
        raw_excerpt="工效学是关注人在工作中的效率与舒适的学科……",
        source_type="article",
        raw_path="raw/articles/2026-05-19-工效学第三章笔记.md",
        original_filename="工效学.md",
        content_hash="a" * 64,
        tags=["人因", "笔记"],
        domains=["人因工程"],
        extracted_entities=["工效学", "作业测定"],
        extracted_topics=["工效学的研究范畴"],
    )
    assert "## 来源元数据" in md
    assert "## 摘要" in md
    assert "## 抽取的实体" in md
    assert "## 原文(节选)" in md
    assert "[[工效学]]" in md
    assert "[[作业测定]]" in md

    fm, _ = parse_text(md)
    assert fm["title"] == "工效学第三章笔记"
    assert fm["kind"] == "source"
    assert fm["source_type"] == "article"
    assert fm["content_hash"] == "a" * 64
    assert fm["extracted_entities"] == ["工效学", "作业测定"]
    assert fm["version"] == 1


@pytest.mark.asyncio
async def test_write_source_page_lands_on_disk(tmp_path):
    slug = await write_source_page(
        wiki_root=tmp_path,
        title="工效学第三章",
        summary="工效学摘要。",
        raw_excerpt="原文节选……",
        source_type="article",
        raw_path="raw/articles/x.md",
        original_filename="x.md",
        content_hash="b" * 64,
        date="2026-05-19",
        extracted_entities=["工效学"],
        extracted_topics=["工效学概述"],
    )
    assert slug == "2026-05-19-工效学第三章"
    p = tmp_path / "wiki" / "sources" / f"{slug}.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "[[工效学]]" in text
    assert "kind: source" in text


@pytest.mark.asyncio
async def test_write_source_page_avoids_collision(tmp_path):
    """同 slug 已存在 → 自动加 -2 后缀。"""
    s1 = await write_source_page(
        wiki_root=tmp_path,
        title="重名素材",
        summary="第一份。",
        raw_excerpt="...",
        source_type="article",
        raw_path="raw/articles/x.md",
        original_filename="x.md",
        content_hash="c" * 64,
        date="2026-05-19",
    )
    s2 = await write_source_page(
        wiki_root=tmp_path,
        title="重名素材",
        summary="第二份。",
        raw_excerpt="...",
        source_type="article",
        raw_path="raw/articles/x.md",
        original_filename="x.md",
        content_hash="d" * 64,
        date="2026-05-19",
    )
    assert s1 == "2026-05-19-重名素材"
    assert s2 == "2026-05-19-重名素材-2"
    assert (tmp_path / "wiki" / "sources" / f"{s1}.md").exists()
    assert (tmp_path / "wiki" / "sources" / f"{s2}.md").exists()


@pytest.mark.asyncio
async def test_write_source_page_blocks_path_escape(tmp_path):
    """title 含 ../ 不可越界(safe_join 拦)。slugify 已拒掉禁用字符,这里是双保险。"""
    # slugify 会把 "/" 去掉,所以越界是不可能的;此用例验证健壮性
    slug = await write_source_page(
        wiki_root=tmp_path,
        title="../etc/passwd",
        summary="...",
        raw_excerpt="...",
        source_type="article",
        raw_path="raw/x.md",
        original_filename="x.md",
        content_hash="e" * 64,
        date="2026-05-19",
    )
    # 文件应落在 wiki/sources/ 下,不会跑到 wiki_root 外部
    p = tmp_path / "wiki" / "sources" / f"{slug}.md"
    assert p.exists()
    assert tmp_path in p.parents
