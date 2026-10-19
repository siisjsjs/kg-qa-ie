"""digest/parsing.py:把 LLM 返回的 markdown 解析成结构化字典。

对应规划文档 §3.4.1 三模板各自骨架:
  - deep_dive:5 个 H2(背景概述/核心观点/不同视角对比/知识脉络/尚待解决的问题)
  - comparison:含 ## 对比表 段下的 markdown 表格
  - timeline:含 ## 事件说明 段下的若干 - {date} {event}:... 行
"""

from __future__ import annotations

import re

H2_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
CITATION_PATTERN = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\[\]]+?)?\]\]")
TABLE_ROW_PATTERN = re.compile(r"^\|(.+)\|\s*$")
SEPARATOR_ROW_PATTERN = re.compile(r"^\|\s*[-:|\s]+\|\s*$")


def split_sections(md: str) -> list[dict]:
    """按 H2 切分 markdown,返回 [{heading, content}]。

    H1 之前的内容(若有)作为 prelude 段,heading=''。
    """
    sections: list[dict] = []
    matches = list(H2_PATTERN.finditer(md))
    if not matches:
        return [{"heading": "", "content": md.strip()}]

    if matches[0].start() > 0:
        prelude = md[: matches[0].start()].strip()
        if prelude:
            sections.append({"heading": "", "content": prelude})

    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        content = md[start:end].strip()
        sections.append({"heading": heading, "content": content})
    return sections


def find_section(sections: list[dict], heading_substring: str) -> dict | None:
    for s in sections:
        if heading_substring in s.get("heading", ""):
            return s
    return None


def extract_citations(md: str) -> list[str]:
    """全文抽 [[X]] 去重保序,过滤掉模板示例 '页面A/B/X/Y'。"""
    placeholders = {"页面A", "页面B", "页面X", "页面Y", "页面1", "页面2", "对象1", "对象2"}
    seen: set[str] = set()
    out: list[str] = []
    for m in CITATION_PATTERN.finditer(md):
        slug = m.group(1).strip()
        if not slug or slug in seen:
            continue
        if slug in placeholders or slug.startswith("页面"):
            continue
        seen.add(slug)
        out.append(slug)
    return out


def parse_markdown_table(content: str) -> list[list[str]]:
    """从一段 markdown 中解析第一张表格,返回 [[cell, ...], ...](含表头)。"""
    rows: list[list[str]] = []
    in_table = False
    for line in content.splitlines():
        line = line.strip()
        if not line:
            if in_table:
                break
            continue
        if SEPARATOR_ROW_PATTERN.match(line):
            in_table = True
            continue
        m = TABLE_ROW_PATTERN.match(line)
        if not m:
            if in_table:
                break
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        rows.append(cells)
        in_table = True
    return rows


def is_blank_cell(cell: str) -> bool:
    """判断一个单元格是否为"空格"。"""
    s = (cell or "").strip()
    if not s:
        return True
    if s in {"-", "—", "—", "N/A", "n/a", "/", "无", "暂无", "待补充", "?", "？"}:
        return True
    if s.startswith("待补充") or s.startswith("> 待补充") or s.startswith("> 暂无"):
        return True
    return False


def parse_timeline_events(content: str) -> list[dict]:
    """从 ## 事件说明 段抽出 events。

    支持两种格式:
        - {YYYY|YYYY-MM} {事件}:{说明}(来源:[[页面X]])
        - **{YYYY}** {事件}:{说明}
    """
    events: list[dict] = []
    line_pattern = re.compile(
        r"""^[-*]\s*
            (?:\*\*)?
            (\d{4}(?:-\d{1,2}(?:-\d{1,2})?)?)
            (?:\*\*)?
            \s*[:：、\s]\s*
            (.+?)$""",
        re.VERBOSE,
    )
    for raw in (content or "").splitlines():
        line = raw.strip()
        m = line_pattern.match(line)
        if m:
            date_s = m.group(1)
            rest = m.group(2).strip()
            sources = [c.strip() for c in CITATION_PATTERN.findall(rest)]
            events.append({"date": date_s, "event": rest, "sources": sources})
    return events
