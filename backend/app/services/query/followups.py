"""Generate 2-3 follow-up questions after a knowledge-base answer."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from loguru import logger

from app.services.llm import ChatMessage, get_llm_provider

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

_ANSWER_EXCERPT_CHARS = 1500
_MAX_SUGGESTIONS = 3
_MAX_LEN_PER_ITEM = 60
_MAX_TOPIC_CHARS = 24
_LLM_TIMEOUT_SECONDS = 5.0
# 推理模型的 reasoning_tokens 也算在 max_tokens 里，给 256 会被思考耗尽导致 content 为空。
_MAX_TOKENS = 768
_ARRAY_PATTERN = re.compile(r"\[.*\]", re.DOTALL)
_QUESTION_WRAPPERS = re.compile(
    r"(?:请问|什么是|是什么|有哪些|有什么|如何|怎么|怎样|为什么|是否|吗|呢|？|\?|。)"
)
_REPEATED_PHRASE = re.compile(r"(.{2,20}?)\1+")
_TOPIC_TRIM = " ，,、:："
_NORMALIZE_TRIM = " ，,、:：。.？?！!"


@lru_cache(maxsize=1)
def _template() -> str:
    """延迟读取模板，保证 import 阶段无文件 IO。"""
    return (PROMPTS_DIR / "followup_suggestions.md").read_text(encoding="utf-8")


def _collapse_repeats(text: str) -> str:
    """折叠连续重复的短语，避免关键词堆砌原样进入建议。"""
    previous = ""
    while previous != text:
        previous = text
        text = _REPEATED_PHRASE.sub(r"\1", text)
    return text


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).strip(_NORMALIZE_TRIM).casefold()


def _clean_items(values: Iterable[object], *, question: str = "") -> list[str]:
    """去重、限长，并排除与原问题雷同的条目。"""
    blocked: set[str] = set()
    question_key = _normalize(question)
    if question_key:
        blocked.add(question_key)
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        suggestion = value.strip().strip("。").strip()
        key = _normalize(suggestion)
        if not key or len(suggestion) > _MAX_LEN_PER_ITEM or key in blocked:
            continue
        blocked.add(key)
        result.append(suggestion)
        if len(result) >= _MAX_SUGGESTIONS:
            break
    return result


def _extract_array(raw: str) -> list | None:
    """取出输出里的 JSON 数组；解析不出数组时返回 None。"""
    if not raw:
        return None
    match = _ARRAY_PATTERN.search(raw.strip())
    if not match:
        return None
    try:
        values = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    return values if isinstance(values, list) else None


def _parse_suggestions(raw: str, *, question: str = "") -> list[str]:
    values = _extract_array(raw)
    return [] if values is None else _clean_items(values, question=question)


def _fallback_suggestions(question: str) -> list[str]:
    topic = _QUESTION_WRAPPERS.sub("", question).strip(_TOPIC_TRIM)
    topic = _collapse_repeats(topic[:120]).strip(_TOPIC_TRIM)
    if not topic:
        topic = "这个方法"
    topic = topic[:_MAX_TOPIC_CHARS]
    return _clean_items(
        [
            f"{topic}在实际场景中应如何实施？",
            f"{topic}实施时最常见的问题有哪些？",
            f"如何衡量{topic}的应用效果？",
        ],
        question=question,
    )


def generate_followups_local(question: str) -> list[str]:
    """Return stable suggestions without delaying query completion on another LLM call."""
    question = (question or "").strip()
    return _fallback_suggestions(question) if question else []


async def _llm_suggestions(question: str, answer: str) -> list[str]:
    if not answer:
        return _fallback_suggestions(question)
    prompt = (
        _template()
        .replace("{question}", question)
        .replace("{answer}", answer[:_ANSWER_EXCERPT_CHARS])
    )
    messages = [
        ChatMessage(
            role="system",
            content="你是工业工程知识库的追问建议生成器。只输出一个 JSON 字符串数组。",
        ),
        ChatMessage(role="user", content=prompt),
    ]
    raw = await asyncio.wait_for(
        get_llm_provider().chat(
            messages,
            stream=False,
            temperature=0.6,
            max_tokens=_MAX_TOKENS,
        ),
        timeout=_LLM_TIMEOUT_SECONDS,
    )
    values = _extract_array(raw if isinstance(raw, str) else "")
    if values is None:
        logger.warning("followup generation returned unparseable output, using fallback")
        return _fallback_suggestions(question)
    # 模型判定本轮无追问价值时会返回空数组，此时不再套模板。
    return _clean_items(values, question=question)


async def generate_followups(*, question: str, answer: str = "") -> list[str]:
    """主路径：LLM 生成追问，失败降级模板兜底，彻底失败返回 []；不抛异常。"""
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question:
        return []
    try:
        return await _llm_suggestions(question, answer)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"followup generation failed, using fallback: {type(exc).__name__}: {exc}"
        )
    try:
        return _fallback_suggestions(question)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"followup fallback failed: {exc}")
        return []
