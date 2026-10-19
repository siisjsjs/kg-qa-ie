"""wiki/frontmatter_io.py：YAML frontmatter 解析与回写。

基于 python-frontmatter,但回写时强制字段顺序稳定(便于 git diff)。
"""

from __future__ import annotations

from collections.abc import Hashable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
import yaml

# 字段顺序:常用字段排前面,其他按字典序排后面
_PREFERRED_ORDER = (
    "title",
    "slug",
    "kind",
    "alias_of",
    "aliases",
    "tags",
    "domains",
    "sources",
    "book",
    "order",
    "version",
    "confidence",
    "answer_quality",
    "tier",
    "gap_flag",
    "user_question",
    "followups",
    "created",
    "updated",
)

# list 类型字段:merge 时取并集
_LIST_UNION_FIELDS = frozenset({"aliases", "tags", "domains", "sources", "followups"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_file(path: Path) -> tuple[dict, str]:
    """读 md 文件,返回 (frontmatter_dict, body)。

    无 frontmatter 时 frontmatter_dict = {} 且 body 为整篇文本。
    """
    text = Path(path).read_text(encoding="utf-8")
    post = frontmatter.loads(text)
    return dict(post.metadata), post.content


def parse_text(text: str) -> tuple[dict, str]:
    """从字符串解析(便于测试)。"""
    post = frontmatter.loads(text)
    return dict(post.metadata), post.content


def _ordered(fm: dict[str, Any]) -> dict[str, Any]:
    """按 _PREFERRED_ORDER 重排字段。"""
    out: dict[str, Any] = {}
    for key in _PREFERRED_ORDER:
        if key in fm:
            out[key] = fm[key]
    for key in sorted(fm.keys()):
        if key not in out:
            out[key] = fm[key]
    return out


def serialize(frontmatter_dict: dict, body: str) -> str:
    """frontmatter + body → 完整 md 文本(YAML 字段顺序稳定)。

    body 末尾保证有且仅有一个 `\\n`。
    """
    ordered = _ordered(dict(frontmatter_dict))
    yaml_text = yaml.safe_dump(
        ordered,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip()

    body_clean = body.rstrip("\n")
    if not body_clean:
        return f"---\n{yaml_text}\n---\n"
    return f"---\n{yaml_text}\n---\n\n{body_clean}\n"


def merge_frontmatter(old: dict, new: dict, *, bump_version: bool = True) -> dict:
    """合并 frontmatter:list 字段去重 union;scalar 字段 new 覆盖 old;version +1。

    - `_LIST_UNION_FIELDS` 中的字段:`old + new` 去重保序合并(old 在前)
    - 其他字段:`new` 优先,缺失时用 `old`
    - `version`:bump_version=True 时 +1(基于 old.version,默认 1)
    - `updated`:总是刷为当前 ISO 时间戳
    - `created`:仅当 old 未设置才写入
    """
    merged: dict[str, Any] = {}
    keys = set(old.keys()) | set(new.keys())

    for key in keys:
        if key == "version":
            continue
        if key in ("updated", "created"):
            continue
        if key in _LIST_UNION_FIELDS:
            old_list = old.get(key) or []
            new_list = new.get(key) or []
            seen: set = set()
            union: list = []
            for raw in list(old_list) + list(new_list):
                # 这些字段应为字符串标量列表;LLM 偶尔误产出嵌套 list,
                # 摊平一层并丢弃不可哈希残渣,避免 `item not in seen` 抛 TypeError。
                items = raw if isinstance(raw, list) else [raw]
                for item in items:
                    if not isinstance(item, Hashable):
                        continue
                    if item not in seen:
                        seen.add(item)
                        union.append(item)
            if union:
                merged[key] = union
        else:
            if key in new and new[key] is not None:
                merged[key] = new[key]
            elif key in old:
                merged[key] = old[key]

    if bump_version:
        old_version = int(old.get("version") or 1)
        merged["version"] = old_version + 1
    elif "version" in old:
        merged["version"] = old["version"]

    merged["created"] = old.get("created") or new.get("created") or _now_iso()
    merged["updated"] = _now_iso()

    return merged
