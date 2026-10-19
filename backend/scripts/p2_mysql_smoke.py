"""scripts/p2_mysql_smoke.py — P2 MySQL 实启验证。

前置条件:
  1. Docker Desktop 已运行
  2. 在 D:/kg-qa-ie/deploy 目录下跑 `docker compose up -d mysql`(或本脚本会自动起)

执行:
  cd D:/kg-qa-ie/backend
  python scripts/p2_mysql_smoke.py

行为:
  1. 等待 MySQL 健康(至多 60 秒)
  2. 跑 `alembic upgrade head`
  3. 列出实际建出的 14 张表 + 关键索引
  4. INSERT/SELECT 一次 system_config 行,验证 read/write 可用
  5. 跑一次 alembic check(对比 model 与库 schema)— 失败时打印差异
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
COMPOSE_DIR = REPO_ROOT / "deploy"

DB_URL = "mysql+pymysql://kgqa:CHANGE_ME@localhost:3309/kg_qa_ie?charset=utf8mb4"
TIMEOUT_S = 60

EXPECTED_TABLES = {
    "alembic_version",
    "users",
    "documents",
    "wiki_pages",
    "wiki_links",
    "wiki_aliases",
    "qa_sessions",
    "qa_messages",
    "gap_queue",
    "hot_topics",
    "followup_queue",
    "ingest_jobs",
    "system_config",
    "prompt_versions",
    "wiki_node_versions",
}


def step(label: str, ok: bool, info: str = "") -> None:
    mark = "OK " if ok else "FAIL"
    line = f"[{mark}] {label}"
    if info:
        line += f"  ({info})"
    print(line, flush=True)


def docker_up_mysql() -> bool:
    print(f"=> ensuring MySQL container up via docker compose ({COMPOSE_DIR})", flush=True)
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d", "mysql"],
            cwd=str(COMPOSE_DIR),
            check=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        step("docker compose up mysql", False, str(e))
        return False


def wait_for_mysql(timeout_s: int = TIMEOUT_S) -> bool:
    print(f"=> waiting for MySQL on {DB_URL} (timeout {timeout_s}s)", flush=True)
    started = time.time()
    last_err = ""
    while time.time() - started < timeout_s:
        try:
            engine = create_engine(DB_URL, pool_pre_ping=True)
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            engine.dispose()
            step("mysql ready", True, f"after {int(time.time() - started)}s")
            return True
        except Exception as e:  # noqa: BLE001
            last_err = str(e).splitlines()[-1][:120]
            time.sleep(2)
    step("mysql ready", False, last_err)
    return False


def alembic_upgrade() -> bool:
    print("=> alembic upgrade head", flush=True)
    env = os.environ.copy()
    env["DATABASE_URL"] = DB_URL
    res = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_DIR),
        env=env,
    )
    ok = res.returncode == 0
    step("alembic upgrade head", ok)
    return ok


def verify_tables() -> bool:
    engine = create_engine(DB_URL)
    insp = inspect(engine)
    actual = set(insp.get_table_names())
    missing = EXPECTED_TABLES - actual
    extra = actual - EXPECTED_TABLES
    print(f"   tables present: {sorted(actual)}")
    if missing:
        step("table set complete", False, f"missing={sorted(missing)}")
        return False
    if extra:
        step("table set complete", True, f"warning: extra tables {sorted(extra)}")
    else:
        step("table set complete", True, f"{len(actual)} tables")
    # 关键索引采样
    for tbl, expected_cols in (
        ("documents", {"content_hash"}),
        ("wiki_pages", {"slug"}),
        ("wiki_links", {"src_slug", "dst_slug"}),
        ("ingest_jobs", {"status", "document_id"}),
    ):
        idx_cols = {col for ix in insp.get_indexes(tbl) for col in ix.get("column_names") or []}
        if pk := insp.get_pk_constraint(tbl):
            idx_cols.update(pk.get("constrained_columns") or [])
        for ec in expected_cols:
            if ec not in idx_cols:
                step(f"index on {tbl}.{ec}", False)
                return False
        step(f"indexes on {tbl}", True)
    engine.dispose()
    return True


def smoke_read_write() -> bool:
    engine = create_engine(DB_URL)
    try:
        with engine.begin() as c:
            c.execute(
                sa.text(
                    "INSERT INTO system_config(`key`, `value`, encrypted, updated_at) "
                    "VALUES (:k, :v, 0, NOW()) "
                    "ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)"
                ),
                {"k": "p2.smoke", "v": "ok"},
            )
            row = c.execute(
                sa.text("SELECT `value` FROM system_config WHERE `key` = :k"),
                {"k": "p2.smoke"},
            ).first()
        ok = row is not None and row[0] == "ok"
        step("system_config read/write", ok)
        return ok
    except Exception as e:  # noqa: BLE001
        step("system_config read/write", False, str(e))
        return False
    finally:
        engine.dispose()


def alembic_check() -> bool:
    """如果 alembic >= 1.13 支持 `alembic check` 比对 metadata 与 DB schema。"""
    print("=> alembic check (model vs db drift)", flush=True)
    env = os.environ.copy()
    env["DATABASE_URL"] = DB_URL
    res = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=str(BACKEND_DIR),
        env=env,
    )
    ok = res.returncode == 0
    step("alembic check (no drift)", ok)
    return ok


def main() -> int:
    if not docker_up_mysql():
        return 1
    if not wait_for_mysql():
        return 1
    if not alembic_upgrade():
        return 1
    ok1 = verify_tables()
    ok2 = smoke_read_write()
    ok3 = alembic_check()
    return 0 if (ok1 and ok2 and ok3) else 1


if __name__ == "__main__":
    sys.exit(main())
