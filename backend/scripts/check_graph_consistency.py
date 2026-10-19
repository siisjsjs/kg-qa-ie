"""三方一致性校验:节点页结构化字段 vs wiki_links(权威) vs 正文 [[]] 链接。

检查项:
  1. source 页「## 抽取的实体/主题」清单  vs  该 source 实际贡献的页(frontmatter sources 命中)
  2. topic 页 frontmatter related_entities  vs  正文 [[]] 出链  vs  wiki_links 出边
  3. 正文 [[]] 链接是否都指向真实存在的页(虚空检查)

权威基准 = wiki_links 表 + 正文实际 [[]] 链接(两者应一致)。
frontmatter / source 清单 应向权威基准对齐;对不上即报。

用法:
    python scripts/check_graph_consistency.py            # 全量
    python scripts/check_graph_consistency.py --strict   # 有不一致则退出码=1(供CI/pipeline用)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pymysql

WIKI = Path(__file__).resolve().parent.parent / "data" / "wiki" / "wiki"

DB = dict(host="127.0.0.1", port=3309, user="kgqa", password="CHANGE_ME", database="kg_qa_ie", charset="utf8mb4")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _conn():
    return pymysql.connect(**DB)


def _split_fm_body(text: str):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if m:
        return m.group(1), m.group(2)
    return "", text


def _fm_list(fm: str, key: str) -> list[str]:
    """从 frontmatter 抓 `key:` 下的 `- item` 列表。"""
    out = []
    if f"{key}:" not in fm:
        return out
    block = fm.split(f"{key}:", 1)[1]
    for line in block.splitlines():
        m = re.match(r"\s*-\s*(.+?)\s*$", line)
        if m:
            out.append(m.group(1).strip())
        elif line.strip() and not line[0].isspace():
            break
    return out


def _fm_scalar(fm: str, key: str) -> str | None:
    """从 frontmatter 抓 `key: value` 的标量值。"""
    m = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", fm, re.M)
    return m.group(1).strip() if m else None


def _section_links(body: str, header: str) -> list[str]:
    """抓 `## header` 段下的 [[xxx]]。"""
    m = re.search(r"##\s*" + re.escape(header) + r"\s*\n(.*?)(?=\n##\s|\Z)", body, re.S)
    if not m:
        return []
    return [x.split("|")[0].strip() for x in LINK_RE.findall(m.group(1))]


def _body_links(body: str) -> set[str]:
    """正文所有 [[]] 链接(排除日期开头的 source 引用)。"""
    out = set()
    for m in LINK_RE.finditer(body):
        t = m.group(1).split("|")[0].strip()
        if not DATE_RE.match(t):
            out.add(t)
    return out


def main() -> None:
    strict = "--strict" in sys.argv
    conn = _conn()
    cur = conn.cursor()

    cur.execute("SELECT slug, kind FROM wiki_pages")
    page_kind = {r[0]: r[1] for r in cur.fetchall()}
    all_slugs = set(page_kind)

    # wiki_links 出边索引
    cur.execute("SELECT src_slug, dst_slug FROM wiki_links")
    out_edges: dict[str, set[str]] = {}
    for s, d in cur.fetchall():
        out_edges.setdefault(s, set()).add(d)
    conn.close()

    problems = 0

    # ---- 1. topic: frontmatter related_entities vs 正文 vs wiki_links ----
    print("=" * 70)
    print("[1] TOPIC 节点:related_entities(fm) vs 正文[[]] vs wiki_links")
    print("=" * 70)
    for f in sorted((WIKI / "topics").glob("*.md")):
        slug = f.stem
        fm, body = _split_fm_body(f.read_text(encoding="utf-8"))
        fm_ents = {e for e in _fm_list(fm, "related_entities") if not DATE_RE.match(e)}
        body_lk = _body_links(body)
        db_out = {d for d in out_edges.get(slug, set()) if not DATE_RE.match(d)}
        # 权威 = body_lk(应与 db_out 一致)
        fm_vs_body = fm_ents ^ body_lk
        body_vs_db = body_lk ^ db_out
        if fm_vs_body or body_vs_db:
            problems += 1
            print(f"\n  {slug}:")
            print(f"    fm={len(fm_ents)} 正文={len(body_lk)} wiki_links={len(db_out)}")
            if fm_ents - body_lk:
                print(f"    fm多出(正文没): {sorted(fm_ents - body_lk)}")
            if body_lk - fm_ents:
                print(f"    正文多出(fm没回写): {sorted(body_lk - fm_ents)}")
            if body_vs_db:
                print(f"    正文vs wiki_links 不一致: {sorted(body_vs_db)}")

    # ---- 2. source: 抽取清单 vs 实际贡献页 ----
    print("\n" + "=" * 70)
    print("[2] SOURCE 节点:抽取清单 vs 实际关联页(frontmatter sources 命中)")
    print("=" * 70)
    # 建 source -> 贡献页 的反向索引(读各页 frontmatter 的 sources 字段)
    src_contrib: dict[str, set[str]] = {}
    for f in WIKI.rglob("*.md"):
        if f.parent.name == "sources":
            continue
        fm, _ = _split_fm_body(f.read_text(encoding="utf-8"))
        for src in _fm_list(fm, "sources"):
            src_contrib.setdefault(src.strip(), set()).add(f.stem)

    for f in sorted((WIKI / "sources").glob("*.md")):
        slug = f.stem
        fm, body = _split_fm_body(f.read_text(encoding="utf-8"))
        actual = src_contrib.get(slug, set())
        actual_ents = sorted(s for s in actual if page_kind.get(s) == "entity")
        actual_tops = sorted(s for s in actual if page_kind.get(s) == "topic")
        # 权威字段:由 pipeline 回写的 contributed_* 统计(int)
        def _as_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
        fm_ce = _as_int(_fm_scalar(fm, "contributed_entity_count"))
        fm_ct = _as_int(_fm_scalar(fm, "contributed_topic_count"))
        if fm_ce != len(actual_ents) or fm_ct != len(actual_tops):
            problems += 1
            print(f"\n  {slug}:")
            print(f"    权威字段 entity={fm_ce} topic={fm_ct}  实际贡献页 entity={len(actual_ents)} topic={len(actual_tops)}")
            print(f"    => contributed_* 未回写或滞后,需重跑 resync_frontmatter_now")
        else:
            print(f"\n  {slug}: OK (entity={fm_ce} topic={fm_ct} 与实际贡献页一致)")

    # ---- 3. 全库虚空链接 ----
    print("\n" + "=" * 70)
    print("[3] 虚空链接检查(正文[[]]指向不存在的页)")
    print("=" * 70)
    void = 0
    for f in WIKI.rglob("*.md"):
        body = f.read_text(encoding="utf-8")
        for m in LINK_RE.finditer(body):
            t = m.group(1).split("|")[0].strip()
            if t not in all_slugs:
                void += 1
                print(f"  {f.stem} -> {t}")
    if void == 0:
        print("  无虚空链接")

    print("\n" + "=" * 70)
    print(f"汇总: 结构化字段不一致节点 {problems} 个, 虚空链接 {void} 条")
    print("=" * 70)

    if strict and (problems or void):
        sys.exit(1)


if __name__ == "__main__":
    main()
