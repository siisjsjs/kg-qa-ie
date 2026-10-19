#!/usr/bin/env python3
"""Mechanical wiki cleanup: scan + safe auto-fix.

Safe fixes:
  1) collapse consecutive identical nonempty lines (body + frontmatter)
  2) in '## 关键实体' / '## 抽取的实体' keep first - [[slug]] bullet per slug
  3) within each heading section, drop later exact-duplicate nonempty lines
  4) autolink existing entity/topic names (len>=4, first hit, skip headings/quotes/code)

Usage (inside kgqa-backend-1):
  python /app/data/wiki_cleanup.py --scan
  python /app/data/wiki_cleanup.py --apply
  python /app/data/wiki_cleanup.py --apply --resync
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

WIKI_ROOT = Path("/app/data/wiki")
WIKI_DIR = WIKI_ROOT / "wiki"
CONTENT_DIRS = ("entities", "topics", "sources", "queries", "synthesis", "comparisons")
REPORT_PATH = WIKI_ROOT / "_cleanup_report.json"

WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]]+?))?\]\]")
FM_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n?", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
KEY_ENTITY_HEAD_RE = re.compile(r"^#{1,6}\s+(?:关键实体|抽取的实体)\s*$")
BULLET_LINK_RE = re.compile(
    r"^(\s*[-*]\s+)\[\[([^\[\]|]+)(?:\|[^\]]+)?\]\](.*)$"
)
INNER_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
MATH_ONLY = frozenset({"$$", "\\[", "\\]"})


def fold_dashes(s: str) -> str:
    return s.replace("‐", "-").replace("‑", "-").replace("‒", "-").replace(
        "–", "-"
    ).replace("—", "-").replace("−", "-")


def split_fm(text: str) -> tuple[str, str]:
    m = FM_RE.match(text)
    if not m:
        return "", text
    return m.group(0), text[m.end() :]


def iter_pages() -> list[Path]:
    out: list[Path] = []
    for d in CONTENT_DIRS:
        p = WIKI_DIR / d
        if p.is_dir():
            out.extend(sorted(p.glob("*.md")))
    return out


def page_kind(path: Path) -> str:
    return path.parent.name


def existing_slugs() -> dict[str, str]:
    """slug -> kind dir name."""
    found: dict[str, str] = {}
    for p in iter_pages():
        found[fold_dashes(p.stem)] = page_kind(p)
    return found


def extract_targets(body: str) -> list[str]:
    out = []
    for m in WIKILINK_RE.finditer(body):
        out.append(fold_dashes(INNER_COMMENT_RE.sub("", m.group(1)).strip()))
    return out


def collapse_consecutive(body: str) -> tuple[str, int]:
    lines = body.splitlines(keepends=True)
    out: list[str] = []
    dropped = 0
    for ln in lines:
        raw = ln[:-1] if ln.endswith("\n") else ln
        if raw.endswith("\r"):
            raw = raw[:-1]
        prev_raw = ""
        if out:
            prev = out[-1]
            prev_raw = prev[:-1] if prev.endswith("\n") else prev
            if prev_raw.endswith("\r"):
                prev_raw = prev_raw[:-1]
        if raw.strip() and out and raw == prev_raw:
            dropped += 1
            continue
        out.append(ln)
    if body.endswith("\n") and out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    return "".join(out), dropped


def dedupe_section_exact_lines(body: str) -> tuple[str, int]:
    """Keep first occurrence of each nonempty line inside a heading section.

    Skips blank lines and math fence tokens ($$ / \\[ / \\]) so formulas stay intact.
    """
    lines = body.splitlines(keepends=True)
    out: list[str] = []
    dropped = 0
    seen: set[str] = set()
    for ln in lines:
        raw = ln.split("\n", 1)[0].rstrip("\r")
        if HEADING_RE.match(raw):
            seen = set()
            out.append(ln)
            continue
        stripped = raw.strip()
        if not stripped or stripped in MATH_ONLY:
            out.append(ln)
            continue
        if raw in seen:
            dropped += 1
            continue
        seen.add(raw)
        out.append(ln)
    return "".join(out), dropped


def dedupe_page_long_repeats(body: str) -> tuple[str, int]:
    """Drop later copies of the same blockquote or long line anywhere on the page."""
    lines = body.splitlines(keepends=True)
    out: list[str] = []
    dropped = 0
    seen: set[str] = set()
    for ln in lines:
        raw = ln.split("\n", 1)[0].rstrip("\r")
        stripped = raw.strip()
        if HEADING_RE.match(raw) or not stripped or stripped in MATH_ONLY:
            out.append(ln)
            continue
        if stripped.startswith(">") or len(stripped) >= 40:
            if raw in seen:
                dropped += 1
                continue
            seen.add(raw)
        out.append(ln)
    return "".join(out), dropped


def dedupe_key_entity_bullets(body: str) -> tuple[str, int, list[str]]:
    """Keep first - [[slug]] per slug inside 关键实体 sections. Returns (new, dropped, slugs)."""
    lines = body.splitlines(keepends=True)
    out: list[str] = []
    dropped = 0
    dropped_slugs: list[str] = []
    in_section = False
    seen: set[str] = set()
    for ln in lines:
        raw = ln.split("\n", 1)[0].rstrip("\r")
        if HEADING_RE.match(raw):
            in_section = bool(KEY_ENTITY_HEAD_RE.match(raw))
            seen = set()
            out.append(ln)
            continue
        if in_section:
            m = BULLET_LINK_RE.match(raw)
            if m:
                slug = fold_dashes(m.group(2).strip())
                if slug in seen:
                    dropped += 1
                    dropped_slugs.append(slug)
                    continue
                seen.add(slug)
        out.append(ln)
    return "".join(out), dropped, dropped_slugs


def strip_fragment_links(body: str, slugs: dict[str, str]) -> tuple[str, int]:
    """[[工业工程#E1]] -> [[工业工程]] when the base slug exists."""
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        raw = fold_dashes(INNER_COMMENT_RE.sub("", m.group(1)).strip())
        alias = m.group(2).strip() if m.group(2) else None
        if "#" not in raw:
            return m.group(0)
        base = raw.split("#", 1)[0].strip()
        if not base or base not in slugs:
            return m.group(0)
        n += 1
        if alias:
            return f"[[{base}|{alias}]]"
        return f"[[{base}]]"

    return WIKILINK_RE.sub(repl, body), n


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def _is_ascii_alnum(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _ok_boundary(text: str, start: int, end: int) -> bool:
    # Chinese running text has no spaces. Python treats CJK as alnum, so only
    # block ASCII mid-token matches (DMAIC inside XDMAICY), not 的工业工程.
    if start > 0 and _is_ascii_alnum(text[start - 1]) and _is_ascii_alnum(text[start]):
        return False
    if end < len(text) and _is_ascii_alnum(text[end - 1]) and _is_ascii_alnum(text[end]):
        return False
    return True


QUOTED_LINK_RE = re.compile(r"([“「\"'])\[\[([^\[\]|]+)(?:\|[^\]]+)?\]\]([”」\"'])")


NESTED_LINK_RE = re.compile(
    r"\[\[([^\[\]]*?)\[\[([^\[\]|]+)(?:\|[^\]]+)?\]\]([^\[\]]*?)\]\]"
)
# [[[slug]]](...) or [[[slug]]] → [[slug]]
TRIPLE_WRAP_RE = re.compile(
    r"\[\[\[([^\[\]]+?)\]\]\](?:\((?:\[\[[^\[\]]*\]\]|[^)]+)\))?"
)
# leftover extra opener: [[[slug]]
TRIPLE_OPEN_RE = re.compile(r"\[\[\[([^\[\]]+?)\]\]")
CJK_BOOKEND_RE = re.compile(r"\[\[\[\[([^\[\]]+)\]\]】")
CJK_CLOSE_RE = re.compile(r"\[\[([^\[\]]+?)】")
CLOSED_LINK_RE = re.compile(r"([^\[\]|]+)(\|[^\]]+)?\]\]")


def _unwrap_quad(text: str) -> tuple[str, int]:
    """[[[[A]]（[[B]]）]] -> [[A]]（[[B]]） by dropping one extra outer pair."""
    n = 0
    out: list[str] = []
    i = 0
    while True:
        j = text.find("[[[[", i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        p = j + 2
        depth = 0
        extra_close = None
        while p < len(text) - 1:
            pair = text[p : p + 2]
            if pair == "[[":
                depth += 1
                p += 2
                continue
            if pair == "]]":
                depth -= 1
                p += 2
                if depth < 0:
                    extra_close = p - 2
                    break
                continue
            p += 1
        if extra_close is None:
            out.append("[[[[")
            i = j + 4
            continue
        out.append(text[j + 2 : extra_close])
        n += 1
        i = extra_close + 2
    return "".join(out), n


def _close_unclosed_slugs(text: str, slugs: dict[str, str]) -> tuple[str, int]:
    """[[产品层次模型及相关...[[X]] -> [[产品层次模型]]及相关...[[X]]."""
    terms = sorted((s for s in slugs if len(s) >= 2), key=len, reverse=True)
    n = 0
    out: list[str] = []
    i = 0
    while True:
        j = text.find("[[", i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i : j + 2])
        rest = text[j + 2 :]
        m = CLOSED_LINK_RE.match(rest)
        if m:
            out.append(m.group(0))
            i = j + 2 + len(m.group(0))
            continue
        hit = next((s for s in terms if rest.startswith(s)), None)
        if hit:
            out.append(hit + "]]")
            n += 1
            i = j + 2 + len(hit)
        else:
            i = j + 2
    return "".join(out), n


def unnest_links(body: str, slugs: dict[str, str]) -> tuple[str, int]:
    """[[不[[增值活动]]]] -> [[不增值活动]] if that slug exists, else 不[[增值活动]]."""
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        prefix, inner, suffix = m.group(1), m.group(2).strip(), m.group(3)
        whole = fold_dashes((prefix + inner + suffix).strip())
        n += 1
        if whole in slugs:
            return f"[[{whole}]]"
        return f"{prefix}[[{inner}]]{suffix}"

    prev = None
    text = body
    extra = 0
    while prev != text:
        prev = text
        text = NESTED_LINK_RE.sub(repl, text)
        text, qn = _unwrap_quad(text)
        extra += qn

        def triple_repl(m: re.Match) -> str:
            nonlocal extra
            extra += 1
            return f"[[{m.group(1).strip()}]]"

        text = TRIPLE_WRAP_RE.sub(triple_repl, text)
        text = TRIPLE_OPEN_RE.sub(triple_repl, text)

        def book_repl(m: re.Match) -> str:
            nonlocal extra
            extra += 1
            return f"[[{m.group(1)}]]"

        text = CJK_BOOKEND_RE.sub(book_repl, text)

        def cjk_close(m: re.Match) -> str:
            nonlocal extra
            extra += 1
            slug = fold_dashes(m.group(1).strip())
            return f"[[{slug}]]"

        text = CJK_CLOSE_RE.sub(cjk_close, text)
        while "]]]]" in text:
            extra += text.count("]]]]")
            text = text.replace("]]]]", "]]")
        text, cn = _close_unclosed_slugs(text, slugs)
        extra += cn
    return text, n + extra


def unwrap_redundant_quoted_links(body: str) -> tuple[str, int]:
    """“[[双手操作图]]” -> “双手操作图” when the line already has [[双手操作图]]."""
    n = 0
    out: list[str] = []
    for ln in body.splitlines(keepends=True):
        raw = ln.split("\n", 1)[0].rstrip("\r")
        existing = {fold_dashes(t) for t in extract_targets(raw)}

        def repl(m: re.Match) -> str:
            nonlocal n
            slug = fold_dashes(m.group(2).strip())
            if slug in existing:
                n += 1
                return f"{m.group(1)}{m.group(2)}{m.group(3)}"
            return m.group(0)

        new_raw = QUOTED_LINK_RE.sub(repl, raw)
        if new_raw != raw:
            if ln.endswith("\r\n"):
                nl = "\r\n"
            elif ln.endswith("\n"):
                nl = "\n"
            else:
                nl = ""
            out.append(new_raw + nl)
        else:
            out.append(ln)
    return "".join(out), n


def _stash_wikilinks(text: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}

    def _repl(m: re.Match) -> str:
        token = f"\x00WL{len(placeholders)}\x00"
        placeholders[token] = m.group(0)
        return token

    return WIKILINK_RE.sub(_repl, text), placeholders


def _restore(text: str, placeholders: dict[str, str]) -> str:
    for token, original in placeholders.items():
        text = text.replace(token, original)
    return text


MAX_AUTOLINK_PER_PAGE = 15
AUTOLINK_KINDS = frozenset({"entities", "topics"})
STOP_TERMS = frozenset({
    "有效性",
    "产品质量",
    "服务质量",
    "制造过程",
    "管理标准",
    "质量评价",
    "质量目标",
    "现场管理",
    "现场改善",
})
SHORT_ALLOW = frozenset({
    "动素", "看板", "安灯", "5S", "SOP", "TPM", "OEE", "JIT", "VSM", "QFD", "SPC",
})


def autolink_body(body: str, terms: list[tuple[str, str]], self_slug: str) -> tuple[str, list[str]]:
    """Longest-match, first unlinked occurrence per canonical. Skip headings/quotes/code fences."""
    if not terms:
        return body, []
    lines = body.splitlines(keepends=True)
    linked: set[str] = set()
    if self_slug:
        linked.add(self_slug)
    hits: list[str] = []
    in_fence = False
    out: list[str] = []
    for ln in lines:
        if len(hits) >= MAX_AUTOLINK_PER_PAGE:
            out.append(ln)
            continue
        raw = ln.split("\n", 1)[0].rstrip("\r")
        stripped = raw.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(ln)
            continue
        if (
            in_fence
            or stripped.startswith("#")
            or stripped.startswith(">")
            or stripped.startswith("    ")
            or stripped.startswith("\t")
        ):
            out.append(ln)
            continue
        for target in extract_targets(raw):
            linked.add(target)
        stashed, ph = _stash_wikilinks(raw)
        changed = False
        for term, canonical in terms:
            if len(hits) >= MAX_AUTOLINK_PER_PAGE:
                break
            if canonical in linked or canonical == self_slug:
                continue
            if not term:
                continue
            idx = 0
            found = -1
            while True:
                found = stashed.find(term, idx)
                if found < 0:
                    break
                if _ok_boundary(stashed, found, found + len(term)):
                    break
                idx = found + 1
                found = -1
            if found < 0:
                continue
            token = f"\x00WL{len(ph)}\x00"
            ph[token] = f"[[{canonical}]]"
            stashed = stashed[:found] + token + stashed[found + len(term) :]
            linked.add(canonical)
            hits.append(canonical)
            changed = True
        if changed:
            raw2 = _restore(stashed, ph)
            if ln.endswith("\r\n"):
                nl = "\r\n"
            elif ln.endswith("\n"):
                nl = "\n"
            else:
                nl = ""
            out.append(raw2 + nl)
        else:
            out.append(ln)
    return "".join(out), hits


def build_terms(slugs: dict[str, str]) -> list[tuple[str, str]]:
    """(term, canonical) longest first. Entity+topic, len>=4, skip generic stop terms."""
    pairs: list[tuple[str, str]] = []
    for slug, kind in slugs.items():
        if kind not in ("entities", "topics"):
            continue
        if slug in STOP_TERMS:
            continue
        if len(slug) < 4 and slug not in SHORT_ALLOW:
            continue
        pairs.append((slug, slug))
    pairs.sort(key=lambda x: -len(x[0]))
    return pairs


def scan_one(path: Path, slugs: dict[str, str], terms: list[tuple[str, str]]) -> dict:
    text = path.read_text(encoding="utf-8")
    fm, body = split_fm(text)
    self_slug = fold_dashes(path.stem)
    fm, fm_n = collapse_consecutive(fm)
    consec_body, consec_n = collapse_consecutive(body)
    consec_n += fm_n
    ke_body, ke_n, ke_slugs = dedupe_key_entity_bullets(consec_body)
    sec_body, sec_n = dedupe_section_exact_lines(ke_body)
    page_body, page_n = dedupe_page_long_repeats(sec_body)
    sec_n += page_n
    ke_body2, ke_n2, ke_slugs2 = dedupe_key_entity_bullets(page_body)
    ke_n += ke_n2
    ke_slugs.extend(ke_slugs2)
    frag_body, frag_n = strip_fragment_links(ke_body2, slugs)
    unnested, nest_n = unnest_links(frag_body, slugs)
    unwrapped, unwrap_n = unwrap_redundant_quoted_links(unnested)
    use_terms = terms if page_kind(path) in AUTOLINK_KINDS else []
    auto_body, auto_hits = autolink_body(unwrapped, use_terms, self_slug)
    auto_body, ke_n3, ke_slugs3 = dedupe_key_entity_bullets(auto_body)
    ke_n += ke_n3
    ke_slugs.extend(ke_slugs3)
    auto_body, sec_n2 = dedupe_section_exact_lines(auto_body)
    auto_body, page_n2 = dedupe_page_long_repeats(auto_body)
    sec_n += sec_n2 + page_n2
    targets = extract_targets(auto_body)
    broken = [t for t in targets if t not in slugs]
    change_n = consec_n + ke_n + sec_n + frag_n + nest_n + unwrap_n + len(auto_hits)
    return {
        "path": str(path.relative_to(WIKI_ROOT)).replace("\\", "/"),
        "kind": page_kind(path),
        "slug": self_slug,
        "bytes": len(text.encode("utf-8")),
        "consecutive_dup_lines": consec_n,
        "section_exact_dups": sec_n,
        "key_entity_dup_bullets": ke_n,
        "key_entity_dup_slugs": Counter(ke_slugs).most_common(8),
        "fragment_fixed": frag_n,
        "nested_fixed": nest_n,
        "quoted_unwrapped": unwrap_n,
        "autolink_hits": auto_hits,
        "autolink_n": len(auto_hits),
        "broken_n": len(broken),
        "broken_sample": broken[:8],
        "would_change": change_n > 0,
        "new_body": auto_body if change_n > 0 else None,
        "fm": fm,
        "orig": text,
    }


def summarize(rows: list[dict]) -> dict:
    files_consec = [r for r in rows if r["consecutive_dup_lines"]]
    files_sec = [r for r in rows if r.get("section_exact_dups")]
    files_ke = [r for r in rows if r["key_entity_dup_bullets"]]
    files_auto = [r for r in rows if r["autolink_n"]]
    files_frag = [r for r in rows if r.get("fragment_fixed")]
    files_broken = [r for r in rows if r["broken_n"]]
    ke_slug_total = Counter()
    auto_total = Counter()
    broken_total = Counter()
    for r in rows:
        for slug, n in r["key_entity_dup_slugs"]:
            ke_slug_total[slug] += n
        for h in r["autolink_hits"]:
            auto_total[h] += 1
        for b in r["broken_sample"]:
            broken_total[b] += 1
    return {
        "pages": len(rows),
        "files_with_consecutive_dups": len(files_consec),
        "consecutive_dup_lines": sum(r["consecutive_dup_lines"] for r in rows),
        "files_with_section_exact_dups": len(files_sec),
        "section_exact_dups": sum(r.get("section_exact_dups", 0) for r in rows),
        "worst_section_dups": sorted(
            (
                {"path": r["path"], "n": r.get("section_exact_dups", 0)}
                for r in files_sec
            ),
            key=lambda x: -x["n"],
        )[:15],
        "files_with_key_entity_dups": len(files_ke),
        "key_entity_dup_bullets": sum(r["key_entity_dup_bullets"] for r in rows),
        "top_dup_slugs": ke_slug_total.most_common(20),
        "files_needing_autolink": len(files_auto),
        "autolink_inserts": sum(r["autolink_n"] for r in rows),
        "top_autolink_terms": auto_total.most_common(20),
        "files_with_fragment_links": len(files_frag),
        "fragment_fixed": sum(r.get("fragment_fixed", 0) for r in rows),
        "files_with_broken_links": len(files_broken),
        "broken_links": sum(r["broken_n"] for r in rows),
        "top_broken": broken_total.most_common(20),
        "files_that_would_change": sum(1 for r in rows if r["would_change"]),
        "worst_key_entity": sorted(
            (
                {
                    "path": r["path"],
                    "dup_bullets": r["key_entity_dup_bullets"],
                    "top": r["key_entity_dup_slugs"][:5],
                }
                for r in files_ke
            ),
            key=lambda x: -x["dup_bullets"],
        )[:15],
        "worst_autolink": sorted(
            (
                {"path": r["path"], "n": r["autolink_n"], "hits": r["autolink_hits"][:8]}
                for r in files_auto
            ),
            key=lambda x: -x["n"],
        )[:15],
    }


def apply_rows(rows: list[dict]) -> dict:
    written = 0
    bytes_before = 0
    bytes_after = 0
    for r in rows:
        if not r["would_change"] or r["new_body"] is None:
            continue
        new_text = r["fm"] + r["new_body"]
        if not new_text.endswith("\n"):
            new_text += "\n"
        path = WIKI_ROOT / r["path"]
        old = r["orig"]
        if new_text == old:
            continue
        bytes_before += len(old.encode("utf-8"))
        path.write_text(new_text, encoding="utf-8")
        bytes_after += len(new_text.encode("utf-8"))
        written += 1
    return {
        "written": written,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "bytes_saved": bytes_before - bytes_after,
    }


def resync() -> dict:
    sys.path.insert(0, "/app")
    from app.core.db import session_scope
    from app.models.wiki_page import WikiPage
    from app.models.wiki_link import WikiLink
    from app.services.wiki.link_resolver import iter_wiki_pages
    from app.services.ingestion.pipeline import _sync_db_indices
    from app.core.config import settings

    wiki_root = Path(settings.wiki_root_path)
    content_dirs = set(CONTENT_DIRS)
    rels = []
    for p in iter_wiki_pages(wiki_root):
        rel = str(p.relative_to(wiki_root)).replace("\\", "/")
        parts = rel.split("/")
        if len(parts) >= 2 and parts[0] == "wiki" and parts[1] in content_dirs:
            rels.append(rel)
    with session_scope() as db:
        n_pg = db.query(WikiPage).delete(synchronize_session=False)
        n_lk = db.query(WikiLink).delete(synchronize_session=False)
        db.flush()
        stat = _sync_db_indices(db, wiki_root=wiki_root, written_paths=rels)
        db.commit()
    return {"pages_cleared": n_pg, "links_cleared": n_lk, "resync": stat, "fs_pages": len(rels)}


async def rebuild_derived() -> dict:
    sys.path.insert(0, "/app")
    from app.core.config import settings
    from app.services.graph.builder import build_graph_data, write_graph_data_json
    from app.services.wiki.nav_map import write_nav_maps

    wiki_root = Path(settings.wiki_root_path)
    nav = await write_nav_maps(wiki_root)
    payload = await build_graph_data(wiki_root=wiki_root)
    await write_graph_data_json(wiki_root=wiki_root, payload=payload)
    return {
        "nav": nav,
        "graph_nodes": len(payload.get("nodes") or []),
        "graph_edges": len(payload.get("edges") or []),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--resync", action="store_true")
    ap.add_argument("--no-autolink", action="store_true")
    args = ap.parse_args()
    if not args.scan and not args.apply:
        args.scan = True

    slugs = existing_slugs()
    terms = [] if args.no_autolink else build_terms(slugs)
    pages = iter_pages()
    print(f"pages={len(pages)} slugs={len(slugs)} autolink_terms={len(terms)}", flush=True)

    rows = []
    for i, p in enumerate(pages, 1):
        if i % 200 == 0:
            print(f"  scanned {i}/{len(pages)}", flush=True)
        rows.append(scan_one(p, slugs, terms))

    summary = summarize(rows)
    serializable_rows = [
        {k: v for k, v in r.items() if k not in ("new_body", "fm", "orig")} for r in rows
    ]
    REPORT_PATH.write_text(
        json.dumps({"summary": summary, "rows": serializable_rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"report -> {REPORT_PATH}", flush=True)

    if args.apply:
        result = apply_rows(rows)
        print("APPLY", json.dumps(result, ensure_ascii=False), flush=True)
        if args.resync:
            print("resync db...", flush=True)
            print("RESYNC", json.dumps(resync(), ensure_ascii=False, default=str), flush=True)
            import asyncio

            print("rebuild graph+nav...", flush=True)
            derived = asyncio.run(rebuild_derived())
            print("DERIVED", json.dumps(derived, ensure_ascii=False, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
