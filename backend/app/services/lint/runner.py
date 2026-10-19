"""lint/runner.py:健康检查 runner。

对应原 scripts/lint-runner.sh。

检查项:
  - orphan:孤立页(无入边无出边)
  - broken_link:[[X]] 指向不存在的 slug
  - duplicate:标题相同的 entity
  - low_confidence:frontmatter.confidence='AMBIGUOUS' 或 confidence_score < 阈值
  - contradiction:页面 frontmatter.contradictions 非空
  - index_inconsistency:wiki/index.md 没列到某 wiki 页 / 列了不存在的页
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from app.services.wiki.frontmatter_io import parse_text
from app.services.wiki.link_resolver import (
    aggregate_edges,
    iter_wiki_pages,
    rebuild_links_index,
)


CONFIDENCE_LOW_THRESHOLD = 0.5


def _kind_from_path(rel_parts: tuple[str, ...]) -> str | None:
    if len(rel_parts) >= 2 and rel_parts[0] == "wiki":
        return {
            "entities": "entity",
            "topics": "topic",
            "sources": "source",
            "queries": "query",
            "synthesis": "synthesis",
            "comparisons": "comparison",
        }.get(rel_parts[1])
    return None


async def run_lint(*, wiki_root: Path) -> dict:
    """跑全量 lint 检查。

    Returns:
        {issues: [{kind, slug, detail, severity}], summary: {<kind>: count}}
    """
    wiki_root = Path(wiki_root).resolve()
    issues: list[dict] = []

    pages = iter_wiki_pages(wiki_root)
    page_by_slug: dict[str, dict] = {}
    for p in pages:
        try:
            fm, body = parse_text(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        slug = p.stem
        page_by_slug[slug] = {
            "fm": fm,
            "body": body,
            "kind": _kind_from_path(p.relative_to(wiki_root).parts),
            "path": p,
        }

    existing = set(page_by_slug.keys())

    # 1) 边
    raw_edges = rebuild_links_index(wiki_root)
    incoming: dict[str, int] = defaultdict(int)
    outgoing: dict[str, int] = defaultdict(int)
    for e in aggregate_edges(raw_edges):
        outgoing[e["src_slug"]] += int(e.get("occurrences") or 1)
        incoming[e["dst_slug"]] += int(e.get("occurrences") or 1)

    # 2) orphan(entity / topic 类才计孤立,sources/queries 不算)
    for slug, info in page_by_slug.items():
        if info["kind"] not in ("entity", "topic"):
            continue
        if outgoing.get(slug, 0) == 0 and incoming.get(slug, 0) == 0:
            issues.append({
                "kind": "orphan",
                "slug": slug,
                "detail": f"孤立页(无入边无出边):{info['kind']}",
                "severity": "warning",
            })

    # 3) broken_link
    seen_broken: set[tuple[str, str]] = set()
    for e in raw_edges:
        dst = e["dst_slug"]
        if dst in existing:
            continue
        key = (e["src_slug"], dst)
        if key in seen_broken:
            continue
        seen_broken.add(key)
        issues.append({
            "kind": "broken_link",
            "slug": e["src_slug"],
            "detail": f"指向不存在的 [[{dst}]]",
            "severity": "error",
        })

    # 4) duplicate(标题相同的 entity)
    title_to_slugs: dict[str, list[str]] = defaultdict(list)
    for slug, info in page_by_slug.items():
        if info["kind"] != "entity":
            continue
        title = str(info["fm"].get("title") or slug).strip()
        title_to_slugs[title].append(slug)
    for title, slugs in title_to_slugs.items():
        if len(slugs) > 1:
            for s in slugs:
                issues.append({
                    "kind": "duplicate",
                    "slug": s,
                    "detail": f"标题 '{title}' 在多个 entity 页存在:{', '.join(slugs)}",
                    "severity": "warning",
                })

    # 5) low_confidence
    for slug, info in page_by_slug.items():
        fm = info["fm"]
        raw = fm.get("confidence")
        if isinstance(raw, str) and raw.upper() == "AMBIGUOUS":
            issues.append({
                "kind": "low_confidence",
                "slug": slug,
                "detail": "confidence=AMBIGUOUS",
                "severity": "info",
            })
        elif isinstance(raw, (int, float)) and float(raw) < CONFIDENCE_LOW_THRESHOLD:
            issues.append({
                "kind": "low_confidence",
                "slug": slug,
                "detail": f"confidence_score={raw} < {CONFIDENCE_LOW_THRESHOLD}",
                "severity": "info",
            })

    # 6) contradiction
    for slug, info in page_by_slug.items():
        contradictions = info["fm"].get("contradictions") or []
        if contradictions:
            issues.append({
                "kind": "contradiction",
                "slug": slug,
                "detail": f"frontmatter.contradictions 非空(共 {len(contradictions)} 项)",
                "severity": "warning",
            })
        if "## 不同素材中的观点" in info["body"] and "⚠ 与来源" in info["body"]:
            issues.append({
                "kind": "contradiction",
                "slug": slug,
                "detail": "正文出现 '⚠ 与来源' 冲突标记",
                "severity": "warning",
            })

    # 7) index_inconsistency
    index_path = wiki_root / "index.md"
    if index_path.exists():
        try:
            index_text = index_path.read_text(encoding="utf-8")
        except Exception:
            index_text = ""
        # 简化:抽 [[X]] 列表与 wiki 页 slugs 比对
        from app.services.wiki.link_resolver import extract_links

        listed = {target for target, _ in extract_links(index_text)}
        for missing in (existing - listed):
            kind = page_by_slug[missing]["kind"]
            if kind in ("entity", "topic"):
                issues.append({
                    "kind": "index_inconsistency",
                    "slug": missing,
                    "detail": f"页面未列在 index.md(kind={kind})",
                    "severity": "info",
                })
        for stale in (listed - existing):
            issues.append({
                "kind": "index_inconsistency",
                "slug": stale,
                "detail": "index.md 列了不存在的页",
                "severity": "warning",
            })

    summary: dict[str, int] = defaultdict(int)
    for it in issues:
        summary[it["kind"]] += 1

    return {"issues": issues, "summary": dict(summary)}
