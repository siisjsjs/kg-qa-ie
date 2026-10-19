"""query/composer.py:引用抽取/清单剥离等文本工具(Agentic 改造后)。

改造计划 §6.4:模板机制(build_prompt + 6 个 query_*.md)与 compose_stream/
compose_out_of_domain_stream/compose_supplement_stream 已废弃删除——主回答
由 agent.py + agent_system.md 生成。本模块仅保留:

- `render_context` / `render_alias_table`:digest 模块仍在用
- `CITATION_PATTERN` / `split_citation_footer` / `strip_generated_citation_footer`
- `extract_citations`:适配 chunk 引用协议([[slug#En]] 取 slug,web#Wn 不算)
"""

from __future__ import annotations

import re

from app.services.query.validation import extract_cited_slugs

DEFAULT_KIND = "general"


def render_context(context_pages: list[dict]) -> str:
    """把读页结果拼成 prompt 用的 markdown 区块(digest 模块使用)。"""
    if not context_pages:
        return "(无相关页面)"
    parts: list[str] = []
    for p in context_pages:
        header = f"### [[{p['slug']}]] · {p.get('kind') or '?'} · {p.get('title') or ''}"
        if p.get("truncated"):
            header += "  (已截断)"
        parts.append(f"{header}\n\n{p.get('body_excerpt') or ''}")
    return "\n\n---\n\n".join(parts)


def render_alias_table(alias_groups: list[list[str]]) -> str:
    if not alias_groups:
        return "(无)"
    return "\n".join("- " + " = ".join(g) for g in alias_groups[:30])


CITATION_PATTERN = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\[\]]+?)?\]\]")
_CITATION_HEADING_LINE = re.compile(
    r"^\s*(?:#{1,6}\s*(?:引用|引用来源)|\*\*(?:引用|引用来源)\*\*|引用(?:来源)?\s*[:：])\s*$",
    re.IGNORECASE,
)
_HEADING_LINE = re.compile(r"^\s*#{1,6}\s")
_LIST_MARKER = re.compile(r"^[\s>*+\-]*(?:\d+[.)、])?\s*")
_CITATION_SEPARATORS = re.compile(r"[\s,，;；/、]+")
_NO_CITATION_PLACEHOLDER = re.compile(r"^[(（]\s*本回答未引用素材\s*[)）]$")


def _is_citation_item(line: str) -> bool:
    """判断某行是否只是引用清单的一项(去掉列表符号后仅剩 [[slug]] 或占位说明)。"""
    body = _LIST_MARKER.sub("", line, count=1).strip()
    if not body:
        return False
    if _NO_CITATION_PLACEHOLDER.match(body):
        return True
    residue = _CITATION_SEPARATORS.sub("", CITATION_PATTERN.sub("", body))
    return not residue and bool(CITATION_PATTERN.search(body))


def split_citation_footer(answer: str) -> tuple[str, str]:
    """把答案拆成 (正文, 引用清单区块)。

    清单不要求位于全文末尾:定位「标题 + 紧随其后的连续引用行」区块。
    [[slug#En]] / [[web#Wn]] 形态同样被 CITATION_PATTERN 覆盖,无需特判。
    """
    lines = (answer or "").splitlines()
    spans: list[tuple[int, int]] = []
    footer_parts: list[str] = []
    i = 0
    while i < len(lines):
        if not _CITATION_HEADING_LINE.match(lines[i]):
            i += 1
            continue
        cursor = i + 1
        last_item = i
        while cursor < len(lines):
            line = lines[cursor]
            if _HEADING_LINE.match(line):
                break
            if not line.strip():
                cursor += 1
                continue
            if not _is_citation_item(line):
                break
            last_item = cursor
            cursor += 1
        has_item = last_item > i
        # 一项引用都没有、后面又跟着真实内容 → 这是正文里叫"引用"的小节,不能动
        if not has_item and cursor < len(lines):
            i += 1
            continue
        end = last_item if has_item else max(i, cursor - 1)
        spans.append((i, end))
        footer_parts.append("\n".join(lines[i:end + 1]).strip())
        i = end + 1

    if not spans:
        return (answer or "").rstrip(), ""

    kept: list[str] = []
    dropped = {n for start, end in spans for n in range(start, end + 1)}
    for idx, line in enumerate(lines):
        if idx in dropped:
            if kept and kept[-1].strip():
                kept.append("")
            continue
        kept.append(line)
    body = "\n".join(kept).strip()
    return body, "\n\n".join(p for p in footer_parts if p).strip()


def strip_generated_citation_footer(answer: str) -> str:
    """Hide the model's duplicate source list while preserving inline citations."""
    return split_citation_footer(answer)[0]


def extract_citations(answer: str) -> list[str]:
    """从答案抽 KB 页面 slug(去重保序)。

    chunk 引用协议下 [[slug#En]] 归并到 slug,[[web#Wn]] 不算 KB 引用;
    与 validation.extract_cited_slugs 保持同一套解析逻辑。
    """
    return extract_cited_slugs(answer)
