"""chunker 单元测试(改造计划 Phase 9:行号对齐、超长章节切分)。"""

from __future__ import annotations

from app.services.query.chunker import (
    chunk_page,
    chunk_page_cached,
    invalidate_chunk_cache,
    page_content_hash,
)
from app.services.wiki.frontmatter_io import parse_text


def _write(tmp_path, slug, text):
    p = tmp_path / f"{slug}.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_chunk_by_h2_with_intro(tmp_path):
    text = (
        "---\ntitle: Alpha\nkind: entity\naliases:\n  - 阿尔法\n---\n\n"
        "开头引言第一行。\n"
        "引言第二行。\n"
        "\n"
        "## 定义\n"
        "定义正文。\n"
        "\n"
        "## 步骤\n"
        "步骤正文一。\n"
        "步骤正文二。\n"
    )
    path = _write(tmp_path, "alpha", text)
    chunks = chunk_page(path)
    assert [c["chunk_id"] for c in chunks] == ["alpha::00", "alpha::01", "alpha::02"]
    assert [c["heading"] for c in chunks] == ["", "定义", "步骤"]
    # 行号对齐:与 grep 的 body.splitlines() 同一坐标系
    _, body = parse_text(path.read_text(encoding="utf-8"))
    lines = body.splitlines()
    intro = chunks[0]
    assert lines[intro["line_start"] - 1] == "开头引言第一行。"
    steps = chunks[2]
    assert lines[steps["line_start"] - 1] == "## 步骤"
    assert lines[steps["line_end"] - 1] == "步骤正文二。"
    # 章节块 line_start 指向标题行,text 不含标题行
    assert "## 步骤" not in steps["text"]
    assert "步骤正文一。" in steps["text"]
    # aliases 并入第一个 chunk 的嵌入文本,其余不含
    assert "阿尔法" in intro["embed_text"]
    assert "阿尔法" not in chunks[1]["embed_text"]
    # 嵌入文本带页面标题上下文
    assert chunks[1]["embed_text"].startswith("Alpha · 定义")
    assert intro["embed_text"].startswith("Alpha · 引言")


def test_long_section_split_by_paragraphs(tmp_path):
    para = "这是一个足够长的段落内容。" * 30  # ~390 字
    text = (
        "---\ntitle: Beta\nkind: topic\n---\n\n"
        "## 超长章节\n"
        f"{para}\n"
        "\n"
        f"{para}\n"
        "\n"
        f"{para}\n"
    )
    path = _write(tmp_path, "beta", text)
    chunks = chunk_page(path)
    assert len(chunks) >= 2
    assert all(c["heading"] == "超长章节" for c in chunks)
    # 第一个子块包含标题行,后续子块行号接续且不重叠
    assert chunks[0]["line_start"] == 1
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt["line_start"] > prev["line_end"]
    # 每块内容非空且落在目标区间附近(单段落 390 字,不切开段落)
    for c in chunks:
        assert c["text"].strip()


def test_heading_only_section_and_empty_page(tmp_path):
    path = _write(
        tmp_path, "gamma", "---\ntitle: G\n---\n\n## 只有标题\n\n## 下一节\n正文\n"
    )
    chunks = chunk_page(path)
    headings = [c["heading"] for c in chunks]
    assert "下一节" in headings
    empty = _write(tmp_path, "empty", "---\ntitle: E\n---\n")
    assert chunk_page(empty) == []


def test_page_content_hash_changes_with_content(tmp_path):
    p1 = _write(tmp_path, "h1", "---\ntitle: H\n---\n\nbody one\n")
    h_before = page_content_hash(chunk_page(p1))
    p1.write_text("---\ntitle: H\n---\n\nbody two\n", encoding="utf-8")
    h_after = page_content_hash(chunk_page(p1))
    assert h_before != h_after


def test_chunk_cache_invalidation(tmp_path):
    invalidate_chunk_cache()
    p = _write(tmp_path, "cache", "---\ntitle: C\n---\n\nbody\n")
    first = chunk_page_cached(p)
    assert chunk_page_cached(p) is first  # TTL 内且 mtime 未变,命中缓存
    invalidate_chunk_cache()
    assert chunk_page_cached(p) is not first
