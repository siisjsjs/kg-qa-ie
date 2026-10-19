"""conversations 包:多轮对话会话与消息持久化。

- store.py:CRUD + token 估算 + 摘要触发判断
"""

from app.services.conversations.store import (
    SUMMARIZE_KEEP_RECENT,
    SUMMARIZE_TOKEN_THRESHOLD,
    add_assistant_message,
    add_user_message,
    create_session,
    delete_session,
    estimate_tokens,
    force_resummarize,
    get_session,
    list_messages,
    list_sessions,
    load_history_payload,
    maybe_auto_summarize,
    rename_session,
    set_summary,
    should_summarize,
    summarize_with_llm,
)

__all__ = [
    "SUMMARIZE_KEEP_RECENT",
    "SUMMARIZE_TOKEN_THRESHOLD",
    "add_assistant_message",
    "add_user_message",
    "create_session",
    "delete_session",
    "estimate_tokens",
    "force_resummarize",
    "get_session",
    "list_messages",
    "list_sessions",
    "load_history_payload",
    "maybe_auto_summarize",
    "rename_session",
    "set_summary",
    "should_summarize",
    "summarize_with_llm",
]
