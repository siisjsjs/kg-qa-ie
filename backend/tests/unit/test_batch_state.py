"""ingestion/batch_state 单元测试。"""

from __future__ import annotations

import pytest

from app.core import config as cfg_module
from app.services.ingestion import batch_state as batch_mod


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_module.settings, "cache_dir", str(tmp_path / "cache"))
    return tmp_path / "cache"


def test_start_batch_creates_state(cache_dir):
    bid = batch_mod.start_batch(document_ids=[1, 2, 3], pause_every=2)
    state = batch_mod.get_state(bid)
    assert state is not None
    assert state["batch_id"] == bid
    assert state["document_ids"] == [1, 2, 3]
    assert state["pause_every"] == 2
    assert state["next_index"] == 0
    assert state["status"] == "running"


def test_start_batch_rejects_empty():
    with pytest.raises(ValueError):
        batch_mod.start_batch(document_ids=[], pause_every=5)


def test_start_batch_rejects_pause_every_zero():
    with pytest.raises(ValueError):
        batch_mod.start_batch(document_ids=[1], pause_every=0)


def test_get_state_returns_none_for_missing(cache_dir):
    assert batch_mod.get_state("no-such-batch") is None


def test_list_batches_summary(cache_dir):
    bid1 = batch_mod.start_batch(document_ids=[1, 2], pause_every=5)
    bid2 = batch_mod.start_batch(document_ids=[3], pause_every=5)
    items = batch_mod.list_batches()
    bids = {x["batch_id"] for x in items}
    assert bid1 in bids
    assert bid2 in bids


def test_cancel_batch_changes_status(cache_dir):
    bid = batch_mod.start_batch(document_ids=[1, 2, 3], pause_every=2)
    state = batch_mod.cancel_batch(bid)
    assert state["status"] == "cancelled"
    assert state["completed_at"] is not None


def test_cancel_batch_idempotent_on_completed(cache_dir):
    bid = batch_mod.start_batch(document_ids=[1], pause_every=1)
    # 手动改成 completed
    state = batch_mod.get_state(bid)
    state["status"] = "completed"
    state["completed_at"] = "2026-05-20T00:00:00+00:00"
    from json import dumps

    p = batch_mod._state_path(bid)
    p.write_text(dumps(state, ensure_ascii=False), encoding="utf-8")

    after = batch_mod.cancel_batch(bid)
    assert after["status"] == "completed"


def test_cancel_batch_unknown_raises(cache_dir):
    with pytest.raises(FileNotFoundError):
        batch_mod.cancel_batch("nonexistent")


@pytest.mark.asyncio
async def test_run_chunk_pauses_after_pause_every(cache_dir, monkeypatch):
    # 模拟 run_pipeline 立刻返回 succeeded
    async def _fake(*, document_id, force=False):
        return {"final_status": "succeeded", "source_slug": f"src{document_id}", "jobs": []}

    monkeypatch.setattr("app.services.ingestion.pipeline.run_pipeline", _fake)

    bid = batch_mod.start_batch(document_ids=[1, 2, 3, 4, 5], pause_every=2)
    state = await batch_mod.run_chunk(bid)
    # 跑 2 个,剩余 3 → 状态 paused
    assert state["status"] == "paused"
    assert state["next_index"] == 2
    assert len(state["results"]) == 2
    assert state["results"][0]["final_status"] == "succeeded"


@pytest.mark.asyncio
async def test_continue_batch_resumes_to_completion(cache_dir, monkeypatch):
    async def _fake(*, document_id, force=False):
        return {"final_status": "succeeded", "source_slug": f"src{document_id}", "jobs": []}

    monkeypatch.setattr("app.services.ingestion.pipeline.run_pipeline", _fake)

    bid = batch_mod.start_batch(document_ids=[1, 2, 3], pause_every=2)
    s1 = await batch_mod.run_chunk(bid)
    assert s1["status"] == "paused"

    s2 = await batch_mod.continue_batch(bid)
    assert s2["status"] == "completed"
    assert s2["next_index"] == 3
    assert s2["completed_at"] is not None


@pytest.mark.asyncio
async def test_run_chunk_records_failed_jobs(cache_dir, monkeypatch):
    async def _fake(*, document_id, force=False):
        if document_id == 2:
            return {
                "final_status": "failed",
                "source_slug": None,
                "jobs": [{"stage": "step1_extract", "status": "failed", "error": "bad json"}],
            }
        return {"final_status": "succeeded", "source_slug": f"src{document_id}", "jobs": []}

    monkeypatch.setattr("app.services.ingestion.pipeline.run_pipeline", _fake)

    bid = batch_mod.start_batch(document_ids=[1, 2, 3], pause_every=10)
    state = await batch_mod.run_chunk(bid)
    assert state["status"] == "completed"
    failed = [r for r in state["results"] if r["final_status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["document_id"] == 2
    assert "step1_extract" in failed[0]["error"]


@pytest.mark.asyncio
async def test_run_chunk_handles_pipeline_crash(cache_dir, monkeypatch):
    async def _crash(*, document_id, force=False):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("app.services.ingestion.pipeline.run_pipeline", _crash)

    bid = batch_mod.start_batch(document_ids=[7], pause_every=1)
    state = await batch_mod.run_chunk(bid)
    # 单文档 + crash → 状态机仍走完到 completed,但结果是 failed
    assert state["status"] == "completed"
    assert state["results"][0]["final_status"] == "failed"
    assert "kaboom" in state["results"][0]["error"]


@pytest.mark.asyncio
async def test_continue_batch_unknown_raises(cache_dir):
    with pytest.raises(FileNotFoundError):
        await batch_mod.continue_batch("nonexistent")


@pytest.mark.asyncio
async def test_run_chunk_completes_when_total_under_pause_every(cache_dir, monkeypatch):
    async def _fake(*, document_id, force=False):
        return {"final_status": "succeeded", "source_slug": f"s{document_id}", "jobs": []}

    monkeypatch.setattr("app.services.ingestion.pipeline.run_pipeline", _fake)

    bid = batch_mod.start_batch(document_ids=[1, 2], pause_every=10)
    state = await batch_mod.run_chunk(bid)
    assert state["status"] == "completed"
    assert state["next_index"] == 2
