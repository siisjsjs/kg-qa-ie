"""wiki/version_history.py:节点版本历史 + diff 计算。

每次 wiki 页面写入(ingest / 手动编辑 / crystallize / merge)且 version bump 时,
写一条 wiki_node_versions 行记录:
  - 旧 → 新的 frontmatter / body diff
  - triggered_by 来源
  - actor_user_id(可选)
  - document_id(若由 ingest 触发)
  - title_snapshot(版本列表展示用)

diff 结构:
{
  "frontmatter": {
    "added":   {key: new_val, ...},
    "removed": {key: old_val, ...},
    "changed": {key: {old, new}, ...}
  },
  "body": {
    "stat": {"added_lines": int, "removed_lines": int},
    "unified": "<unified diff text, 限 max_chars 截断>"
  }
}
"""

from __future__ import annotations

import difflib
import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.wiki_node_version import WikiNodeVersion


_DIFF_MAX_CHARS = 8000  # 单次 diff 文本上限


def _frontmatter_diff(old: dict, new: dict) -> dict[str, Any]:
    old_keys = set(old.keys())
    new_keys = set(new.keys())
    added = {k: new[k] for k in new_keys - old_keys}
    removed = {k: old[k] for k in old_keys - new_keys}
    changed: dict[str, dict] = {}
    for k in old_keys & new_keys:
        # updated/created 这种时间戳类字段不进 diff(避免噪声)
        if k in ("updated", "created"):
            continue
        if old.get(k) != new.get(k):
            changed[k] = {"old": old[k], "new": new[k]}
    return {"added": added, "removed": removed, "changed": changed}


def _body_diff(old_body: str, new_body: str) -> dict[str, Any]:
    old_lines = old_body.splitlines(keepends=True)
    new_lines = new_body.splitlines(keepends=True)
    diff_iter = difflib.unified_diff(
        old_lines, new_lines, fromfile="old", tofile="new", lineterm=""
    )
    unified_lines: list[str] = list(diff_iter)
    unified_text = "".join(unified_lines)
    if len(unified_text) > _DIFF_MAX_CHARS:
        unified_text = unified_text[:_DIFF_MAX_CHARS] + "\n…(truncated)"
    added = sum(
        1
        for line in unified_lines
        if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1
        for line in unified_lines
        if line.startswith("-") and not line.startswith("---")
    )
    return {
        "stat": {"added_lines": added, "removed_lines": removed},
        "unified": unified_text,
    }


def compute_diff(
    *,
    old_fm: dict,
    old_body: str,
    new_fm: dict,
    new_body: str,
) -> dict[str, Any]:
    """计算 frontmatter + body 的结构化 diff。"""
    return {
        "frontmatter": _frontmatter_diff(old_fm or {}, new_fm or {}),
        "body": _body_diff(old_body or "", new_body or ""),
    }


def write_version(
    db: Session,
    *,
    slug: str,
    version: int,
    diff: dict[str, Any],
    triggered_by: str,
    title_snapshot: str | None = None,
    document_id: int | None = None,
    actor_user_id: int | None = None,
) -> WikiNodeVersion:
    """写一条 wiki_node_versions 行。

    triggered_by ∈ ingest / manual / merge / crystallize / delete
    """
    row = WikiNodeVersion(
        slug=slug,
        version=int(version),
        diff_json=json.dumps(diff, ensure_ascii=False, default=str),
        triggered_by=triggered_by,
        title_snapshot=title_snapshot,
        document_id=document_id,
        actor_user_id=actor_user_id,
    )
    db.add(row)
    db.flush()
    return row


def list_versions(
    db: Session,
    *,
    slug: str,
    limit: int = 50,
) -> list[WikiNodeVersion]:
    """按 version desc 列出某 slug 的版本历史。"""
    return (
        db.query(WikiNodeVersion)
        .filter(WikiNodeVersion.slug == slug)
        .order_by(WikiNodeVersion.version.desc())
        .limit(limit)
        .all()
    )


def get_version(
    db: Session, *, slug: str, version: int
) -> WikiNodeVersion | None:
    return (
        db.query(WikiNodeVersion)
        .filter(WikiNodeVersion.slug == slug, WikiNodeVersion.version == version)
        .one_or_none()
    )


def diff_stat(diff_json_str: str) -> dict[str, Any]:
    """从 diff_json 提取摘要;给版本列表展示用。"""
    try:
        diff = json.loads(diff_json_str)
    except (json.JSONDecodeError, TypeError):
        return {}
    body_stat = (diff.get("body") or {}).get("stat") or {}
    fm_diff = diff.get("frontmatter") or {}
    return {
        "added_lines": int(body_stat.get("added_lines") or 0),
        "removed_lines": int(body_stat.get("removed_lines") or 0),
        "fm_added": len(fm_diff.get("added") or {}),
        "fm_removed": len(fm_diff.get("removed") or {}),
        "fm_changed": len(fm_diff.get("changed") or {}),
    }
