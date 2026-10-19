"""ingestion/batch_state.py:批量摄入的可恢复状态机。

每个 batch 的状态落盘为 `{cache_dir}/batches/{batch_id}.json`,字段:

{
  "batch_id": str,
  "document_ids": [int],         # 全部要跑的 doc_id 顺序
  "pause_every": int,            # 每 N 个跑完后暂停
  "next_index": int,             # 下一个要处理的 document_ids 下标
  "status": "running" | "paused" | "completed" | "failed",
  "started_at": iso8601,
  "updated_at": iso8601,
  "completed_at": iso8601 | null,
  "results": [                    # 已跑完的每个 doc 的简要结果
    {"document_id": N, "final_status": "succeeded|failed|skipped",
     "source_slug": str|null, "error": str|null}
  ]
}

主入口:
- start_batch(document_ids, pause_every) → batch_id
- get_state(batch_id) → dict | None
- run_chunk(batch_id) → 跑下一段(直到 next_index 触达 pause_every 倍数 或 全跑完)
- continue_batch(batch_id) → 显式恢复(等价于 run_chunk)
- list_batches() → list[summary]
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from loguru import logger

from app.core.config import settings


_BATCH_LOCK = Lock()


def _batches_dir() -> Path:
    p = Path(settings.cache_dir) / "batches"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_path(batch_id: str) -> Path:
    return _batches_dir() / f"{batch_id}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_batch_id(document_ids: list[int]) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    # 用 ms 避让同秒并发碰撞
    ms = int((time.time() * 1000) % 1000)
    return f"batch-{ts}-{ms:03d}-{len(document_ids)}"


def _save(state: dict[str, Any]) -> None:
    state["updated_at"] = _now_iso()
    p = _state_path(state["batch_id"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _load(batch_id: str) -> dict[str, Any] | None:
    p = _state_path(batch_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning(f"batch_state: corrupt JSON in {p}, returning None")
        return None


def start_batch(*, document_ids: list[int], pause_every: int = 5) -> str:
    """登记一个新批,初始化为 running;不立刻跑。"""
    if not document_ids:
        raise ValueError("document_ids must be non-empty")
    if pause_every < 1:
        raise ValueError("pause_every must be >= 1")

    with _BATCH_LOCK:
        batch_id = _make_batch_id(document_ids)
        state = {
            "batch_id": batch_id,
            "document_ids": list(document_ids),
            "pause_every": int(pause_every),
            "next_index": 0,
            "status": "running",
            "started_at": _now_iso(),
            "updated_at": _now_iso(),
            "completed_at": None,
            "results": [],
        }
        _save(state)
        return batch_id


def get_state(batch_id: str) -> dict[str, Any] | None:
    return _load(batch_id)


def list_batches(limit: int = 50) -> list[dict[str, Any]]:
    """按修改时间倒序列出所有批的摘要。"""
    out: list[dict[str, Any]] = []
    for p in sorted(_batches_dir().glob("batch-*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({
            "batch_id": data.get("batch_id"),
            "status": data.get("status"),
            "total": len(data.get("document_ids") or []),
            "next_index": data.get("next_index", 0),
            "succeeded": sum(1 for r in data.get("results", []) if r.get("final_status") == "succeeded"),
            "skipped": sum(1 for r in data.get("results", []) if r.get("final_status") == "skipped"),
            "failed": sum(1 for r in data.get("results", []) if r.get("final_status") == "failed"),
            "started_at": data.get("started_at"),
            "updated_at": data.get("updated_at"),
        })
        if len(out) >= limit:
            break
    return out


async def run_chunk(batch_id: str) -> dict[str, Any]:
    """跑下一段:从 next_index 开始,直到处理完 pause_every 个 或 全跑完。

    返回最新 state。状态变更:
      - 跑完 pause_every 个但还有剩余 → status=paused,等用户调 continue
      - 全跑完 → status=completed
      - 任意一个失败 → 那个 doc 标 failed 进 results,但批次继续
    """
    # 延迟 import 避免循环依赖(pipeline 也会 import 本模块)
    from app.services.ingestion.pipeline import run_pipeline

    state = _load(batch_id)
    if state is None:
        raise FileNotFoundError(f"batch {batch_id!r} not found")

    if state["status"] == "completed":
        return state
    if state["status"] == "running" and state.get("results"):
        # 之前已经在跑,可能崩溃后恢复
        pass
    state["status"] = "running"
    _save(state)

    document_ids = state["document_ids"]
    pause_every = int(state["pause_every"])
    start = int(state["next_index"])

    chunk_processed = 0
    for i in range(start, len(document_ids)):
        doc_id = document_ids[i]
        try:
            result = await run_pipeline(document_id=doc_id)
            state["results"].append(
                {
                    "document_id": doc_id,
                    "final_status": result.get("final_status"),
                    "source_slug": result.get("source_slug"),
                    "error": _first_error(result),
                }
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(f"batch {batch_id} doc#{doc_id} crashed: {e}")
            state["results"].append(
                {
                    "document_id": doc_id,
                    "final_status": "failed",
                    "source_slug": None,
                    "error": str(e),
                }
            )

        state["next_index"] = i + 1
        chunk_processed += 1

        # 间断检查 + 暂停
        if state["next_index"] >= len(document_ids):
            state["status"] = "completed"
            state["completed_at"] = _now_iso()
            _save(state)
            return state

        if chunk_processed >= pause_every:
            state["status"] = "paused"
            _save(state)
            return state

    state["status"] = "completed"
    state["completed_at"] = _now_iso()
    _save(state)
    return state


async def continue_batch(batch_id: str) -> dict[str, Any]:
    """显式继续一个 paused 批。等价于 run_chunk。"""
    state = _load(batch_id)
    if state is None:
        raise FileNotFoundError(f"batch {batch_id!r} not found")
    if state["status"] == "completed":
        return state
    return await run_chunk(batch_id)


def cancel_batch(batch_id: str) -> dict[str, Any]:
    """终止一个批(状态置 cancelled)。"""
    state = _load(batch_id)
    if state is None:
        raise FileNotFoundError(f"batch {batch_id!r} not found")
    if state["status"] in ("completed", "cancelled"):
        return state
    state["status"] = "cancelled"
    state["completed_at"] = _now_iso()
    _save(state)
    return state


def _first_error(result: dict[str, Any]) -> str | None:
    for j in result.get("jobs") or []:
        if j.get("status") == "failed" and j.get("error"):
            return f"[{j.get('stage')}] {j.get('error')}"
    return None
