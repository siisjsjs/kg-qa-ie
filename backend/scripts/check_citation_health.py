"""检查 wiki 页面是否有「引用格式」问题(粘连引用 / 裸 slug)。

用于新素材 ingest 后做体检。可选 --since 只看某时间点之后修改的页(查新页)。

用法:
    python scripts/check_citation_health.py              # 全库
    python scripts/check_citation_health.py --since-min 60   # 只看近 60 分钟改的页
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

WIKI = Path(__file__).resolve().parent.parent / "data" / "wiki" / "wiki"
# source 页可能落在 wiki/wiki/sources 或上一级 wiki/sources,两处都收。
SOURCES = {
    p.stem
    for d in (WIKI / "sources", WIKI.parent / "sources")
    if d.exists()
    for p in d.glob("*.md")
}

# 真粘连:> 前面是「可见字符」(正文/中文/标点),即引用粘在正文句尾。
# 行首的 `> ` 和列表项内缩进的 `   > `(> 前是空白)都是合法 blockquote,不算粘连。
INLINE_BQ = re.compile(r"(?<=[^\s>]) *> *(?=引自来源|⚠)")
# 裸 slug:引自来源: 后(跳过任意空格后)仍非 [ 字符 → 没有双链
BARE_SLUG = re.compile(r"引自来源:(?!\s*\[)")
# 一行内 2+ 引自来源(可能粘连)
MULTI = re.compile(r"引自来源.*引自来源")


def main() -> None:
    since = 0.0
    if "--since-min" in sys.argv:
        mins = float(sys.argv[sys.argv.index("--since-min") + 1])
        since = time.time() - mins * 60

    pages = 0
    bad_inline: list[tuple[str, int]] = []
    bad_bare: list[tuple[str, str]] = []
    bad_multi: list[str] = []

    for page in sorted(WIKI.rglob("*.md")):
        if page.parent.name == "sources":
            continue
        if since and page.stat().st_mtime < since:
            continue
        pages += 1
        rel = str(page.relative_to(WIKI))
        body = page.read_text(encoding="utf-8")

        n_inline = len(INLINE_BQ.findall(body))
        if n_inline:
            bad_inline.append((rel, n_inline))

        for m in BARE_SLUG.finditer(body):
            # 截到行尾再切首个 : / ：,拿到完整裸 slug(不要按固定字符数截,长 slug 会被切断)
            tail = body[m.end():].split("\n", 1)[0]
            ctx = re.split(r"[:：]", tail, 1)[0].strip()
            bad_bare.append((rel, ctx))

        for line in body.splitlines():
            if MULTI.search(line):
                bad_multi.append(rel)
                break

    print(f"检查页面数: {pages}")
    print(f"\n[粘连引用 行中>] {len(bad_inline)} 页:")
    for rel, n in bad_inline:
        print(f"  {rel}: {n} 处")
    print(f"\n[裸 slug 无双链] {len(bad_bare)} 处:")
    for rel, ctx in bad_bare:
        orphan = "" if ctx in SOURCES else "  <孤儿slug,无对应source>"
        print(f"  {rel}: 引自来源:{ctx}{orphan}")
    print(f"\n[一行多引用 可能粘连] {len(set(bad_multi))} 页:")
    for rel in sorted(set(bad_multi)):
        print(f"  {rel}")

    if not bad_inline and not bad_bare:
        print("\n>>> 无引用格式问题 <<<")


if __name__ == "__main__":
    main()
