"""一次性脚本:清空所有已导入的实体/主题/素材数据(2026-05-20 用户请求)。

清空范围:
  - wiki/{entities,topics,sources,queries,synthesis,comparisons}/*.md
  - wiki/synthesis/sessions/*
  - wiki/_growth/{gap-queue.md,hot-topics.md,followup-queue.md} → 重置为占位
  - raw/{articles,zhihu,wechat,xiaohongshu,pdfs,plain_text,tweets}/*
  - .wiki-cache.json / graph-data.json / index.md / log.md / overview.md / .wiki-schema.md
    → 重置为 init.py 种子模板默认值
  - DB 表清空:documents / wiki_pages / wiki_links / wiki_node_versions /
    wiki_aliases / ingest_jobs / qa_messages / qa_sessions / gap_queue /
    hot_topics / followup_queue / slug_redirects
  - data/cache/batches/* / last_step1_raw.txt / run*.json / upload*.json
保留:
  - users(管理员账号)
  - system_config(LLM/embedding 配置)
  - prompt_versions / alembic_version

用法:
  cd D:/kg-qa-ie/backend
  python scripts/wipe_all_ingested.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让脚本能 import app.*
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.db import session_scope  # noqa: E402
from app.services.wiki.init import _render_template  # noqa: E402

TEMPLATES_ROOT = ROOT / "app" / "prompts" / "templates"


# ---------------------------------------------------------------------------
# 文件清理
# ---------------------------------------------------------------------------


WIKI_KIND_DIRS = (
    "wiki/entities",
    "wiki/topics",
    "wiki/sources",
    "wiki/queries",
    "wiki/synthesis",
    "wiki/comparisons",
)
RAW_KIND_DIRS = (
    "raw/pdfs",
    "raw/articles",
    "raw/tweets",
    "raw/wechat",
    "raw/xiaohongshu",
    "raw/zhihu",
    "raw/plain_text",
)


def _clear_dir(d: Path, *, keep_gitkeep: bool = True, recursive: bool = True) -> int:
    """删除目录下所有文件(保留目录本身;可选保留 .gitkeep)。"""
    if not d.exists():
        return 0
    n = 0
    for p in d.rglob("*") if recursive else d.iterdir():
        if p.is_file():
            if keep_gitkeep and p.name == ".gitkeep":
                continue
            p.unlink()
            n += 1
    # 第二轮:把空子目录清掉(synthesis/sessions/ 这种)
    if recursive:
        for p in sorted([x for x in d.rglob("*") if x.is_dir()], reverse=True):
            try:
                if not any(p.iterdir()):
                    p.rmdir()
            except OSError:
                pass
    return n


def wipe_wiki_files(wiki_root: Path) -> dict:
    stats: dict[str, int] = {}
    # 实体/主题/素材/查询/综合/对比 全清
    for sub in WIKI_KIND_DIRS:
        d = wiki_root / sub
        n = _clear_dir(d, keep_gitkeep=True, recursive=True)
        stats[sub] = n
        # 清空后重建 .gitkeep
        d.mkdir(parents=True, exist_ok=True)
        keep = d / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
    # synthesis/sessions 子目录单独保留
    sessions = wiki_root / "wiki" / "synthesis" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    keep = sessions / ".gitkeep"
    if not keep.exists():
        keep.write_text("", encoding="utf-8")

    # raw/ 全清
    for sub in RAW_KIND_DIRS:
        d = wiki_root / sub
        n = _clear_dir(d, keep_gitkeep=True, recursive=True)
        stats[sub] = n
        d.mkdir(parents=True, exist_ok=True)
        keep = d / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")

    # _growth 重建占位
    growth_dir = wiki_root / "wiki" / "_growth"
    growth_dir.mkdir(parents=True, exist_ok=True)
    gap_template = TEMPLATES_ROOT / "gap_queue.md"
    if gap_template.exists():
        (growth_dir / "gap-queue.md").write_text(
            _render_template(gap_template.read_text(encoding="utf-8")), encoding="utf-8"
        )
    for fname in ("hot-topics.md", "followup-queue.md"):
        p = growth_dir / fname
        if p.exists():
            p.unlink()
    stats["wiki/_growth"] = 1

    # 顶层元文件重置
    for tpl_name, dest_rel in (
        ("schema.md", ".wiki-schema.md"),
        ("index.md", "index.md"),
        ("log.md", "log.md"),
        ("overview.md", "overview.md"),
    ):
        tpl = TEMPLATES_ROOT / tpl_name
        if not tpl.exists():
            continue
        text_ = _render_template(tpl.read_text(encoding="utf-8"))
        (wiki_root / dest_rel).write_text(text_, encoding="utf-8")
    stats["meta_files_reset"] = 4

    # 删除 .wiki-cache.json / graph-data.json
    for fname in (".wiki-cache.json", "graph-data.json", "knowledge-graph.html"):
        p = wiki_root / fname
        if p.exists():
            p.unlink()
            stats[fname] = 1

    return stats


def wipe_cache_dir(cache_dir: Path) -> dict:
    """清掉 data/cache/ 下的中间产物,保留 system_config.json + .fernet.key。"""
    stats: dict[str, int] = {}
    if not cache_dir.exists():
        return stats
    keep_names = {"system_config.json", ".fernet.key"}
    n_files = 0
    for p in cache_dir.iterdir():
        if p.name in keep_names:
            continue
        if p.is_file():
            p.unlink()
            n_files += 1
        elif p.is_dir():
            # batches/ 等子目录整个清掉
            count = _clear_dir(p, keep_gitkeep=False, recursive=True)
            n_files += count
            try:
                p.rmdir()
            except OSError:
                pass
    stats["cache_files_removed"] = n_files
    return stats


# ---------------------------------------------------------------------------
# DB 清理
# ---------------------------------------------------------------------------


# 顺序很关键:有 FK 的子表先删
TABLES_TO_TRUNCATE = (
    "wiki_node_versions",
    "wiki_links",
    "wiki_aliases",
    "slug_redirects",
    "qa_messages",
    "qa_sessions",
    "gap_queue",
    "hot_topics",
    "followup_queue",
    "ingest_jobs",
    "wiki_pages",
    "documents",
)


def wipe_db_tables() -> dict:
    """清空指定表;保留 users / system_config / prompt_versions / alembic_version。

    用 DELETE 而非 TRUNCATE,以兼容 SQLite 与 MySQL,且保留自增 ID 的话不影响功能。
    MySQL 上 InnoDB FK 约束需要按子→父顺序删除。
    """
    stats: dict[str, int] = {}
    with session_scope() as db:
        # MySQL:临时关掉外键检查,简化清理顺序
        try:
            db.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        except Exception:  # noqa: BLE001
            # SQLite 等不支持
            pass
        for tbl in TABLES_TO_TRUNCATE:
            try:
                result = db.execute(text(f"DELETE FROM {tbl}"))
                stats[tbl] = result.rowcount or 0
            except Exception as e:  # noqa: BLE001
                stats[tbl] = -1
                print(f"  [warn] table {tbl} delete failed: {e}")
        try:
            db.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        except Exception:  # noqa: BLE001
            pass
    return stats


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    wiki_root = settings.wiki_root_path
    cache_dir = settings.cache_dir_path
    print(f"[wipe] wiki_root = {wiki_root}")
    print(f"[wipe] cache_dir = {cache_dir}")
    print(f"[wipe] db_url    = {settings.database_url.split('@')[-1]}")
    print()

    print("[1/3] 清理 wiki/ + raw/ 下文件...")
    fs_stats = wipe_wiki_files(wiki_root)
    for k, v in fs_stats.items():
        print(f"   {k}: {v}")

    print("\n[2/3] 清理 data/cache/ 下中间产物...")
    cache_stats = wipe_cache_dir(cache_dir)
    for k, v in cache_stats.items():
        print(f"   {k}: {v}")

    print("\n[3/3] 清空 DB 表(保留 users / system_config)...")
    try:
        db_stats = wipe_db_tables()
        for k, v in db_stats.items():
            print(f"   {k}: deleted {v} rows")
    except Exception as e:  # noqa: BLE001
        print(f"   [error] DB 清理失败: {e}")
        print("   提示:确保 MySQL 容器在跑 (docker compose up mysql)")
        return 1

    print("\n[done] 所有数据已清空,可以重新上传素材跑深度 ingest。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
