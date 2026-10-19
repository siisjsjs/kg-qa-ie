"""query/chunker.py：wiki 页面切块器（chunk 级检索的基础,改造计划 §4.1）。

切块规则:
  - 按 ``## `` 二级标题切段;无标题的开头部分算「引言」块(heading="")
  - 超过 ~700 字的章节按空行段落再切,目标每块 300-600 字,块间不重叠
  - ``chunk_id = f"{slug}::{序号:02d}"``(序号按文中顺序,Chroma id 直接用它)
  - ``line_start/line_end`` 是正文 body 的 1-based 行号,与 grep 的
    ``body.splitlines()`` 坐标系完全一致(章节块的 line_start 指向标题行,
    保证 grep 命中标题行也能落进 chunk)
  - 嵌入文本 ``embed_text = f"{title} · {heading}\\n{text}"``;frontmatter 的
    aliases 并入第一个 chunk 的嵌入文本
  - ``content_hash`` 基于 embed_text,标题/别名变化同样触发重嵌
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from app.services.wiki.frontmatter_io import parse_text

# 章节超过该字数则按段落再切
SECTION_SPLIT_THRESHOLD = 700
# 再切时每块的目标区间
CHUNK_TARGET_MIN = 300
CHUNK_TARGET_MAX = 600

# 供 hybrid 词法腿映射用的短 TTL 缓存:同一次问答内多次取同页 chunk 不重复解析
_CHUNK_CACHE_TTL_SECONDS = 300.0
_CHUNK_CACHE: dict[str, tuple[float, int, list[dict]]] = {}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_sections(lines: list[str]) -> list[tuple[str, int, int]]:
    """按 ``## `` 二级标题切段,返回 [(heading, start_line, end_line)],行号 1-based 闭区间。

    第一段为引言(heading="")——可能为空(页面直接以 ``## `` 开头时跳过)。
    章节段的 start_line 指向标题行本身。
    """
    sections: list[tuple[str, int, int]] = []
    current_heading = ""
    current_start = 1
    for idx, line in enumerate(lines, start=1):
        if line.startswith("## "):
            if idx - 1 >= current_start:
                sections.append((current_heading, current_start, idx - 1))
            current_heading = line[3:].strip()
            current_start = idx
    sections.append((current_heading, current_start, len(lines)))
    # 过滤空区间(start > end,如空 body)
    return [(h, s, e) for h, s, e in sections if s <= e]


def _split_paragraph_blocks(
    lines: list[str], start_line: int, end_line: int
) -> list[tuple[int, int]]:
    """把 [start_line, end_line] 按空行切成段落块,返回行号闭区间列表。"""
    blocks: list[tuple[int, int]] = []
    block_start: int | None = None
    for ln in range(start_line, end_line + 1):
        if lines[ln - 1].strip():
            if block_start is None:
                block_start = ln
        else:
            if block_start is not None:
                blocks.append((block_start, ln - 1))
                block_start = None
    if block_start is not None:
        blocks.append((block_start, end_line))
    return blocks


def _pack_blocks(
    lines: list[str], blocks: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """贪心合并段落块到 300-600 字目标区间,不切开单个段落。"""
    packed: list[tuple[int, int]] = []
    cur_start: int | None = None
    cur_end = 0
    cur_len = 0
    for bs, be in blocks:
        block_len = sum(len(lines[i - 1]) for i in range(bs, be + 1))
        if cur_start is None:
            cur_start, cur_end, cur_len = bs, be, block_len
            continue
        if cur_len >= CHUNK_TARGET_MIN and cur_len + block_len > CHUNK_TARGET_MAX:
            packed.append((cur_start, cur_end))
            cur_start, cur_end, cur_len = bs, be, block_len
        else:
            cur_end = be
            cur_len += block_len
    if cur_start is not None:
        packed.append((cur_start, cur_end))
    return packed


def _section_text_len(lines: list[str], start: int, end: int) -> int:
    return sum(len(lines[i - 1]) for i in range(start, end + 1))


def chunk_page(path: Path) -> list[dict]:
    """解析并切块一个 wiki 页面。

    返回 [{chunk_id, slug, title, kind, heading, text, line_start, line_end,
           embed_text, content_hash, chunk_index}],空页返回 []。
    """
    path = Path(path)
    try:
        fm, body = parse_text(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    slug = path.stem
    title = str(fm.get("title") or slug)
    kind = str(fm.get("kind") or "")
    aliases = " ".join(str(a) for a in (fm.get("aliases") or []))
    lines = body.splitlines()
    if not lines:
        return []

    # 1) 章节区间
    spans: list[tuple[str, int, int]] = []
    for heading, s, e in _split_sections(lines):
        if not any(lines[i - 1].strip() for i in range(s, e + 1)):
            continue  # 纯空白章节
        if _section_text_len(lines, s, e) <= SECTION_SPLIT_THRESHOLD:
            spans.append((heading, s, e))
            continue
        # 2) 超长章节按段落块再切;标题行归入第一个子块的区间
        content_start = s + 1 if heading and lines[s - 1].startswith("## ") else s
        blocks = _split_paragraph_blocks(lines, content_start, e)
        packed = _pack_blocks(lines, blocks)
        for i, (bs, be) in enumerate(packed):
            span_start = s if i == 0 else bs
            spans.append((heading, span_start, be))

    # 3) 生成 chunk 记录
    chunks: list[dict] = []
    for idx, (heading, s, e) in enumerate(spans):
        text_lines = [
            lines[i - 1]
            for i in range(s, e + 1)
            if not lines[i - 1].startswith("## ")
        ]
        text = "\n".join(text_lines).strip()
        if not text:
            # 章节只有标题没有正文:退化用标题行本身,保证行号可映射
            text = lines[s - 1].strip()
        head_label = heading or "引言"
        embed_text = f"{title} · {head_label}\n{text}"
        if idx == 0 and aliases:
            embed_text = f"{title} · {head_label}\n别名: {aliases}\n{text}"
        chunks.append(
            {
                "chunk_id": f"{slug}::{idx:02d}",
                "slug": slug,
                "title": title,
                "kind": kind,
                "heading": heading,
                "text": text,
                "line_start": s,
                "line_end": e,
                "chunk_index": idx,
                "embed_text": embed_text,
                "content_hash": _hash(embed_text),
            }
        )
    return chunks


def page_content_hash(chunks: list[dict]) -> str:
    """页级 content_hash = 全部 chunk hash 拼接再 hash(改造计划 §4.2)。"""
    return _hash("|".join(c["content_hash"] for c in chunks))


def chunk_page_cached(path: Path) -> list[dict]:
    """带 mtime + TTL 的 chunk_page,供 hybrid 词法腿高频调用。"""
    path = Path(path)
    key = str(path)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return []
    now = time.monotonic()
    cached = _CHUNK_CACHE.get(key)
    if cached and cached[1] == mtime_ns and now - cached[0] < _CHUNK_CACHE_TTL_SECONDS:
        return cached[2]
    chunks = chunk_page(path)
    _CHUNK_CACHE[key] = (now, mtime_ns, chunks)
    return chunks


def invalidate_chunk_cache() -> None:
    _CHUNK_CACHE.clear()
