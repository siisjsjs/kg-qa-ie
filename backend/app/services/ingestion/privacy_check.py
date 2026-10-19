"""ingestion/privacy_check.py:上传前隐私自查清单。

按规划文档 §3.1.3 设计:
  - 6 类敏感正则:phone / id_card / email / api_key / bank_card / url_token
  - block 级:身份证 / API key / URL token — 必须改后再传,不允许跳过
  - warn 级:手机号 / 邮箱 / 银行卡 — 前端弹窗确认后放行

返回 {ok, hits, block_count, warn_count}。
"""

from __future__ import annotations

import re

# 敏感模式 → (level, label)
PATTERNS: list[tuple[str, re.Pattern[str], str, str]] = [
    (
        "phone",
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        "warn",
        "中国大陆手机号",
    ),
    (
        "id_card",
        re.compile(
            r"(?<!\d)[1-9]\d{5}(18|19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
        ),
        "block",
        "中国大陆身份证号",
    ),
    (
        "email",
        re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
        "warn",
        "邮箱地址",
    ),
    (
        "api_key",
        re.compile(r"(sk-|gsk_|xai-|hf_|ghp_|github_pat_)[A-Za-z0-9_\-]{16,}"),
        "block",
        "常见 API key 前缀",
    ),
    (
        "bank_card",
        re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
        "warn",
        "疑似银行卡号(易误报)",
    ),
    (
        "url_token",
        re.compile(r"[?&](token|access_token|api_key)=[A-Za-z0-9_\-]{8,}"),
        "block",
        "URL 中包含 token",
    ),
]


def _mask(snippet: str, *, keep_head: int = 3, keep_tail: int = 2) -> str:
    """对 snippet 做掩码,保留头尾,中间替换为 *。"""
    if len(snippet) <= keep_head + keep_tail:
        return "*" * len(snippet)
    return snippet[:keep_head] + "*" * (len(snippet) - keep_head - keep_tail) + snippet[-keep_tail:]


def scan(content: str) -> dict:
    """扫描 md 全文,返回隐私检查结果。

    Returns:
        {
          "ok": bool,                   # 没有 block 级命中即 True(warn 不影响)
          "hits": [{line, category, snippet, level, label}, ...],
          "block_count": int,
          "warn_count": int,
        }
    """
    hits: list[dict] = []
    if not content:
        return {"ok": True, "hits": [], "block_count": 0, "warn_count": 0}

    lines = content.splitlines()
    for line_no, line in enumerate(lines, start=1):
        for category, pat, level, label in PATTERNS:
            for m in pat.finditer(line):
                hits.append({
                    "line": line_no,
                    "category": category,
                    "snippet": _mask(m.group(0)),
                    "level": level,
                    "label": label,
                })

    block_count = sum(1 for h in hits if h["level"] == "block")
    warn_count = sum(1 for h in hits if h["level"] == "warn")
    return {
        "ok": block_count == 0,
        "hits": hits,
        "block_count": block_count,
        "warn_count": warn_count,
    }
