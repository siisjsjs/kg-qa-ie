from __future__ import annotations

import asyncio

import pytest

from app.services.query.inflight import (
    InflightQueryManager,
    make_inflight_key,
    normalize_question,
)


async def _collect(stream):
    return [event async for event in stream]


def test_key_normalizes_question_and_mapping_order():
    common = {
        "user_id": 7,
        "history": [{"role": "user", "content": "earlier"}],
        "summary": "summary",
        "scope": {"wiki_version": "42"},
    }
    first = make_inflight_key(
        question="  什么是  VSM？ ",
        model_config={"temperature": 0.2, "model": "deepseek-chat"},
        **common,
    )
    second = make_inflight_key(
        question="什么是 VSM?",
        model_config={"model": "deepseek-chat", "temperature": 0.2},
        **common,
    )

    assert normalize_question("  ＶＳＭ\n是什么 ") == "vsm 是什么"
    # NFKC canonicalizes the full-width question mark in the first input.
    assert first == second


@pytest.mark.parametrize(
    "changed_field", ["user_id", "question", "history", "summary", "scope", "model_config"]
)
def test_key_separates_answer_affecting_state(changed_field):
    base = {
        "user_id": "user-a",
        "question": "什么是 VSM？",
        "history": [{"role": "user", "content": "one"}],
        "summary": "summary-a",
        "scope": "wiki-v1",
        "model_config": {"model": "m1", "temperature": 0.2},
    }
    changed = dict(base)
    changed[changed_field] = {
        "user_id": "user-b",
        "question": "什么是 ECRS？",
        "history": [{"role": "user", "content": "two"}],
        "summary": "summary-b",
        "scope": "wiki-v2",
        "model_config": {"model": "m2", "temperature": 0.2},
    }[changed_field]

    assert make_inflight_key(**base) != make_inflight_key(**changed)


@pytest.mark.asyncio
async def test_concurrent_subscribers_share_producer_and_receive_live_events():
    manager: InflightQueryManager[str] = InflightQueryManager()
    calls = 0
    first_event = asyncio.Event()
    release = asyncio.Event()

    async def producer():
        nonlocal calls
        calls += 1
        yield "token-1"
        first_event.set()
        await release.wait()
        yield "token-2"

    subscribers = [
        asyncio.create_task(_collect(manager.stream("same", producer)))
        for _ in range(4)
    ]
    await first_event.wait()
    await asyncio.sleep(0)
    release.set()

    results = await asyncio.gather(*subscribers)
    assert calls == 1
    assert results == [["token-1", "token-2"]] * 4
    assert manager.active_count == 0


@pytest.mark.asyncio
async def test_different_keys_do_not_share_producers():
    manager: InflightQueryManager[str] = InflightQueryManager()
    calls = 0

    async def producer():
        nonlocal calls
        calls += 1
        yield "done"

    results = await asyncio.gather(
        _collect(manager.stream("a", producer)),
        _collect(manager.stream("b", producer)),
    )
    assert calls == 2
    assert results == [["done"], ["done"]]
    assert manager.active_count == 0


@pytest.mark.asyncio
async def test_producer_error_reaches_all_subscribers_after_replayed_events():
    manager: InflightQueryManager[str] = InflightQueryManager()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def producer():
        nonlocal calls
        calls += 1
        yield "partial"
        started.set()
        await release.wait()
        raise ValueError("provider failed")
        yield  # pragma: no cover

    async def consume():
        events = []
        with pytest.raises(ValueError, match="provider failed"):
            async for event in manager.stream("same", producer):
                events.append(event)
        return events

    leader = asyncio.create_task(consume())
    await started.wait()
    follower = asyncio.create_task(consume())
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(leader, follower) == [["partial"], ["partial"]]
    assert calls == 1
    assert manager.active_count == 0


@pytest.mark.asyncio
async def test_last_subscriber_leaving_cancels_and_cleans_up_producer():
    manager: InflightQueryManager[str] = InflightQueryManager()
    cancelled = asyncio.Event()

    async def producer():
        try:
            yield "first"
            await asyncio.Future()
        finally:
            cancelled.set()

    stream = manager.stream("abandoned", producer)
    assert await anext(stream) == "first"
    await stream.aclose()
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    assert manager.active_count == 0


@pytest.mark.asyncio
async def test_completed_flight_is_not_a_cross_request_cache():
    manager: InflightQueryManager[int] = InflightQueryManager()
    calls = 0

    async def producer():
        nonlocal calls
        calls += 1
        yield calls

    assert await _collect(manager.stream("same", producer)) == [1]
    assert await _collect(manager.stream("same", producer)) == [2]
    assert calls == 2
