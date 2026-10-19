"""一次性脚本:用 firecrawl 联网抓料 + LLM 综合,填充 void-link 实体页。

逻辑见 app/services/wiki/firecrawl_fill.py。
噪声过滤:跳过日期前缀 source 式 slug、HTML 注释残渣;全/半角括号变体去重。

用法:
  cd D:/kg-qa-ie/backend
  python scripts/fill_void_via_web.py --scan                       # 只看清洗后的目标分布,不调用任何 API
  python scripts/fill_void_via_web.py --dry-run --limit 3          # 前 3 个目标:真搜真抓但不落盘
  python scripts/fill_void_via_web.py --min-ref 2                  # 真跑:只填被引用 >=2 次的高价值目标
  python scripts/fill_void_via_web.py --min-ref 2 --limit 10       # 真跑前 10 个高价值目标
  python scripts/fill_void_via_web.py                              # 真跑全部清洗后目标(消耗较多 firecrawl 配额)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.services.wiki.firecrawl_fill import (  # noqa: E402
    collect_fillable_targets,
    fill_void_entities_via_web,
)


def _sync_and_rebuild(wiki_root: Path, rel_paths: list[str]) -> None:
    if not rel_paths:
        return
    try:
        from app.core.db import session_scope
        from app.services.ingestion.pipeline import _sync_db_indices

        with session_scope() as db:
            stats = _sync_db_indices(db, wiki_root=wiki_root, written_paths=rel_paths)
        print(f"[db-sync] {stats}")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] db sync failed (页已落盘): {e}")

    try:
        from app.services.graph.builder import build_graph_data, write_graph_data_json

        async def _rebuild() -> None:
            payload = await build_graph_data(wiki_root=wiki_root)
            await write_graph_data_json(wiki_root=wiki_root, payload=payload)

        asyncio.run(_rebuild())
        print("[graph] graph-data.json 已重建")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] graph rebuild failed: {e}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", action="store_true", help="只打印清洗后目标分布,不调用 API")
    parser.add_argument("--dry-run", action="store_true", help="真搜真抓但不落盘")
    parser.add_argument("--min-ref", type=int, default=1, help="只填引用次数 >= 此值的目标")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 个(按 ref 降序)")
    parser.add_argument("--only-slug", type=str, default=None, help="只处理指定 slug")
    args = parser.parse_args()

    wiki_root = settings.wiki_root_path
    if not (wiki_root / "wiki").exists():
        print(f"[ERROR] wiki dir not found: {wiki_root / 'wiki'}")
        return 1

    targets = collect_fillable_targets(wiki_root=wiki_root, min_ref_count=args.min_ref)
    print(f"wiki_root           : {wiki_root}")
    print(f"清洗后 fillable 目标 : {len(targets)} (min_ref={args.min_ref})")
    ge2 = [t for t in targets if t["ref_count"] >= 2]
    print(f"  其中 ref>=2 高价值 : {len(ge2)}")

    if args.scan:
        print("\n--- 目标(按 ref_count 降序,前 40)---")
        for t in targets[:40]:
            print(f"  {t['ref_count']:>3}  {t['broken_target']}")
        print("\n--scan 模式:未调用任何 API。")
        return 0

    print(f"模式                : {'DRY-RUN(搜+抓不落盘)' if args.dry_run else '真跑(落盘)'}")
    if args.limit is not None:
        print(f"本次只处理前        : {args.limit} 个")
    print("-" * 60)

    res = asyncio.run(
        fill_void_entities_via_web(
            wiki_root=wiki_root,
            dry_run=args.dry_run,
            min_ref_count=args.min_ref,
            limit=args.limit,
            only_slug=args.only_slug,
        )
    )

    print(f"\n目标处理 : {res['targets']}")
    print(f"已填充   : {res['filled']}")
    print(f"失败     : {res['failed']}")
    for g in res["generated"]:
        print(f"  + {g['slug']}  ({g.get('body_chars', 0)} 字, {len(g.get('urls') or [])} 源)")
    for f in res["failures"]:
        print(f"  ! {f.get('slug')}: {f.get('error')}")

    if not args.dry_run:
        rel_paths = [g["written"] for g in res["generated"] if g.get("written")]
        print(f"\n落盘 {len(rel_paths)} 个新实体页,开始同步索引与图谱...")
        _sync_and_rebuild(wiki_root, rel_paths)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
