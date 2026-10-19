"""query/grep.py:在 wiki/ 下全文搜,按相关性排序。

排序规则(按规划文档 §3.3 流程 step 4):
  1. 文件名精确命中(slug 或 frontmatter.aliases 含关键词) — 最高加权
  2. index.md / overview.md 命中
  3. 正文命中次数

返回 [{slug, score, hit_segments: [{kw, line, text, ctx_before, ctx_after}]}, ...]
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from app.services.wiki.frontmatter_io import parse_text
from app.services.wiki.link_resolver import iter_wiki_pages

WEIGHT_FILENAME_EXACT = 100.0
WEIGHT_TITLE_OR_ALIAS = 50.0
WEIGHT_INDEX_MATCH = 20.0
WEIGHT_BODY_HIT = 1.0

# 自我产出的问答页(queries/comparisons/synthesis)不参与作答检索:
# 它们是"产出"而非"原始知识素材",回头当 context 喂给 LLM 会形成
# 自我污染——二手压缩内容拉低后续答案质量。
# 可被后续问答检索/引用的来源只有三类原始知识:entities / topics / sources。
# synthesis(timeline/deep_dive 类问答的落盘)同样是产出,照常持久化留痕、
# 参与图谱/反链/broken-link 等全量扫描,但在 query 检索这一步一律跳过。
# _growth 是系统自动维护的内部元数据(gap-queue 等,含用户原始提问/吐槽),
# 同样绝不参与检索——喂给模型会被误读为用户偏好/历史对话。
_GENERATED_SUBDIRS = frozenset({"queries", "comparisons", "synthesis", "_growth"})
_PAGE_CACHE_TTL_SECONDS = 300.0
_PAGE_CACHE: dict[str, tuple[float, list[tuple[Path, dict, str]]]] = {}


def _is_generated_page(page: Path, wiki_root: Path) -> bool:
    """判断页面是否落在生成类子目录(wiki/{queries|comparisons|synthesis}/...)。"""
    try:
        rel = page.relative_to(wiki_root).parts
    except ValueError:
        return False
    return len(rel) >= 2 and rel[0] == "wiki" and rel[1] in _GENERATED_SUBDIRS


def _build_kw_pattern(keywords: list[str]) -> re.Pattern[str] | None:
    """把多个 keywords 编译成一个 OR 正则。空列表返回 None。"""
    valid = [re.escape(k) for k in keywords if k.strip()]
    if not valid:
        return None
    valid.sort(key=len, reverse=True)
    return re.compile("|".join(valid), re.IGNORECASE)


def _load_pages(wiki_root: Path) -> list[tuple[Path, dict, str]]:
    """Cache parsed pages briefly so one QA request does not reread the entire wiki."""
    key = str(wiki_root)
    cached = _PAGE_CACHE.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _PAGE_CACHE_TTL_SECONDS:
        return cached[1]
    rows: list[tuple[Path, dict, str]] = []
    for page in iter_wiki_pages(wiki_root):
        if _is_generated_page(page, wiki_root):
            continue
        try:
            fm, body = parse_text(page.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append((page, fm, body))
    _PAGE_CACHE[key] = (now, rows)
    return rows


def retrievable_slugs(wiki_root: Path) -> set[str]:
    return {page.stem for page, _, _ in _load_pages(Path(wiki_root).resolve())}


def invalidate_page_cache(wiki_root: Path | None = None) -> None:
    if wiki_root is None:
        _PAGE_CACHE.clear()
    else:
        _PAGE_CACHE.pop(str(Path(wiki_root).resolve()), None)


async def grep_wiki(
    *,
    keywords: list[str],
    wiki_root: Path,
    top_k: int = 5,
    max_segments_per_keyword: int = 3,
    max_total_segments: int = 15,
) -> list[dict]:
    """对 wiki/**/*.md 全文搜索,按相关性排序。"""
    wiki_root = Path(wiki_root).resolve()
    pat = _build_kw_pattern(keywords)
    if pat is None:
        return []

    page_rows = _load_pages(wiki_root)
    pages = [row[0] for row in page_rows]
    results: list[dict] = []

    # 预读 index.md / overview.md 内容,用于 boost
    boost_targets: dict[str, str] = {}
    for fname in ("index.md", "overview.md"):
        p = wiki_root / fname
        if p.exists():
            boost_targets[fname] = p.read_text(encoding="utf-8")

    index_hit_slugs: set[str] = set()
    for fname, content in boost_targets.items():
        for m in pat.finditer(content):
            # 在 index/overview 命中的关键词,标记所有提到该 kw 的页面
            kw = m.group(0)
            for page in pages:
                if kw.lower() in page.stem.lower():
                    index_hit_slugs.add(page.stem)

    for page, fm, body in page_rows:

        slug = page.stem
        score = 0.0
        hit_segments: list[dict] = []

        # 1) 文件名 / 标题 / aliases 命中
        title = str(fm.get("title") or slug)
        aliases = fm.get("aliases") or []
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower == slug.lower() or kw_lower == title.lower():
                score += WEIGHT_FILENAME_EXACT
                break
            if any(kw_lower == str(a).lower() for a in aliases):
                score += WEIGHT_TITLE_OR_ALIAS
                break
            if kw_lower in slug.lower() or kw_lower in title.lower():
                score += WEIGHT_TITLE_OR_ALIAS / 2

        # 2) index/overview boost
        if slug in index_hit_slugs:
            score += WEIGHT_INDEX_MATCH

        # 3) 正文命中次数(按 keyword 限段数)
        body_lower = body.lower()
        if not any(keyword.strip().lower() in body_lower for keyword in keywords if keyword.strip()):
            if score > 0:
                results.append({
                    "slug": slug,
                    "score": round(score, 2),
                    "kind": fm.get("kind"),
                    "title": title,
                    "hit_segments": [],
                    "hit_lines": [],
                    "file_path": str(page.relative_to(wiki_root)).replace("\\", "/"),
                })
            continue
        lines = body.splitlines()
        per_kw_count: dict[str, int] = {}
        for line_no, line in enumerate(lines, start=1):
            for m in pat.finditer(line):
                kw = m.group(0)
                per_kw_count[kw] = per_kw_count.get(kw, 0) + 1
                if (
                    per_kw_count[kw] <= max_segments_per_keyword
                    and len(hit_segments) < max_total_segments
                ):
                    hit_segments.append({
                        "kw": kw,
                        "line": line_no,
                        "text": line.strip(),
                        "ctx_before": [
                            lines[line_no - 1 - i].strip()
                            for i in range(1, min(2, line_no))
                        ][::-1],
                        "ctx_after": [
                            lines[line_no - 1 + i].strip()
                            for i in range(1, min(3, len(lines) - line_no + 1))
                        ],
                    })
        body_hits = sum(per_kw_count.values())
        score += WEIGHT_BODY_HIT * body_hits

        if score > 0:
            results.append({
                "slug": slug,
                "score": round(score, 2),
                "kind": fm.get("kind"),
                "title": title,
                "hit_segments": hit_segments,
                "hit_lines": [s["line"] for s in hit_segments],
                "file_path": str(page.relative_to(wiki_root)).replace("\\", "/"),
            })

    results.sort(key=lambda x: (-x["score"], x["slug"]))
    return results[:top_k]
