"""扫描全 wiki 的虚空内链:[[X]] 指向的目标既无同名页、也非别名 → 虚空连接。

doc#24 cascade-delete + 残留清理后,老页正文里可能仍有指向「已删 PTS 页」的
叙述性内链(如 [[模特排时法]] [[方法时间衡量]] [[MOD值]] 等),这些目标页已被
当作孤儿删除 → 成为虚空连接。本脚本只做扫描报告,不改文件。

判定:对每条 [[target(|alias)]],target 规整后:
  - 命中现存页 slug → 合法
  - 命中某页 aliases → 合法(可别名解析)
  - 否则 → 虚空
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import frontmatter  # noqa: E402

WIKI = Path(__file__).resolve().parent.parent / "data" / "wiki" / "wiki"
CONTENT_DIRS = ("entities", "topics", "sources", "queries", "synthesis", "comparisons")
LINK = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]+))?\]\]")


def _iter_pages():
    for d in CONTENT_DIRS:
        for p in sorted((WIKI / d).rglob("*.md")):
            yield p


def main() -> None:
    slugs: set[str] = set()
    aliases: set[str] = set()
    page_text: dict[Path, str] = {}
    for p in _iter_pages():
        txt = p.read_text(encoding="utf-8")
        page_text[p] = txt
        post = frontmatter.loads(txt)
        slugs.add(p.stem)
        s = post.metadata.get("slug")
        if s:
            slugs.add(str(s))
        for a in (post.metadata.get("aliases") or []):
            aliases.add(str(a))

    known = slugs | aliases
    void: Counter = Counter()
    void_pages: dict[str, set] = {}
    for p, txt in page_text.items():
        # 跳过 sources 页(其正文不应有内链残留,且引用行已清)
        for m in LINK.finditer(txt):
            target = m.group(1).strip()
            if target in known:
                continue
            void[target] += 1
            void_pages.setdefault(target, set()).add(p.stem)

    print(f"现存页 slug={len(slugs)} 别名={len(aliases)}")
    print(f"=== 虚空目标 {len(void)} 个 · 共 {sum(void.values())} 处引用 ===")
    for target, cnt in void.most_common():
        pages = "、".join(sorted(void_pages[target])[:6])
        more = "…" if len(void_pages[target]) > 6 else ""
        print(f"  [{cnt:>2}] {target}  ← {pages}{more}")


if __name__ == "__main__":
    main()
