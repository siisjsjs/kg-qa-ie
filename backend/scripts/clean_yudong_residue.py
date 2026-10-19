"""清理「2026-06-07-第十章-预定动作时间标准法」失败 ingest(post_process orphan)
留在 19 个老实体页正文里的深度融合残留。

背景:doc#24 的 post_process 已把 PTS 内容深度融合写入老页(blockquote 证据行 +
参考项),但收尾(frontmatter.sources 回填 / 索引 / DB 版本)被进程重启打断 orphan。
cascade-delete(delete_source)已删 source 页 + 29 个纯 PTS 孤儿页、并从这些老页
frontmatter.sources 摘除该 slug、删 `### 来源` 子段;但 blockquote 级证据行
(`> 引自来源:[[SRC]]:...`)和 `## 参考来源` 下的 `- [[SRC]]` 项不在其清理范围,
故残留。与秒表那次同一处置:逐行精准删。

残留形态(均为单行):
  1. blockquote 证据行:`> 引自来源:[[SRC]]:...` 或缩进 `  > 引自来源:[[SRC]]:...` → 删整行
  2. 参考项:`- [[SRC]]` → 删整行
  3. 多源行(同一行还引用了其它真实来源,如组合引用行 / `⚠ 来源不一致` 对比注):
     **不能整删**,否则会误删对其它来源的合法引用 → 仅摘掉本行内的 `[[SRC]]`
     及其紧跟的全角括号说明 `（...）`,保留其余。

收尾:压缩 3+ 连续空行为 2 个。

用法:
    python scripts/clean_yudong_residue.py            # dry-run
    python scripts/clean_yudong_residue.py --apply
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WIKI = Path(__file__).resolve().parent.parent / "data" / "wiki" / "wiki"
SRC = "2026-06-07-第十章-预定动作时间标准法"
LINK = f"[[{SRC}]]"

# 一行里出现的所有 source 链接 [[2026-...]];用于判定本行是否「多源」
ANY_SOURCE_LINK = re.compile(r"\[\[2026-\d{2}-\d{2}[^\]]*\]\]")
# 摘除 [[SRC]] 及其紧跟的全角括号说明(若有),并清理遗留的中文顿号分隔
SRC_WITH_PAREN = re.compile(re.escape(LINK) + r"(（[^）]*）)?")


def _is_multi_source(line: str) -> bool:
    links = ANY_SOURCE_LINK.findall(line)
    others = [l for l in links if l != LINK]
    return len(others) > 0


def _delink_src_only(line: str) -> str:
    new = SRC_WITH_PAREN.sub("", line)
    # 清理因摘除中段链接遗留的顿号/逗号:连续顿号、行首/链接间多余分隔
    new = re.sub(r"、\s*、", "、", new)
    new = re.sub(r"）\s*、\s*$", "）", new)
    new = re.sub(r"、\s*$", "", new)
    return new


def clean_page(text: str) -> tuple[str, list[str]]:
    lines = text.split("\n")
    notes: list[str] = []
    kept: list[str] = []
    for idx, line in enumerate(lines):
        if LINK not in line:
            kept.append(line)
            continue
        # `⚠ 来源 ... 不一致` 矛盾注:其前提就是拿 SRC 与另一来源对比;
        # SRC 已删 → 对比失去意义,整行删(而非去链留下「与  的说法不一致」病句)。
        if "⚠" in line and "不一致" in line:
            notes.append(f"  [删矛盾注] L{idx + 1}: {line.strip()[:50]}")
            continue
        if _is_multi_source(line):
            new_line = _delink_src_only(line)
            notes.append(f"  [摘链] L{idx + 1}: 保留其它来源,去 [[SRC]]")
            kept.append(new_line)
        else:
            notes.append(f"  [删行] L{idx + 1}: {line.strip()[:60]}")
    new = "\n".join(kept)
    new = re.sub(r"\n{3,}", "\n\n", new)
    return new, notes


def main() -> None:
    apply = "--apply" in sys.argv
    total = 0
    touched = 0
    for page in sorted(WIKI.rglob("*.md")):
        # 只动内容目录,跳过 sources / _growth / 备份
        parts = page.relative_to(WIKI).parts
        if parts[0] not in ("entities", "topics"):
            continue
        body = page.read_text(encoding="utf-8")
        if LINK not in body:
            continue
        new, notes = clean_page(body)
        if not notes:
            continue
        touched += 1
        total += len(notes)
        rel = page.relative_to(WIKI)
        print(f"== {rel} ({'APPLY' if apply else 'DRY'}): {len(notes)} 行")
        for n in notes:
            print(n)
        if apply:
            page.write_text(new, encoding="utf-8")
    print(f"\n=== {'已写盘' if apply else 'DRY-RUN'} 合计删 {total} 行,{touched} 页 ===")


if __name__ == "__main__":
    main()
