from __future__ import annotations

import pytest

from app.services.query import followups


class _BrokenProvider:
    async def chat(self, *args, **kwargs):
        raise RuntimeError("temporary provider error")


class _MalformedProvider:
    async def chat(self, *args, **kwargs):
        return "not json"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [_BrokenProvider(), _MalformedProvider()])
async def test_generate_followups_always_has_fallback(monkeypatch, provider):
    monkeypatch.setattr(followups, "get_llm_provider", lambda: provider)
    result = await followups.generate_followups(
        question="5S管理应该如何实施？",
        answer="先整理，再整顿，并建立检查机制。",
    )
    assert len(result) == 3
    assert all(item.endswith("？") for item in result)
    assert all("5S管理" in item for item in result)


def test_parse_suggestions_keeps_valid_unique_items():
    raw = '["如何落地？", "如何评价？", "如何落地？"]'
    assert followups._parse_suggestions(raw) == ["如何落地？", "如何评价？"]


def test_local_followups_are_immediate_and_stable():
    first = followups.generate_followups_local("5S管理应该如何实施？")
    second = followups.generate_followups_local("5S管理应该如何实施？")
    assert first == second
    assert len(first) == 3
    assert all(item.endswith("？") for item in first)
