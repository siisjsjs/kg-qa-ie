"""去链:把 11 个「虚空目标」(无对应页、且为秒表残留正文内链)的 [[X]] 还原为纯文字。

背景:秒表 ingest(已删)融合改写时,在多个老实体页正文里写入了指向这些目标的内链,
但这些目标从未生成独立页 → 成为虚空连接。方案 1:去链(不让 LLM 凭空造页)。

规则:
  [[X]]        → X        (保留显示文字)
  [[X|别名]]   → 别名      (保留别名显示文字)
只处理下列 11 个目标,绝不动其它合法内链([[标准时间]]/[[作业测定]] 等)。

用法:
    python scripts/delink_void_targets.py            # dry-run
    python scripts/delink_void_targets.py --apply
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WIKI = Path(__file__).resolve().parent.parent / "data" / "wiki" / "wiki"

VOID_TARGETS = [
    "正常时间", "平准化法", "合成评定法", "客观评定法", "误差界限法",
    "d2值法", "私事宽放", "疲劳宽放", "延迟宽放", "政策宽放",
    "M82汽车座椅骨架装配生产线",
]

# [[目标]] 或 [[目标|别名]];去链时:有别名取别名,否则取目标本身
PATTERNS = {
    t: re.compile(r"\[\[" + re.escape(t) + r"(?:\|([^\[\]]+))?\]\]")
    for t in VOID_TARGETS
}


def delink_page(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    new = text
    for t, pat in PATTERNS.items():
        def _sub(m: re.Match) -> str:
            return m.group(1) if m.group(1) else t
        new, n = pat.subn(_sub, new)
        if n:
            notes.append(f"  [[{t}]] × {n}")
    return new, notes


def main() -> None:
    apply = "--apply" in sys.argv
    total = 0
    touched = 0
    for page in sorted(WIKI.rglob("*.md")):
        if page.parent.name == "sources":
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
