"""query/alias_expand.py:基于 .wiki-schema.md 别名词表的同义扩展。

规则:用户问题中含有某别名组的任一成员 → 该组所有同义词都纳入搜索关键词。
不跨组传递、自动去重、保序(原始问题词在前)。

同时返回基础关键词(从问题里抽出的中文段 + 英文 token),作为 fallback。

别名匹配的边界规则:别名首/尾字符是 ASCII 字母数字时,该侧要求词边界。
词表里有 16 个单字母动素码(P/R/I/D/G/A/U/F/H/Y),裸子串匹配会让它们命中
"TCP"/"Rust"/"API" 这类英文词的内部,把整组同义词(生产率/定位/休息…)
灌进检索关键词。纯中文别名仍按子串匹配——中文没有词边界。
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.services.query.keywords import meaningful_keywords

# 中文连续字符 / 英文 token / 英文带破折号短语
_TOKEN_PATTERN = re.compile(
    r"[一-鿿]{2,}|[A-Za-z][A-Za-z0-9\-]+",
)

_ASCII_ALNUM = re.compile(r"[A-Za-z0-9]")


def basic_keywords(question: str) -> list[str]:
    """从问题里抽连续中文 + 英文 token,长度去重保序。

    例:"VSM 怎么应用到精益生产里?" → ["VSM", "怎么应用到精益生产里", "精益生产"] 之类。
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in _TOKEN_PATTERN.finditer(question):
        tok = m.group(0)
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


@lru_cache(maxsize=4096)
def _member_pattern(member: str) -> re.Pattern[str] | None:
    """别名的边界感知正则;纯中文别名返回 None(走子串匹配)。"""
    if not _ASCII_ALNUM.search(member):
        return None
    prefix = r"(?<![A-Za-z0-9])" if _ASCII_ALNUM.match(member[0]) else ""
    suffix = r"(?![A-Za-z0-9])" if _ASCII_ALNUM.match(member[-1]) else ""
    return re.compile(prefix + re.escape(member) + suffix)


def _member_matches(member: str, question: str) -> bool:
    pattern = _member_pattern(member)
    if pattern is None:
        return member in question
    return pattern.search(question) is not None


def expand(question: str, *, alias_groups: list[list[str]]) -> tuple[list[str], list[str]]:
    """返回 (expanded_keywords, hit_aliases)。

    - 基础关键词来自 basic_keywords(question)
    - 对于每个别名组,只要问题中包含任一成员 → 整组追加(去重保序,原序在前)
    - hit_aliases 列出"命中的别名组的 canonical(group[0])"
    """
    # basic_keywords 把连续中文抓成整段,问题主语会被整句吞掉——"工时研究在智能制造
    # 背景下的演进与局限"只得到 1 个整句 token,"工时研究"从未进过检索。
    # meaningful_keywords 能切出领域词,但它是评分侧的过滤器,会丢掉超长 token
    # (同一句里的"精益生产""六西格玛"就这么没的),所以两者取并集而不是替换。
    base = basic_keywords(question)
    seen: set[str] = set(base)
    out: list[str] = list(base)
    for term in meaningful_keywords(question):
        if term and term not in seen:
            seen.add(term)
            out.append(term)
    hit_canonicals: list[str] = []

    for group in alias_groups:
        if not group:
            continue
        if any(member and _member_matches(member, question) for member in group):
            canonical = group[0]
            if canonical not in hit_canonicals:
                hit_canonicals.append(canonical)
            for member in group:
                if member and member not in seen:
                    seen.add(member)
                    out.append(member)
    return out, hit_canonicals
