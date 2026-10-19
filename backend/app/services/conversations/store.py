"""conversations/store.py:会话与消息的持久化操作。

设计要点:
  - 会话/消息严格按 user_id 隔离,所有 CRUD 都校验 owner
  - QaMessage 用 P6+ 的 role+content 模型;assistant 消息把 citations/quality/path 塞 meta_json
  - load_history_payload 给 query SSE 提供与前端 streamQuery 同形的 (history, summary)
  - should_summarize:用 token 估算(中文≈0.6 tok/char,英文≈0.25 tok/char)而不是字符数
  - summarize_with_llm:复用旧 /summarize 的 prompt,把"较早一半"压成 summary,与现有 prev_summary 合并

阈值:
  SUMMARIZE_TOKEN_THRESHOLD = 4000   触发摘要
  SUMMARIZE_KEEP_RECENT     = 6     压缩后保留近 N 条原文
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.qa import QaMessage, QaSession
from app.services.llm import ChatMessage, get_llm_provider

SUMMARIZE_TOKEN_THRESHOLD = 4000
SUMMARIZE_KEEP_RECENT = 6


# ===== token 估算 =====

def estimate_tokens(text: str) -> int:
    """启发式 token 估算:中文 0.6 tok/char,英文 0.25 tok/char。

    跑 tiktoken 更准,但每条历史/请求都跑成本高,且 tiktoken 没专门跑中文。
    工程上这个估算误差 ±15%,对触发摘要这种粗粒度判断够用。
    """
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            cjk += 1
        else:
            other += 1
    return int(cjk * 0.6 + other * 0.25 + 0.5)


# ===== 会话 CRUD =====

def _ensure_owned(db: Session, session_id: int, user_id: int) -> QaSession:
    s = db.get(QaSession, session_id)
    if s is None or s.user_id != user_id:
        raise LookupError(f"session {session_id} not found")
    return s


def create_session(db: Session, *, user_id: int, title: str | None = None) -> QaSession:
    s = QaSession(user_id=user_id, title=(title or "新对话")[:120], summary="")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def get_session(db: Session, *, session_id: int, user_id: int) -> QaSession:
    return _ensure_owned(db, session_id, user_id)


def list_sessions(db: Session, *, user_id: int) -> list[dict]:
    """会话列表 + 每个会话的最新一条消息预览 + 消息总数。

    n 次消息 + 1 次总数 = 2 次查询,足够小项目用,后续可考虑物化。
    """
    sessions = (
        db.execute(
            select(QaSession).where(QaSession.user_id == user_id).order_by(desc(QaSession.updated_at))
        )
        .scalars()
        .all()
    )
    if not sessions:
        return []
    sids = [s.id for s in sessions]
    # 每会话消息总数
    counts = dict(
        db.execute(
            select(QaMessage.session_id, func.count(QaMessage.id))
            .where(QaMessage.session_id.in_(sids))
            .group_by(QaMessage.session_id)
        ).all()
    )
    # 每会话最新一条
    latest_rows = db.execute(
        select(QaMessage.session_id, QaMessage.content, QaMessage.created_at, QaMessage.role)
        .where(QaMessage.session_id.in_(sids))
        .order_by(QaMessage.session_id, desc(QaMessage.created_at))
    ).all()
    latest_map: dict[int, tuple[str, datetime, str]] = {}
    for sid, content, created_at, role in latest_rows:
        if sid not in latest_map:
            latest_map[sid] = (content or "", created_at, role)

    out: list[dict] = []
    for s in sessions:
        preview, _, _ = latest_map.get(s.id, ("", None, ""))
        out.append(
            {
                "id": s.id,
                "title": s.title or "新对话",
                "summary": s.summary or "",
                "last_message_preview": (preview or "").strip().replace("\n", " ")[:80],
                "message_count": counts.get(s.id, 0),
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
        )
    return out


def rename_session(db: Session, *, session_id: int, user_id: int, title: str) -> QaSession:
    s = _ensure_owned(db, session_id, user_id)
    s.title = title[:120] or "新对话"
    db.commit()
    db.refresh(s)
    return s


def delete_session(db: Session, *, session_id: int, user_id: int) -> None:
    s = _ensure_owned(db, session_id, user_id)
    db.execute(QaMessage.__table__.delete().where(QaMessage.session_id == s.id))
    db.delete(s)
    db.commit()


def set_summary(db: Session, *, session_id: int, user_id: int, summary: str) -> QaSession:
    s = _ensure_owned(db, session_id, user_id)
    s.summary = (summary or "").strip()
    db.commit()
    db.refresh(s)
    return s


# ===== 消息 =====

def _list_messages(db: Session, *, session_id: int) -> list[QaMessage]:
    return (
        db.execute(
            select(QaMessage)
            .where(QaMessage.session_id == session_id)
            .order_by(QaMessage.created_at, QaMessage.id)
        )
        .scalars()
        .all()
    )


def list_messages(db: Session, *, session_id: int, user_id: int) -> list[QaMessage]:
    _ensure_owned(db, session_id, user_id)
    return _list_messages(db, session_id=session_id)


def add_user_message(
    db: Session, *, session_id: int, user_id: int, content: str
) -> QaMessage:
    """追加用户消息;若是会话首条且 title='新对话',用问题前 24 字作 title。"""
    s = _ensure_owned(db, session_id, user_id)
    msg = QaMessage(
        session_id=session_id,
        user_id=user_id,
        role="user",
        content=content,
        meta_json="{}",
        # 兼容旧字段:question 仍写入,answer 留空,hot_topics 还能扫
        question=content,
    )
    db.add(msg)
    if (s.title or "") in ("", "新对话") and content.strip():
        s.title = content.strip()[:24]
    db.commit()
    db.refresh(msg)
    return msg


def add_assistant_message(
    db: Session,
    *,
    session_id: int,
    user_id: int,
    content: str,
    meta: dict | None = None,
    persisted_query_slug: str | None = None,
) -> QaMessage:
    """追加助手消息;quality/citations/path 塞 meta_json + 兼容旧 answer 字段。"""
    _ensure_owned(db, session_id, user_id)
    meta = meta or {}
    quality = meta.get("quality") or {}
    citations = meta.get("citations") or []
    path = meta.get("path") or {}
    msg = QaMessage(
        session_id=session_id,
        user_id=user_id,
        role="assistant",
        content=content,
        meta_json=json.dumps(meta, ensure_ascii=False),
        # 旧字段兼容
        answer=content,
        citations_json=json.dumps(citations, ensure_ascii=False),
        path_json=json.dumps(path, ensure_ascii=False),
        answer_quality=quality.get("score"),
        tier=quality.get("tier"),
        gap_flag=bool(quality.get("gap_flag", False)),
        persisted_query_slug=persisted_query_slug,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


# ===== history 装载(给 query SSE 用)=====

def load_history_payload(
    db: Session, *, session_id: int, user_id: int
) -> tuple[list[dict], str]:
    """读"给 LLM 看"的 (history, summary)。

    history 只取 summary_until 之后的消息(已压缩的部分由 summary 代替)。
    用户视图(/conversations/{id})走 list_messages,**返回完整历史**,与本函数无关。
    """
    s = _ensure_owned(db, session_id, user_id)
    msgs = _list_messages(db, session_id=session_id)
    cutoff = s.summary_until
    if cutoff is not None:
        msgs = [m for m in msgs if m.created_at and m.created_at > cutoff]
    history = [
        {"role": m.role, "content": m.content or ""}
        for m in msgs
        if m.role in ("user", "assistant") and (m.content or "").strip()
    ]
    return history, s.summary or ""


# ===== 摘要 =====

def should_summarize(active_messages: list[QaMessage], summary: str) -> bool:
    """达到阈值且活跃消息数富裕时才压缩。

    active_messages: 仅 summary_until 之后的消息(被压缩过的不重算)
    """
    if len(active_messages) <= SUMMARIZE_KEEP_RECENT + 2:
        return False
    total = estimate_tokens(summary or "")
    for m in active_messages:
        total += estimate_tokens(m.content or "")
    return total >= SUMMARIZE_TOKEN_THRESHOLD


async def summarize_with_llm(
    *,
    messages: list[dict],
    prev_summary: str | None,
) -> str:
    """复用旧 /summarize 的 prompt,把对话压成 ≤300 字摘要。"""
    convo_lines: list[str] = []
    for m in messages:
        role = "用户" if m.get("role") == "user" else "助手"
        convo_lines.append(f"{role}: {m.get('content', '')}")
    convo_text = "\n".join(convo_lines)
    prev = (prev_summary or "").strip()
    prev_block = f"已有摘要(请合并而非替换):\n{prev}\n\n" if prev else ""
    prompt = (
        "下面是一段工业工程知识库的多轮对话。请压缩为一段中文摘要(不超过 600 字),"
        "需保留:用户的核心问题与意图、关键事实与数据、已确认的结论、未决的问题与偏好、"
        "重要的方法论步骤与量化指标。丢弃寒暄、重复内容、纯过程性描述。"
        "宁可保留更多事实细节,不要为了压缩而丢失专业内容。仅输出摘要正文,不要任何前缀或说明。\n\n"
        f"{prev_block}对话:\n{convo_text}"
    )
    provider = get_llm_provider()
    text = await provider.chat(
        [
            ChatMessage(role="system", content="你是一个善于压缩对话的助手。"),
            ChatMessage(role="user", content=prompt),
        ],
        stream=False,
    )
    return (text or "").strip()


def split_for_summarize(messages: list[QaMessage]) -> tuple[list[QaMessage], list[QaMessage]]:
    """切分:前 N-KEEP_RECENT 条压缩,后 KEEP_RECENT 条保留原文。"""
    if len(messages) <= SUMMARIZE_KEEP_RECENT:
        return [], messages
    cutoff = len(messages) - SUMMARIZE_KEEP_RECENT
    return messages[:cutoff], messages[cutoff:]


async def maybe_auto_summarize(
    db: Session, *, session_id: int, user_id: int
) -> bool:
    """在新一条 assistant 消息落地后调用。

    达到阈值时:压缩 summary_until 之后的较早部分 → 写 summary 字段 + 推进 summary_until。
    **不删除消息**——用户视图仍能看到完整对话;LLM 视图通过 summary_until 切片省 token。
    """
    s = _ensure_owned(db, session_id, user_id)
    all_msgs = _list_messages(db, session_id=session_id)
    cutoff_dt = s.summary_until
    active = (
        [m for m in all_msgs if m.created_at and m.created_at > cutoff_dt]
        if cutoff_dt is not None
        else list(all_msgs)
    )
    if not should_summarize(active, s.summary):
        return False
    to_compress, to_keep = split_for_summarize(active)
    if not to_compress:
        return False
    payload = [
        {"role": m.role, "content": m.content or ""}
        for m in to_compress
        if m.role in ("user", "assistant")
    ]
    try:
        new_summary = await summarize_with_llm(messages=payload, prev_summary=s.summary)
    except Exception:  # noqa: BLE001
        return False
    if not new_summary:
        return False
    # 推进 summary_until 到"被压缩部分的最后一条"的 created_at
    last_compressed = to_compress[-1]
    s.summary = new_summary
    s.summary_until = last_compressed.created_at
    db.commit()
    return True


async def force_resummarize(
    db: Session, *, session_id: int, user_id: int
) -> tuple[str, int]:
    """重新生成摘要(用户手动点"重新生成摘要"按钮)。

    把所有消息(含已有 summary)重新压一次 → 写 summary,把 summary_until 推到最后一条消息时间。
    消息**不删**。
    返回 (new_summary, message_count)。
    """
    s = _ensure_owned(db, session_id, user_id)
    msgs = _list_messages(db, session_id=session_id)
    payload = [
        {"role": m.role, "content": m.content or ""}
        for m in msgs
        if m.role in ("user", "assistant") and (m.content or "").strip()
    ]
    if not payload and not s.summary:
        return "", 0
    new_summary = await summarize_with_llm(messages=payload, prev_summary=s.summary)
    if new_summary:
        s.summary = new_summary
        # 推进 summary_until 到最后一条消息时间(没消息就保持原值)
        if msgs:
            s.summary_until = msgs[-1].created_at
        db.commit()
    return s.summary or "", len(msgs)
