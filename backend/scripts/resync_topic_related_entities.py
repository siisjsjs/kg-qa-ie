"""一次性脚本:把所有主题页 frontmatter.related_entities 重新按正文「## 关键实体」段双链派生。

背景:LLM 在 step2c 一次性写出 frontmatter.related_entities 与正文「## 关键实体」段
双链,两处本应同源(都来自 step1.topic.related_entities),但 LLM 前后不一致 →
目录树/关系侧栏(读 frontmatter)与正文/图谱(读正文双链)显示不同实体。
正文「## 关键实体」段是用户实际阅读的权威列表,故让 frontmatter 跟随正文。

本脚本只重写 frontmatter.related_entities,不动正文、不 bump version、不重传。
复用 pipeline._derive_related_entities_from_body 的同一派生逻辑,保证与新入库一致。

用法:
  cd D:/kg-qa-ie/backend
  python scripts/resync_topic_related_entities.py --dry-run   # 仅打印将改动的主题
  python scripts/resync_topic_related_entities.py             # 实际写入
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.services.ingestion.pipeline import (  # noqa: E402
    _derive_related_entities_from_body,
)
from app.services.wiki.frontmatter_io import parse_text, serialize  # noqa: E402


def _write_atomic_sync(path: Path, content: str) -> None:
    """同步原子写(脚本无事件循环,不复用 async fs_io.atomic_write)。"""
    import os

    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="仅打印差异,不写盘")
    args = ap.parse_args()

    wiki_root = settings.wiki_root_path
    topics_dir = wiki_root / "wiki" / "topics"
    if not topics_dir.is_dir():
        print(f"[err] 主题目录不存在: {topics_dir}")
        return 1

    changed = 0
    skipped_no_section = 0
    total = 0
    for fp in sorted(topics_dir.glob("*.md")):
        total += 1
        text = fp.read_text(encoding="utf-8")
        try:
            fm, body = parse_text(text)
        except Exception as e:  # noqa: BLE001
            print(f"[skip] {fp.stem} 解析失败: {e}")
            continue

        derived = _derive_related_entities_from_body(body)
        if derived is None:
            # 正文无「## 关键实体」段或段内无双链 → 保留原值,不动
            skipped_no_section += 1
            continue

        old = [str(x) for x in (fm.get("related_entities") or [])]
        if old == derived:
            continue

        changed += 1
        only_old = [x for x in old if x not in set(derived)]
        only_new = [x for x in derived if x not in set(old)]
        print(f"【{fp.stem}】 {len(old)} → {len(derived)}")
        if only_old:
            print(f"    移除(frontmatter 有正文无): {only_old}")
        if only_new:
            print(f"    新增(正文有frontmatter无): {only_new}")

        if not args.dry_run:
            fm = dict(fm)
            fm["related_entities"] = derived
            _write_atomic_sync(fp, serialize(fm, body))

    mode = "DRY-RUN(未写盘)" if args.dry_run else "已写盘"
    print(
        f"\n--- {mode} | 主题总数 {total} | 改动 {changed} | "
        f"无关键实体段保留原值 {skipped_no_section} ---"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
