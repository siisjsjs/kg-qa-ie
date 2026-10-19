"""去链:把 doc#24(预定动作时间标准法)删除后产生的虚空内链还原为纯文字。

背景:cascade-delete 删掉了 29 个纯 PTS 孤儿页,但老页正文里仍有指向它们的
叙述性内链 [[X]] → 虚空连接。方案:去链(保留显示文字,不让 LLM 凭空重建已删页)。

规则:
  [[X]]      → X
  [[X|别名]] → 别名
只处理下列经 scan_void_links_yudong.py 确认的 9 个虚空目标,绝不动其它合法内链。

用法:
    python scripts/delink_yudong_void.py            # dry-run
    python scripts/delink_yudong_void.py --apply
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WIKI = Path(__file__).resolve().parent.parent / "data" / "wiki" / "wiki"

VOID_TARGETS = [
    "模特排时法", "MOD值", "克里斯·哈依德", "工作因素法", "方法时间衡量",
    "哈罗德·梅纳德", "古斯塔夫·斯坦门丁", "模特分析记录表", "基本动作时间",
]

PATTERNS = {
    t: re.compile(r"\[\[" + re.escape(t) + r"(?:\|([^\[\]]+))?\]\]")
    for t in VOID_TARGETS
}


def delink_page(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    new = text
    for t, pat in PATTERNS.items():
        def _sub(m: re.Match, _t: str = t) -> str:
            return m.group(1) if m.group(1) else _t
        new, n = pat.subn(_sub, new)
        if n:
            notes.append(f"  [[{t}]] × {n}")
    return new, notes


def main() -> None:
    apply = "--apply" in sys.argv
    total = 0
    touched = 0
    for page in sorted(WIKI.rglob("*.md")):
        parts = page.relative_to(WIKI).parts
        if parts[0] not in ("entities", "topics"):
            continue
        body = page.read_text(encoding="utf-8")
        new, notes = delink_page(body)
        if not notes:
            continue
        touched += 1
        n_here = sum(int(x.split("×")[1]) for x in notes)
        total += n_here
        rel = page.relative_to(WIKI)
        print(f"== {rel} ({'APPLY' if apply else 'DRY'}): {n_here} 处")
        for n in notes:
            print(n)
        if apply:
            page.write_text(new, encoding="utf-8")
    print(f"\n=== {'已写盘' if apply else 'DRY-RUN'} 合计 {total} 处去链,{touched} 页 ===")


if __name__ == "__main__":
    main()
