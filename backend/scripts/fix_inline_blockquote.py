"""把行中的 ' > 引自来源' / ' > ⚠' 规整成顶格 blockquote。

问题:LLM 把引用证据接在正文段落同一行尾部,用 ' > ' 分隔,
'>' 不在行首,markdown 不认 blockquote,渲染成普通正文。

规则:把每个 ' > '(空格+>+空格,且 > 不在行首)替换成 '\n\n> ',
让每条引用独立成顶格 blockquote 块,与正文之间留空行。

只处理传入的单个文件(单页试错用)。

用法:
    python scripts/fix_inline_blockquote.py <相对wiki的路径>            # dry-run
    python scripts/fix_inline_blockquote.py <相对wiki的路径> --apply    # 写盘
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WIKI = Path(__file__).resolve().parent.parent / "data" / "wiki" / "wiki"

# 行中真粘连的引用标记 '>':> 前面是「可见字符」(正文/中文/标点),
# 即引用粘在正文句尾(如 '。> 引自来源' / '；> 引自来源' / '文字 > 引自来源')。
# 行首 '> ' 和列表项内缩进 '   > '(> 前是空白)是合法 blockquote,绝不能动,
# 否则会破坏列表结构。故 lookbehind 用 [^\s>] 排除空白与连续 >。
INLINE_BQ = re.compile(r"(?<=[^\s>]) *> *(?=引自来源|⚠)")


def transform(text: str) -> tuple[str, int]:
    new, n = INLINE_BQ.subn("\n\n> ", text)
    return new, n


def _process(page: Path, apply: bool) -> int:
    body = page.read_text(encoding="utf-8")
    new_body, n = transform(body)
    if n:
        rel = page.relative_to(WIKI)
        print(f"[{'APPLY' if apply else 'DRY'}] {rel}: 规整 {n} 处")
        if apply:
            page.write_text(new_body, encoding="utf-8")
    return n


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    full = "--all" in sys.argv

    if full:
        total_pages = total_hits = 0
        for page in sorted(WIKI.rglob("*.md")):
            if page.parent.name == "sources":
                continue
            n = _process(page, apply)
            if n:
                total_pages += 1
                total_hits += n
        print(f"\n=== {'APPLY' if apply else 'DRY'} 全库: {total_pages} 页, {total_hits} 处 ===")
        return

    if not args:
        print("用法: fix_inline_blockquote.py <wiki相对路径> [--apply]  |  --all [--apply]")
        sys.exit(1)
    page = WIKI / args[0]
    if not page.exists():
        print(f"文件不存在: {page}")
        sys.exit(1)
    n = _process(page, apply)
    print(f"[{'APPLY' if apply else 'DRY'}] {args[0]}: 规整 {n} 处行中引用为顶格 blockquote")


if __name__ == "__main__":
    main()
