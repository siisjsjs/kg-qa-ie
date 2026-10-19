"""admin/purpose_editor.py:purpose.md 解析与回写。

格式:
  # 研究目标
  自由段
  # 关键问题
  1. xxx
  2. xxx
  # 研究范围
  ## 在范围内
  - xxx
  ## 暂不深入
  - xxx
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.wiki.fs_io import atomic_write

PURPOSE_FILE = "purpose.md"


def _parse_section_blocks(text: str) -> dict[str, str]:
    """切 H1 段:返回 {标题: body}。"""
    blocks: dict[str, str] = {}
    cur: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("---"):
            continue
        m = re.match(r"^#\s+(.+?)\s*$", line)
        if m:
            if cur is not None:
                blocks[cur] = "\n".join(buf).strip()
            cur = m.group(1).strip()
            buf = []
        else:
            if cur is not None:
                buf.append(line)
    if cur is not None:
        blocks[cur] = "\n".join(buf).strip()
    return blocks


def _parse_h2_blocks(body: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    cur: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if cur is not None:
                blocks[cur] = "\n".join(buf).strip()
            cur = m.group(1).strip()
            buf = []
        else:
            if cur is not None:
                buf.append(line)
    if cur is not None:
        blocks[cur] = "\n".join(buf).strip()
    return blocks


def _parse_list_items(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        m = re.match(r"^[-*]\s+(.+)$", s)
        if m:
            out.append(m.group(1).strip())
            continue
        m = re.match(r"^\d+\.\s+(.+)$", s)
        if m:
            out.append(m.group(1).strip())
    return out


async def read_purpose(wiki_root: Path) -> dict:
    p = Path(wiki_root) / PURPOSE_FILE
    raw = p.read_text(encoding="utf-8") if p.exists() else ""
    h1 = _parse_section_blocks(raw)
    goals_text = h1.get("研究目标", "")
    questions_text = h1.get("关键问题", "")
    scope_text = h1.get("研究范围", "")
    scope_h2 = _parse_h2_blocks(scope_text)
    return {
        "raw_markdown": raw,
        "research_goals": [s for s in goals_text.splitlines() if s.strip()],
        "key_questions": _parse_list_items(questions_text),
        "in_scope": _parse_list_items(scope_h2.get("在范围内", "")),
        "out_of_scope": _parse_list_items(scope_h2.get("暂不深入", "")),
    }


def render_purpose(payload: dict) -> str:
    raw = payload.get("raw_markdown")
    if raw and isinstance(raw, str) and raw.strip():
        return raw
    goals = "\n\n".join(payload.get("research_goals") or []) or "_(待补充)_"
    questions = (
        "\n".join(f"{i + 1}. {q}" for i, q in enumerate(payload.get("key_questions") or []))
        or "_(待补充)_"
    )
    in_scope = (
        "\n".join(f"- {x}" for x in payload.get("in_scope") or []) or "_(待补充)_"
    )
    out_scope = (
        "\n".join(f"- {x}" for x in payload.get("out_of_scope") or []) or "_(待补充)_"
    )
    return (
        "---\n"
        "title: 研究目的\nslug: purpose\nkind: purpose\n"
        "---\n\n"
        f"# 研究目标\n\n{goals}\n\n"
        f"# 关键问题\n\n{questions}\n\n"
        "# 研究范围\n\n"
        f"## 在范围内\n\n{in_scope}\n\n"
        f"## 暂不深入\n\n{out_scope}\n"
    )


async def write_purpose(*, wiki_root: Path, payload: dict) -> None:
    text = render_purpose(payload)
    p = Path(wiki_root) / PURPOSE_FILE
    await atomic_write(p, text)
