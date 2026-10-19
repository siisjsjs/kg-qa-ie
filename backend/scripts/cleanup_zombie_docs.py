"""清理 documents 表中的脏数据(running 假死 / pending 没跑过的)。

使用:
    python scripts/cleanup_zombie_docs.py            # 报告(dry-run)
    python scripts/cleanup_zombie_docs.py --apply    # 真执行

策略:
  - parse_status='running' 但所有 ingest_jobs 已结束(没有 running 行) → 标 failed
  - parse_status='pending' 但 ingest_jobs 全为 succeeded/skipped(cache 命中型脏数据,
    旧 bug 留下:cache 命中时没改 doc.parse_status) → 标 succeeded
  - parse_status='pending' 且没有任何 ingest_jobs 行 → 视为从未触发,直接删 documents 行
                                                       + 顺手清掉对应 raw/ md 文件(若孤立)

Document 行删除时也会 cascade 删 ingest_jobs(外键),但若有其他表关联(如 wiki_node_versions
.document_id 没设 cascade),那行的 document_id 会变成悬空 — 这里只是清理脏数据,我们用
"version 表的 document_id 置 NULL" 而不是真删 version。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本能 import app.*(脚本目录在 backend/scripts/,需要把 backend/ 加 sys.path)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import session_scope  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.ingest_job import IngestJob  # noqa: E402
from app.models.wiki_node_version import WikiNodeVersion  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真执行清理(默认 dry-run)")
    args = ap.parse_args()

    apply = args.apply
    print(f"模式:{'APPLY (真执行)' if apply else 'DRY-RUN (只报告)'}")
    print("-" * 60)

    with session_scope() as db:
        # 1) running 假死:有 running 状态的 doc,但所有 ingest_jobs 都 finished
        running_docs = (
            db.query(Document).filter(Document.parse_status == "running").all()
        )
        zombie_running: list[Document] = []
        for d in running_docs:
            jobs = db.query(IngestJob).filter(IngestJob.document_id == d.id).all()
            has_active = any(j.status in ("queued", "running") for j in jobs)
            if not has_active:
                zombie_running.append(d)

        # 2) pending 从未触发:没有任何 ingest_jobs 行
        # 3) pending 但已跑过(cache 命中型):有 jobs 且全 succeeded/skipped
        pending_docs = (
            db.query(Document).filter(Document.parse_status == "pending").all()
        )
        zombie_pending: list[Document] = []
        cache_hit_stuck: list[Document] = []
        TERMINAL_OK = {"succeeded", "skipped"}
        for d in pending_docs:
            jobs = db.query(IngestJob).filter(IngestJob.document_id == d.id).all()
            if not jobs:
                zombie_pending.append(d)
            elif all(j.status in TERMINAL_OK for j in jobs):
                cache_hit_stuck.append(d)
            # 中间态(如有 failed)就不动,管理员人工处理

        print(f"\n[假死 running]:{len(zombie_running)} 个 doc")
        for d in zombie_running:
            print(f"  doc#{d.id}  {d.original_filename}  → 标 failed")

        print(f"\n[cache 命中卡 pending]:{len(cache_hit_stuck)} 个 doc")
        for d in cache_hit_stuck:
            print(f"  doc#{d.id}  {d.original_filename}  → 标 succeeded")

        print(f"\n[未触发 pending]:{len(zombie_pending)} 个 doc")
        for d in zombie_pending:
            print(f"  doc#{d.id}  {d.original_filename}  → 删除 + 移除 raw 文件")

        if not apply:
            print("\n(dry-run,未做任何修改;加 --apply 真执行)")
            return 0

        # 真执行
        # — running → failed
        for d in zombie_running:
            d.parse_status = "failed"
            d.parse_error = "标记为失败:进程崩溃后假死(由 cleanup_zombie_docs 处理)"

        # — cache 命中卡 pending → succeeded(对应 wiki 页面已实际落盘)
        for d in cache_hit_stuck:
            d.parse_status = "succeeded"
            d.parse_error = None

        # — pending 删除:先删 ingest_jobs,再断 wiki_node_versions.document_id,再删 doc + raw 文件
        removed_raw: list[str] = []
        for d in zombie_pending:
            # ingest_jobs 因外键约束需先清(虽然这些 doc 没有 jobs,稳妥起见)
            db.query(IngestJob).filter(IngestJob.document_id == d.id).delete(
                synchronize_session=False
            )
            # wiki_node_versions.document_id 设为 NULL(避免外键错)
            db.query(WikiNodeVersion).filter(
                WikiNodeVersion.document_id == d.id
            ).update({"document_id": None}, synchronize_session=False)
            # raw 文件
            raw_p = Path(d.file_path)
            if raw_p.exists() and raw_p.is_file():
                try:
                    raw_p.unlink()
                    removed_raw.append(str(raw_p))
                except OSError as e:
                    print(f"  [warn] 无法删除 {raw_p}: {e}")
            db.delete(d)

        db.commit()
        print(f"\n已应用:")
        print(f"  - {len(zombie_running)} 个 running → failed")
        print(f"  - {len(cache_hit_stuck)} 个 cache-hit pending → succeeded")
        print(f"  - {len(zombie_pending)} 个 pending document 行已删除")
        print(f"  - {len(removed_raw)} 个孤立 raw md 文件已删除")
        for r in removed_raw:
            print(f"    {r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
