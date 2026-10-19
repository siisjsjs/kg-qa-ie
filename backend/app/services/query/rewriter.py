"""query/rewriter.py:多轮问答的指代消解。

应用场景:
  用户先问 "VSM 是什么?",再问 "它的局限性?"
  原 grep 用 "它/局限性" 找不到正确页面;rewriter 把它改写成
  "VSM 的局限性是什么?",再交给 grep。

设计:
  1. 正则白名单检测指代词。命中才调 LLM,通常 2/3 的提问根本不需要改写。
  2. 仅在 history 非空时启用(纯首问跳过)。
  3. LLM 失败回退原 question,绝不阻塞主链路。
  4. 改写结果只用于 grep / alias_expand / scoring;LLM 综合 prompt 仍用原 question + history,
     避免改写本身丢上下文风格。
"""

from __future__ import annotations

import re

from app.services.llm import ChatMessage, get_llm_provider

# 中文/英文的常见指代触发词。命中任意一个 → 走 LLM 改写
_PRONOUN_TOKENS = (
    "它", "他", "她", "它们", "他们", "她们",
    "这个", "那个", "这些", "那些", "这", "那",
    "上面", "上述", "前面", "刚才", "之前",
    "继续", "再讲", "再说", "更多", "下一",
    "is it", "they", "them", "this", "that", "these", "those",
    "more on", "go on", "continue",
)


def needs_rewrite(question: str, history: list[dict] | None) -> bool:
    """判断是否需要走 LLM 改写。"""
    if not history:
        return False
    q = question.strip()
    if not q:
        return False
    # 短句优先怀疑(<=12 字 + 含指代词)
    if len(q) <= 24:
        for tok in _PRONOUN_TOKENS:
            if tok in q:
                return True
        # 短句但不含已知名词,且历史非空 → 也可能省略主语
        # 这里取保守:只有命中显式指代词才返 True
        return False
    # 长句也可能含指代,但概率低,保守跳过省时间
    for tok in ("它的", "他的", "她的", "上述", "前面提到", "刚才说的", "之前讲的"):
        if tok in q:
            return True
    return False


def _build_rewrite_prompt(question: str, history: list[dict], summary: str | None) -> str:
    # 取最近 8 轮上下文,避免 prompt 过长但保证语义完整
    tail = history[-16:]
    convo_lines: list[str] = []
    for h in tail:
        role = "用户" if h.get("role") == "user" else "助手"
        content = (h.get("content") or "").strip()
        if not content:
            continue
        if len(content) > 1000:
            content = content[:1000] + "…"
        convo_lines.append(f"{role}: {content}")
    convo = "\n".join(convo_lines) or "(无)"
    summary_block = ""
    if summary and summary.strip():
        summary_block = f"\n## 更早历史的摘要\n{summary.strip()}\n"
    return (
        "下面是一段工业工程知识库问答的多轮对话。当前用户的最新一句话可能含有指代词或省略,"
        "请把它改写成一句完整、自包含的问题(替换 它/这个/上述 等指代,补回省略的主语),"
        "便于后续在 wiki 中检索。\n\n"
        "硬性约束:\n"
        "- 只输出改写后的一句话,不要解释,不要前缀,不要标点性的引号。\n"
        "- 改写后语义必须等同原句,不要凭空增加新条件。\n"
        "- 如果原句已经完整自包含(主语清晰、无指代),直接原样返回。\n"
        f"{summary_block}\n"
        f"## 最近对话\n{convo}\n\n"
        f"## 当前用户的话\n{question}\n\n"
        "## 改写后的完整问题\n"
    )


async def rewrite(
    *,
    question: str,
    history: list[dict] | None = None,
    summary: str | None = None,
) -> tuple[str, bool]:
    """返回 (改写后的 question, 是否真的改写过)。

    任何异常 / 改写结果为空 / 与原 question 等价 → 返 (question, False)
    """
    if not needs_rewrite(question, history):
        return question, False
    prompt = _build_rewrite_prompt(question, history or [], summary)
    try:
        provider = get_llm_provider()
        text = await provider.chat(
            [
                ChatMessage(
                    role="system",
                    content="你是一个负责重写用户提问的助手,只输出一句话改写结果。",
                ),
                ChatMessage(role="user", content=prompt),
            ],
            stream=False,
            temperature=0.0,
            max_tokens=1024,
        )
    except Exception:  # noqa: BLE001
        return question, False
    if not isinstance(text, str):
        return question, False
    cleaned = text.strip().strip("`'\"").splitlines()[0].strip() if text else ""
    # 截掉模型偶尔会带的"改写后:"前缀
    for prefix in ("改写后:", "改写:", "改写后的问题:", "Rewrite:", "Rewritten:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    if not cleaned:
        return question, False
    if cleaned == question.strip():
        return question, False
    # 长度检查:防止模型啰嗦扩展(常出现"是的,这里给您改写...")
    if len(cleaned) > max(200, len(question) * 4):
        return question, False
    return cleaned, True
