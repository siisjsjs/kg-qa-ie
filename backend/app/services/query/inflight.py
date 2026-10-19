"""In-process coalescing for identical streaming query requests.

The registry deliberately keeps results only while a producer is running.  It
is not an answer cache: completed flights are removed immediately, and the
caller must include all state that can change an answer in the key.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import unicodedata
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar


EventT = TypeVar("EventT")

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_question(question: str) -> str:
    """Return the canonical question form used for in-flight matching."""

    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", question)).strip().casefold()


def _json_fingerprint(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def make_inflight_key(
    *,
    user_id: str | int,
    question: str,
    history: Any = None,
    summary: Any = None,
    scope: Any,
    model_config: Any,
) -> str:
    """Build a privacy-safe key for an answer-producing request.

    ``scope`` should identify the knowledge-base snapshot (for example an
    index version or content revision). ``model_config`` should contain every
    model setting that can affect output. History order is intentionally
    preserved, while mapping key order is canonicalized.
    """

    material = {
        "user": str(user_id),
        "question": normalize_question(question),
        "history": _json_fingerprint(history),
        "summary": _json_fingerprint(summary),
        "scope": _json_fingerprint(scope),
        "model": _json_fingerprint(model_config),
    }
    return f"query:v1:{_json_fingerprint(material)}"


@dataclass(slots=True)
class _Flight(Generic[EventT]):
    events: list[EventT] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    done: bool = False
    error: BaseException | None = None
    subscribers: int = 0
    task: asyncio.Task[None] | None = None


class InflightQueryManager(Generic[EventT]):
    """Broadcast one async producer to all concurrent subscribers.

    A subscriber joining an active flight replays events already produced and
    then follows new events live. Once production finishes, the registry entry
    is removed; this keeps the class an in-flight deduplicator rather than a
    cross-request cache.
    """

    def __init__(self) -> None:
        self._flights: dict[str, _Flight[EventT]] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def active_count(self) -> int:
        return len(self._flights)

    async def stream(
        self,
        key: str,
        producer: Callable[[], AsyncIterator[EventT]],
    ) -> AsyncIterator[EventT]:
        """Subscribe to ``key``, starting ``producer`` only for the leader."""

        flight = await self._join(key, producer)
        index = 0
        try:
            while True:
                async with flight.condition:
                    await flight.condition.wait_for(
                        lambda: index < len(flight.events) or flight.done
                    )
                    if index < len(flight.events):
                        event = flight.events[index]
                        index += 1
                    elif flight.error is not None:
                        raise flight.error
                    else:
                        break
                yield event
        finally:
            await self._leave(key, flight)

    async def close(self) -> None:
        """Cancel active producers and make the manager unavailable."""

        async with self._lock:
            self._closed = True
            flights = list(self._flights.values())
            self._flights.clear()
        tasks = [flight.task for flight in flights if flight.task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _join(
        self,
        key: str,
        producer: Callable[[], AsyncIterator[EventT]],
    ) -> _Flight[EventT]:
        async with self._lock:
            if self._closed:
                raise RuntimeError("in-flight query manager is closed")
            flight = self._flights.get(key)
            if flight is None:
                flight = _Flight()
                self._flights[key] = flight
                flight.task = asyncio.create_task(self._produce(key, flight, producer))
            flight.subscribers += 1
            return flight

    async def _leave(self, key: str, flight: _Flight[EventT]) -> None:
        task_to_cancel: asyncio.Task[None] | None = None
        async with self._lock:
            flight.subscribers -= 1
            if flight.subscribers == 0 and not flight.done:
                # No client can consume the answer now. Remove first so a task
                # cancelled before its coroutine starts cannot leak the entry.
                if self._flights.get(key) is flight:
                    self._flights.pop(key, None)
                task_to_cancel = flight.task
        if task_to_cancel is not None:
            task_to_cancel.cancel()

    async def _produce(
        self,
        key: str,
        flight: _Flight[EventT],
        producer: Callable[[], AsyncIterator[EventT]],
    ) -> None:
        error: BaseException | None = None
        try:
            async for event in producer():
                async with flight.condition:
                    flight.events.append(event)
                    flight.condition.notify_all()
        except BaseException as exc:
            error = exc
        finally:
            async with flight.condition:
                flight.error = error
                flight.done = True
                flight.condition.notify_all()
            async with self._lock:
                if self._flights.get(key) is flight:
                    self._flights.pop(key, None)


__all__ = ["InflightQueryManager", "make_inflight_key", "normalize_question"]
