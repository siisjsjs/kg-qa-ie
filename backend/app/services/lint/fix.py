"""lint/fix.py:可自动修复的健康问题。

仅做"低风险"修复(不动 LLM):
  - 重新生成 index.md(列出 entities / topics 全集)
  - 把 broken [[X]] 替换为 ~~[[X]]~~ 或纯文本 X(可选,默认不改正文)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.services.wiki.frontmatter_io import parse_text
from app.services.wiki.fs_io import atomic_write
from app.services.wiki.link_resolver import iter_wiki_pages

KIND_ORDER = ("entities", "topics", "synthesis", "comparisons", "sources", "queries")


def _kind_label(name: str) -> str:
    return {
        "entities": "实体",
        "topics": "主题",
        "sources": "素材",
        "queries": "查询",
        "synthesis": "综合",
        "comparisons": "对比",
    }.get(name, name)


def regenerate_index(wiki_root: Path) -> Path:
    """根据 wiki/<kind>/ 实际文件重写 wiki/index.md。返回写入路径。"""
    wiki_root = Path(wiki_root)
    target = wiki_root / "index.md"

    by_kind: dict[str, list[tuple[str, str]]] = {}  # kind → [(slug, title)]
    for kind in KIND_ORDER:
        d = wiki_root / "wiki" / kind
        if not d.exists():
            continue
        bucket: list[tuple[str, str]] = []
        for p in sorted(d.glob("*.md")):
            try:
                fm, _ = parse_text(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            slug = p.stem
            title = str(fm.get("title") or slug)
            bucket.append((slug, title))
        if bucket:
            by_kind[kind] = bucket

    now = datetime.now(timezone.utc).isoformat()
    lines: list[str] = [
        "---",
        "title: 知识库索引",
        "slug: index",
        "kind: index",
        f"updated: {now}",
        "---",
        "",
        "# 知识库索引",
        "",
        f"> 自动生成于 {now}",
        "",
        "## 统计",
        "",
    ]
    total = sum(len(v) for v in by_kind.values())
    lines.append(f"- 总页数:{total}")
    for kind in KIND_ORDER:
        if kind in by_kind:
            lines.append(f"- {_kind_label(kind)}:{len(by_kind[kind])}")
    lines.append("")

    for kind in KIND_ORDER:
        if kind not in by_kind:
            continue
        lines.append(f"## {_kind_label(kind)}")
        lines.append("")
        for slug, title in by_kind[kind]:
            if title and title != slug:
                lines.append(f"- [[{slug}|{title}]]")
            else:
                lines.append(f"- [[{slug}]]")
        lines.append("")

    target.write_text("\n".join(lines), encoding="utf-8")
    return target


async def fix(*, wiki_root: Path, kinds: list[str] | None = None) -> dict:
    """运行可自动修复的项,返回 {regenerated_index, broken_links_neutralized, ...}."""
    wiki_root = Path(wiki_root).resolve()
    requested = set(kinds or ["index"])
    out: dict = {"applied": [], "skipped": []}

    if "index" in requested:
        idx = regenerate_index(wiki_root)
        out["applied"].append({"kind": "regenerated_index", "path": str(idx)})
    else:
        out["skipped"].append("index")

    return out
