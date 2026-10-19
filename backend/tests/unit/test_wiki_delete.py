"""wiki/delete 单元测试(纯文件操作 + DB 同步)。"""

from __future__ import annotations

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.core.db import Base
from app.models.wiki_link import WikiLink
from app.models.wiki_page import WikiPage
from app.services.wiki.cache import record as cache_record
from app.services.wiki.delete import (
    _strip_empty_observations_section,
    _strip_source_subsection,
    delete_page,
    delete_source,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'wd.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def wiki_root(tmp_path):
    from app.services.wiki.init import init_wiki

    root = tmp_path / "wr"
    init_wiki(root)
    return root


def _seed_page(root, kind_dir, slug, fm, body):
    p = root / "wiki" / kind_dir / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n\n" + body,
        encoding="utf-8",
    )
    return p


# ---------- 纯函数 ----------


def test_strip_source_subsection_basic():
    body = """## 简介
工效学是研究…

## 不同素材中的观点

### 来源:[[2026-05-19-A]]

观点 A 的内容

### 来源:[[2026-05-19-B]]

观点 B 的内容
"""
    out, removed = _strip_source_subsection(body, "2026-05-19-A")
    assert removed is True
    assert "观点 A 的内容" not in out
    assert "观点 B 的内容" in out


def test_strip_source_subsection_full_width_colon():
    body = "## 不同素材中的观点\n\n### 来源:[[X]]\n\n内容\n"
    out, removed = _strip_source_subsection(body, "X")
    assert removed is True
    assert "内容" not in out


def test_strip_source_subsection_no_match():
    body = "## 不同素材中的观点\n\n### 来源:[[A]]\n\n内容\n"
    _, removed = _strip_source_subsection(body, "B")
    assert removed is False


def test_strip_empty_observations_section_removes_empty():
    body = "## 简介\n正文\n\n## 不同素材中的观点\n\n"
    out = _strip_empty_observations_section(body)
    assert "## 不同素材中的观点" not in out


def test_strip_empty_observations_section_keeps_nonempty():
    body = "## 不同素材中的观点\n\n### 来源:[[X]]\n内容\n"
    out = _strip_empty_observations_section(body)
    assert "## 不同素材中的观点" in out


# ---------- delete_source 集成 ----------


@pytest.mark.asyncio
async def test_delete_source_simple(wiki_root, db):
    _seed_page(
        wiki_root,
        "sources",
        "2026-05-19-x",
        {"title": "x", "slug": "2026-05-19-x", "kind": "source"},
        "body",
    )
    cache_record(wiki_root, content_hash="a" * 64, source_slug="2026-05-19-x")

    result = await delete_source(
        source_slug="2026-05-19-x", wiki_root=wiki_root, db=db
    )
    assert "wiki/sources/2026-05-19-x.md" in result["removed_pages"]
    assert result["cache_evicted"] >= 1
    assert not (wiki_root / "wiki" / "sources" / "2026-05-19-x.md").exists()


@pytest.mark.asyncio
async def test_delete_source_cascades_observation_block(wiki_root, db):
    """实体页 sources 含被删 source + 有"### 来源:[[X]]"段 → 段删 + sources 移除。"""
    _seed_page(
        wiki_root,
        "sources",
        "src1",
        {"title": "src1", "slug": "src1", "kind": "source"},
        "源 1 摘要",
    )
    _seed_page(
        wiki_root,
        "entities",
        "工效学",
        {
            "title": "工效学",
            "slug": "工效学",
            "kind": "entity",
            "sources": ["src1", "src2"],
        },
        """## 简介

研究人在工作中…

## 不同素材中的观点

### 来源:[[src1]]

来自 src1 的视角

### 来源:[[src2]]

来自 src2 的视角
""",
    )

    result = await delete_source(source_slug="src1", wiki_root=wiki_root, db=db)
    # 实体页未被整页删(还有 src2)
    assert any(p == "wiki/entities/工效学.md" for p in result["updated_pages"])
    assert (wiki_root / "wiki" / "entities" / "工效学.md").exists()
    text = (wiki_root / "wiki" / "entities" / "工效学.md").read_text(encoding="utf-8")
    assert "src1" not in text
    assert "来自 src2 的视角" in text


