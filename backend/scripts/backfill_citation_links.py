"""回填裸 slug 引用为双链:引自来源:<slug> -> 引自来源:[[<slug>]]。

只对真实存在的 source slug 做精确锚定替换(slug 边界由文件名确定,不靠正则猜)。
不在 source 列表里的孤儿 slug 单独报出,不自动补(避免造 broken link)。

用法:
    python scripts/backfill_citation_links.py          # dry-run,只打印
    python scripts/backfill_citation_links.py --apply  # 真正写盘
"""

from __future__ import annotations

import sys
from pathlib import Path

WIKI = Path(__file__).resolve().parent.parent / "data" / "wiki" / "wiki"
SOURCES_DIR = WIKI / "sources"

# 已知真实 source slug(由文件名确定)
REAL_SLUGS = sorted((p.stem for p in SOURCES_DIR.glob("*.md")), key=len, reverse=True)


def find_bare_citations(text: str) -> list[str]:
    """找出 '引自来源:' 后面紧跟非 '[' 字符的位置上下文(用于报告孤儿)。"""
    hits = []
    marker = "引自来源:"
    i = 0
    while True:
        idx = text.find(marker, i)
        if idx < 0:
            break
        after = idx + len(marker)
        # 跳过可选空格
        j = after
        while j < len(text) and text[j] == " ":
            j += 1
        if j < len(text) and text[j] != "[":
            hits.append(text[after : after + 40].split("：")[0].split(":")[0].strip())
        i = after
    return hits


def backfill(text: str) -> tuple[str, int]:
    """对每个真实 slug 做 '引自来源:<slug>' -> '引自来源:[[<slug>]]'。

    长 slug 优先(REAL_SLUGS 已按长度降序),避免短 slug 是长 slug 前缀时误切。
    已是 '引自来源:[[' 的不动。
    """
    count = 0
    for slug in REAL_SLUGS:
        bare = f"引自来源:{slug}"
        good = f"引自来源:[[{slug}]]"
        # 已经是 [[slug]] 的不会匹配 bare(bare 后面紧跟 slug 首字符,good 后面是 '[')
        n = text.count(bare)
        if n:
            text = text.replace(bare, good)
            count += n
        # 带一个空格的变体: 引自来源: <slug>
        bare_sp = f"引自来源: {slug}"
        good_sp = f"引自来源: [[{slug}]]"
        n2 = text.count(bare_sp)
        if n2:
            text = text.replace(bare_sp, good_sp)
            count += n2
    return text, count


def main() -> None:
    apply = "--apply" in sys.argv
    targets = sorted(WIKI.rglob("*.md"))
    total_fixed = 0
    orphans: dict[str, list[str]] = {}

    for page in targets:
        if page.parent.name == "sources":
            continue
        body = page.read_text(encoding="utf-8")
        new_body, fixed = backfill(body)
        if fixed:
            total_fixed += fixed
            rel = page.relative_to(WIKI)
            print(f"[{'APPLY' if apply else 'DRY'}] {rel}: 补 {fixed} 处双链")
            if apply:
                page.write_text(new_body, encoding="utf-8")
        # 检测改完后仍存在的裸引用(孤儿 slug)
        check_body = new_body
        bare = find_bare_citations(check_body)
        # 过滤掉真实 slug(已被替换,不该再出现)
        leftover = [b for b in bare if not any(b.startswith(s[:8]) for s in REAL_SLUGS) or b not in REAL_SLUGS]
        leftover = [b for b in bare if b and b not in REAL_SLUGS]
        if leftover:
            orphans[str(page.relative_to(WIKI))] = sorted(set(leftover))

    print(f"\n=== 总计补双链: {total_fixed} 处 ===")
    if orphans:
        print("\n=== 孤儿 slug(不在 source 列表,未自动补,需人工决定) ===")
        for f, slugs in orphans.items():
            for s in slugs:
                print(f"  {f}: 引自来源:{s}")


if __name__ == "__main__":
    main()
