"""检索与评分共用的关键词清洗。

``basic_keywords`` 会把连续中文当成一个 token，"工时研究在智能制造背景下的演进与局限"
因此只产出一个整句 token：检索侧只有"智能制造"被别名扩展命中，真正的主题词
"工时研究"从未进入检索；评分侧则把整句判成未命中，误报缺口。

这里把整句 token 沿结构词切成领域词，并丢掉不可能在正文里原样出现的超长 token。
本模块必须保持纯函数、零依赖（不引入 scoring / DB / LLM / I/O），供检索与评分共用。
"""

from __future__ import annotations

import re

MAX_TERM_LEN = 12
MIN_TERM_LEN = 2

_TOKEN_RE = re.compile(r"[一-鿿]+|[A-Za-z][A-Za-z0-9\-]*")

# 问句包装词：切开后允许出现空片段（"精益生产如何实施" → 精益生产 / 实施）
_WRAPPER_RE = re.compile(
    r"(?:请问|能否|可以|麻烦|帮我|介绍一下|说明一下|分析一下|"
    r"是什么|有什么|有哪些|包括哪些|应该|怎么样|怎样|怎么|如何|哪些|什么|是否|吗|呢)"
)

# 连接词/介词：只有切开后每段都还是有效词才切，避免拆坏"在制品""参与""到达时间"
_CONNECTORS = (
    "背景下", "情况下", "条件下", "环境下", "过程中",
    "以及", "对于", "关于", "针对", "基于", "或者",
    "在", "和", "与", "及", "或", "到",
)

_TRAILING_LOCATIVES = ("里", "中", "内", "上", "下")

_STOPWORDS = {
    "介绍", "说明", "回答", "问题", "一下", "请问", "帮我",
    "一个", "一种", "一些", "什么", "怎样", "怎么", "如何", "为什么",
    "是否", "能否", "可以", "应该", "哪些", "以及", "而且", "但是",
    "因此", "所以", "例如", "比如", "然后", "这个", "那个", "它们",
    "我们", "你们", "自己", "各种", "非常", "particular",
}


def _split_on_connectors(term: str) -> list[str]:
    parts = [term]
    for connector in _CONNECTORS:
        nxt: list[str] = []
        for part in parts:
            if connector not in part:
                nxt.append(part)
                continue
            pieces = part.split(connector)
            if all(len(piece) >= MIN_TERM_LEN for piece in pieces):
                nxt.extend(pieces)
            else:
                nxt.append(part)
        parts = nxt
    return parts


def _normalize(term: str) -> str:
    term = term.strip(" ，。！？?、：:；;“”\"'()（）的与和及")
    for suffix in _TRAILING_LOCATIVES:
        if term.endswith(suffix) and len(term) - len(suffix) >= MIN_TERM_LEN:
            term = term[: -len(suffix)]
            break
    return term


# 句首系词残留:问句包装词被切开后常留下一个"是"字(如 "是标准工时")。
# 它不可能出现在答案正文里,不剥掉的话 question_coverage 对好答案恒判 0。
_LEADING_COPULAS = (
    "什么叫做", "什么是", "是不是", "是否是", "是什么", "怎么样",
    "为什么", "是否", "啥是", "怎么", "如何", "请问", "是", "做",
)


def _strip_leading_copula(term: str) -> str:
    for pre in _LEADING_COPULAS:
        if term.startswith(pre):
            rest = term[len(pre):]
            if len(rest) >= MIN_TERM_LEN:
                return rest
    return term


def meaningful_keywords(text: str) -> list[str]:
    """把问题/关键词文本清成可检索、可评价的领域词，去重保序。

    丢弃问句包装词、结构性碎片，以及长于 ``MAX_TERM_LEN`` 的整句 token
    （这类 token 不可能在正文里原样出现，按字面统计只会制造假阴性）。
    """
    out: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN_RE.finditer(text or ""):
        token = match.group(0).lower()
        for chunk in _split_on_connectors(token):
            parts = _WRAPPER_RE.split(chunk) if _WRAPPER_RE.search(chunk) else [chunk]
            for part in parts:
                term = _strip_leading_copula(_normalize(part))
                if len(term) < MIN_TERM_LEN or len(term) > MAX_TERM_LEN:
                    continue
                if term in _STOPWORDS or term in seen:
                    continue
                seen.add(term)
                out.append(term)
    return out