@pytest.mark.asyncio
async def test_delete_source_orphan_entity_deleted(wiki_root, db):
    """实体页 sources 仅含被删 source → 整页删除 + 写 redirect。"""
    _seed_page(
        wiki_root,
        "sources",
        "only_src",
        {"title": "only", "slug": "only_src", "kind": "source"},
        "src",
    )
    _seed_page(
        wiki_root,
        "entities",
        "孤立实体",
        {
            "title": "孤立实体",
            "slug": "孤立实体",
            "kind": "entity",
            "sources": ["only_src"],
        },
        "## 简介\n仅来自 only_src 的实体",
    )

    result = await delete_source(source_slug="only_src", wiki_root=wiki_root, db=db)
    assert "wiki/entities/孤立实体.md" in result["removed_pages"]
    assert not (wiki_root / "wiki" / "entities" / "孤立实体.md").exists()

    # redirect 表写了一条:孤立实体 → None,reason=source_deleted
    from app.services.wiki.slug_redirect import lookup

    r = lookup(db, old_slug="孤立实体", kind="entity")
    assert r is not None
    assert r.new_slug is None
    assert r.reason == "source_deleted"


@pytest.mark.asyncio
async def test_delete_source_db_sync_removes_pages_and_links(wiki_root, db):
    _seed_page(
        wiki_root,
        "sources",
        "src1",
        {"title": "s", "slug": "src1", "kind": "source"},
        "源",
    )
    # seed wiki_pages + wiki_links
    db.add(WikiPage(slug="src1", title="s", kind="source", file_path="wiki/sources/src1.md", frontmatter_json="{}", version=1))
    db.add(WikiLink(src_slug="some_entity", dst_slug="src1", link_type="related", occurrences=1, confidence="EXTRACTED"))
    db.commit()

    await delete_source(source_slug="src1", wiki_root=wiki_root, db=db)
    db.commit()

    assert db.query(WikiPage).filter(WikiPage.slug == "src1").count() == 0
    # 入边 dst=src1 也被清掉
    assert db.query(WikiLink).filter(WikiLink.dst_slug == "src1").count() == 0


@pytest.mark.asyncio
async def test_delete_source_missing_file_still_cleans_up(wiki_root, db):
    """半删坏掉的"虚空 source":文件不存在也不报错,照常级联清理(delete.py 既有设计)。"""
    result = await delete_source(source_slug="ghost", wiki_root=wiki_root, db=db)
    # 文件本身不存在 → removed_pages 里没有 sources/ghost.md
    assert "wiki/sources/ghost.md" not in result["removed_pages"]


# ---------- delete_page(单页删除) ----------


@pytest.mark.asyncio
async def test_delete_page_entity(wiki_root, db):
    _seed_page(
        wiki_root,
        "entities",
        "工效学",
        {"title": "工效学", "slug": "工效学", "kind": "entity", "sources": ["s1"]},
        "## 简介\n正文",
    )
    db.add(
        WikiPage(
            slug="工效学", title="工效学", kind="entity",
            file_path="wiki/entities/工效学.md", frontmatter_json="{}", version=1,
        )
    )
    db.add(WikiLink(src_slug="工效学", dst_slug="动作研究", link_type="related", occurrences=2, confidence="EXTRACTED"))
    db.add(WikiLink(src_slug="人因", dst_slug="工效学", link_type="related", occurrences=1, confidence="EXTRACTED"))
    db.commit()

    result = await delete_page(slug="工效学", wiki_root=wiki_root, db=db)
    db.commit()

    assert result["removed_path"] == "wiki/entities/工效学.md"
    assert result["kind"] == "entity"
    assert not (wiki_root / "wiki" / "entities" / "工效学.md").exists()
    assert db.query(WikiPage).filter(WikiPage.slug == "工效学").count() == 0
    # 出入边都清掉
    assert db.query(WikiLink).filter(WikiLink.src_slug == "工效学").count() == 0
    assert db.query(WikiLink).filter(WikiLink.dst_slug == "工效学").count() == 0

    # redirect:slug → None, reason=manual
    from app.services.wiki.slug_redirect import lookup
    r = lookup(db, old_slug="工效学", kind="entity")
    assert r is not None
    assert r.new_slug is None
    assert r.reason == "manual"


@pytest.mark.asyncio
async def test_delete_page_source_kind_rejected(wiki_root, db):
    """source kind 不能走单页删除 — 调用方应改用 delete_source。"""
    _seed_page(
        wiki_root,
        "sources",
        "src1",
        {"title": "src1", "slug": "src1", "kind": "source"},
        "src body",
    )
    with pytest.raises(ValueError, match="delete_source"):
        await delete_page(slug="src1", wiki_root=wiki_root, db=db)


@pytest.mark.asyncio
async def test_delete_page_not_found(wiki_root, db):
    with pytest.raises(FileNotFoundError):
        await delete_page(slug="ghost-slug", wiki_root=wiki_root, db=db)
