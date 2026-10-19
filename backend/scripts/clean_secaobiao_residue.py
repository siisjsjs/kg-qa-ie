"""清理「2026-06-07-秒表时间研究」失败 ingest(post_process 崩溃)留下的正文级残留。

背景:该素材 ingest 在 post_process(merge_frontmatter)崩溃。崩之前 unify_entity_pages
已把秒表内容深度融合写入 10 个老实体页正文,但 frontmatter/索引未同步,
故 delete_source 漏清。这些页 frontmatter.sources 均已不含秒表(有其它真实来源),
不该整页删,只需精准摘除秒表残留段。

残留形态(按行/段处理):
  1. blockquote 引用行:    `> 引自来源:[[2026-06-07-秒表时间研究]]: ...`        → 删整行
  2. 缩进 blockquote 引用:  `  > 引自来源:[[2026-06-07-秒表时间研究]]: ...`       → 删整行
  3. 文末参考项:           `- [[2026-06-07-秒表时间研究]]`                       → 删整行
  4. 内嵌来源列表项:        `- **例8-x ...**(来源/引自:[[2026-06-07-秒表时间研究]]...)...` → 删整行
  5. 纯秒表 ### 小节:       标题命中「例8-」或「在[[秒表时间研究]]中的应用」 → 删整段(到下一同级/上级标题前)
  6. 正文裸链:             `[[秒表时间研究]]`(非「法」)指向已删页              → 去链保留文字

用法:
    python scripts/clean_secaobiao_residue.py            # dry-run,逐页打印将删的行/段
    python scripts/clean_secaobiao_residue.py --apply    # 写盘
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WIKI = Path(__file__).resolve().parent.parent / "data" / "wiki" / "wiki"
SRC = "2026-06-07-秒表时间研究"

PAGES = [
    "time-study", "三倍标准差法", "作业测定", "动作经济原则", "宽放时间",
    "宽放率", "工作抽样法", "标准时间", "生产线的平衡率", "秒表时间研究法",
]

# 命中 [[SRC]] 的行(引用行 / 参考项 / 内嵌来源列表项)
LINE_HAS_SRC = re.compile(re.escape(f"[[{SRC}]]"))
# 纯秒表小节标题:### 含「例8-」或「在[[秒表时间研究]]中的应用」或「习题7」(齿轮加工,秒表新增、无引用行)
SECAOBIAO_HEADING = re.compile(r"^(#{2,4})\s.*(例8-|在\[\[秒表时间研究\]\]中的应用|习题7)")
ANY_HEADING = re.compile(r"^(#{1,6})\s")
# 正文裸链 [[秒表时间研究]](精确,排除「秒表时间研究法」)
BARE_LINK = re.compile(r"\[\[秒表时间研究\]\](?!法)")


def _section_end(lines: list[str], start: int) -> int:
    """从小节标题行 start 起,返回该段结束(不含)的行索引:下一个 同级或更高级 标题。"""
    m = ANY_HEADING.match(lines[start])
    level = len(m.group(1))
    for i in range(start + 1, len(lines)):
        hm = ANY_HEADING.match(lines[i])
        if hm and len(hm.group(1)) <= level:
            return i
    return len(lines)


def clean_page(text: str) -> tuple[str, list[str]]:
    lines = text.split("\n")
    drop = [False] * len(lines)
    notes: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        # 5. 纯秒表小节:整段删
        if SECAOBIAO_HEADING.match(line):
            end = _section_end(lines, i)
            for j in range(i, end):
                drop[j] = True
            notes.append(f"  [删小节] L{i + 1}-{end}: {line.strip()[:40]}")
            i = end
            continue
        # 1-4. 命中 [[SRC]] 的整行删
        if LINE_HAS_SRC.search(line):
            drop[i] = True
            notes.append(f"  [删行]  L{i + 1}: {line.strip()[:60]}")
        i += 1

    kept = [ln for k, ln in enumerate(lines) if not drop[k]]
    new = "\n".join(kept)

    # 6. 裸链去链(保留文字)
    n_link = len(BARE_LINK.findall(new))
    if n_link:
        new = BARE_LINK.sub("秒表时间研究", new)
        notes.append(f"  [去裸链] {n_link} 处 [[秒表时间研究]] → 秒表时间研究")

    # 收尾:压缩因删行产生的 3+ 连续空行为 2 个
    new = re.sub(r"\n{3,}", "\n\n", new)
    return new, notes


def main() -> None:
    apply = "--apply" in sys.argv
    total = 0
    for name in PAGES:
        page = WIKI / "entities" / f"{name}.md"
        if not page.exists():
            print(f"!! 不存在: {name}.md")
            continue
        body = page.read_text(encoding="utf-8")
        new, notes = clean_page(body)
        if not notes:
            print(f"== {name}.md: 无残留")
            continue
        total += len(notes)
        print(f"== {name}.md ({'APPLY' if apply else 'DRY'}): {len(notes)} 处")
        for n in notes:
            print(n)
        if apply:
            page.write_text(new, encoding="utf-8")
    print(f"\n=== {'已写盘' if apply else 'DRY-RUN'} 合计 {total} 处操作,{len(PAGES)} 页 ===")


if __name__ == "__main__":
    main()
