"""query/router.py:正则快路径路由 + 问题形状识别(Agentic 改造后)。

改造计划 §6.4:LLM 分类调用已删除(省 1-2s),只保留:
- `_fast_path`:明显的招呼/感谢/告别/自我介绍 → chitchat,不查 wiki
- `_fast_kb_kind`:按提问形状给 kind(持久化分目录 + 前端展示用),
  与领域无关,不用于路由决策
其余问题一律进 Agent 主链路,域外/闲聊边界由 agent_system.md 的
「特殊情况处理」约束模型自行判断。
"""

from __future__ import annotations

import re

ROUTE_KB = "knowledge_base"
ROUTE_CHITCHAT = "chitchat"
ROUTE_OUT_OF_DOMAIN = "out_of_domain"

# 问题类型(持久化分目录/前端展示用)。兜底 general。
DEFAULT_KIND = "general"
_VALID_KINDS = {"comparison", "timeline", "deep_dive", "concept", "method", "general"}

# ---- 快路径:正则白名单,省一次 LLM 往返 ----
# 命中条件:输入很短(去掉空白后 <=12 字)且整句完全是问候/感谢/告别/系统介绍类。
# 设计原则:宁可漏(进 Agent 主链路)也不要把 IE 问题误判成闲聊。
_FAST_CHITCHAT_PATTERNS = [
    re.compile(r"^(你好|您好|嗨|hi|hello|hey|哈喽|哈罗|早安|早上好|晚安|下午好|晚上好)[!!~。.\s]*$", re.IGNORECASE),
    re.compile(r"^(在吗|在么|有人吗|有人么)[??!!~。.\s]*$"),
    re.compile(r"^(谢谢|感谢|多谢|thanks?|thank\s*you|thx|3q)[!!~。.\s]*$", re.IGNORECASE),
    re.compile(r"^(再见|拜拜|bye|byebye|88|回见)[!!~。.\s]*$", re.IGNORECASE),
    re.compile(r"^(你是谁|你叫什么|你叫啥|你是什么)[??!!~。.\s]*$"),
    re.compile(r"^(你能(干|做)(什么|啥)|你会(干|做)(什么|啥)|怎么用你|帮助|help)[??!!~。.\s]*$", re.IGNORECASE),
    re.compile(r"^(介绍一下你(自己)?|自我介绍|你能(介绍|说说)(下|一下)?(自己)?)[??!!~。.\s]*$"),
    re.compile(r"^(测试|test|ping)[!!~。.\s]*$", re.IGNORECASE),
]


def _fast_path(question: str) -> str | None:
    """规则命中返 'chitchat',否则返 None 进 Agent 主链路。"""
    q = question.strip()
    if not q or len(q) > 12:
        return None
    for pat in _FAST_CHITCHAT_PATTERNS:
        if pat.match(q):
            return ROUTE_CHITCHAT
    return None


_FAST_KB_KIND_PATTERNS = (
    ("comparison", re.compile(r"(?:对比|比较|区别|差异|异同|哪个好|\bvs\b)", re.IGNORECASE)),
    ("timeline", re.compile(r"(?:历史|发展历程|演变|起源|时间线)")),
    ("deep_dive", re.compile(r"(?:深入|深度|系统分析|全面分析|详细剖析)")),
    ("method", re.compile(r"(?:如何|怎么|怎样|步骤|实施|落地|操作|流程|应用方法)")),
    ("concept", re.compile(r"(?:什么是|是什么|定义|概念|含义)")),
)


def _fast_kb_kind(question: str) -> str | None:
    """Classify obvious KB question shapes without an extra LLM round trip."""
    q = question.strip()
    for kind, pattern in _FAST_KB_KIND_PATTERNS:
        if pattern.search(q):
            return kind
    return None
