"""digest/router.py:按触发关键词路由模板。

规划文档 §3.4.1:
| 模板 | 触发关键词 | prompt 模板 |
|------|---------|-----------|
| deep_dive | 深度分析 / 综述 / digest / 全面讲讲 / 系统梳理 / 总览 | digest_deep_dive.md |
| comparison | 对比 / 区别 / 异同 / VS / 比较 / 优劣 | digest_comparison.md |
| timeline | 时间线 / 按时间 / 发展史 / 演进 / 历史 / 演变 | digest_timeline.md |

多关键词同时命中时优先级:`comparison > timeline > deep_dive`
(因为 comparison 和 timeline 结构化要求最强,deep_dive 是兜底)。
"""

from __future__ import annotations

import re
from typing import Literal

DigestMode = Literal["deep_dive", "comparison", "timeline"]

# 中文关键词用 substring 匹配;英文/缩写要 word boundary 防止 "vs" 命中 "VSM"
_COMPARISON_CN = (
    "对比", "区别", "异同", "比较", "优劣", "差异", "对照",
)
_COMPARISON_WORD = re.compile(r"\bvs\b", re.IGNORECASE)

_TIMELINE_CN = (
    "时间线", "按时间", "发展史", "演进", "历史演变", "演变", "时间轴",
)
_DEEP_DIVE_CN = (
    "深度分析", "综述", "全面讲讲", "系统梳理", "总览", "深度",
    "系统讲", "讲透",
)
_DEEP_DIVE_WORD = re.compile(r"\bdigest\b", re.IGNORECASE)


def detect_mode(text: str) -> DigestMode:
    """关键词路由:comparison > timeline > deep_dive(兜底)。"""
    if not text:
        return "deep_dive"
    low = text.lower()
    if any(k in low for k in _COMPARISON_CN) or _COMPARISON_WORD.search(low):
        return "comparison"
    if any(k in low for k in _TIMELINE_CN):
        return "timeline"
    if any(k in low for k in _DEEP_DIVE_CN) or _DEEP_DIVE_WORD.search(low):
        return "deep_dive"
    return "deep_dive"

