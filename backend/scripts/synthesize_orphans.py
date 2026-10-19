"""一次性脚本:把书内"(无主题分组)"的孤立实体综合成高质量 topic 页。

逻辑见 app/services/wiki/orphan_topic.py:
  - 扫所有书,每个 source(章节)下的孤立实体 → LLM 综合成 1 个 topic
  - 实体多时按 _MAX_ENTITIES_PER_TOPIC 分批,产出多个 topic
  - 写盘后同步 wiki_pages/wiki_links 表 + 重建 graph-data.json

用法:
  cd D:/kg-qa-ie/backend
  python scripts/synthesize_orphans.py --dry-run            # 全量预览,不写盘
  python scripts/synthesize_orphans.py --dry-run --limit 3  # 只看前 3 个章节
  python scripts/synthesize_orphans.py --limit 3            # 真跑前 3 个章节(冒烟)
  python scripts/synthesize_orphans.py                      # 全量真跑
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.services.wiki.orphan_topic import (  # noqa: E402
    collect_orphan_work,
    synthesize_orphan_topics,
)


def _sync_and_rebuild(wiki_root: Path, rel_paths: list[str]) -> None:
    """写盘后:同步 DB 索引 + 重建图谱 json。失败只告警,不阻断。"""
    if not rel_paths:
        return
    try:
        from app.core.db import session_scope
        from app.services.ingestion.pipeline import _sync_db_indices

        with session_scope() as db:
            stats = _sync_db_indices(db, wiki_root=wiki_root, written_paths=rel_paths)
        print(f"[db-sync] {stats}")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] db sync failed (页已落盘,可稍后重建): {e}")

    try:
        from app.services.graph.builder import build_graph_data, write_graph_data_json

        async def _rebuild() -> None:
            payload = await build_graph_data(wiki_root=wiki_root)
            await write_graph_data_json(wiki_root=wiki_root, payload=payload)

        asyncio.run(_rebuild())
        print("[graph] graph-data.json 已重建")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] graph rebuild failed (下次 ingest 会重建): {e}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只预览,不写盘")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 个章节")
    parser.add_argument("--only-source", type=str, default=None, help="只处理指定 source_slug")
    args = parser.parse_args()

    wiki_root = settings.wiki_root_path
    if not (wiki_root / "wiki").exists():
        print(f"[ERROR] wiki dir not found: {wiki_root / 'wiki'}")
        return 1

    work = collect_orphan_work(wiki_root)
    total_orphans = sum(len(w["orphan_slugs"]) for w in work)
    print(f"wiki_root          : {wiki_root}")
    print(f"章节(有孤立实体)  : {len(work)}")
    print(f"孤立实体总数        : {total_orphans}")
    if args.limit is not None:
        print(f"本次只处理前        : {args.limit} 个章节")
    if args.only_source:
        print(f"本次只处理 source   : {args.only_source}")
    print(f"模式                : {'DRY-RUN(预览)' if args.dry_run else '真跑(写盘)'}")
    print("-" * 60)

    res = asyncio.run(
        synthesize_orphan_topics(
            wiki_root=wiki_root,
            dry_run=args.dry_run,
            limit_sources=args.limit,
            only_source=args.only_source,
        )
    )

    print(f"\n章节已处理 : {res['sources_processed']}")
    print(f"topic 生成 : {res['topics_generated']}")
    print(f"失败       : {res['failed']}")
    for g in res["generated"]:
        flag = "(已存在,加后缀)" if g.get("exists") else ""
        print(f"  + [{g['source']}] {g['topic_title']}  ({g['related_count']} 实体) {flag}")
    for f in res["failures"]:
        print(f"  ! [{f.get('source')}] {f.get('error')}")

    if not args.dry_run:
        rel_paths = [g["written"] for g in res["generated"] if g.get("written")]
        print(f"\n落盘 {len(rel_paths)} 个新 topic 页,开始同步索引与图谱...")
        _sync_and_rebuild(wiki_root, rel_paths)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
