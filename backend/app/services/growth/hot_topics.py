"""growth/hot_topics.py:周期性聚合热门主题。

策略:
  - 扫近 window_days 天的 QaMessage,按问题中的关键词(简单 token 化)聚合
  - 同时算每个 theme 的 gap_share = (gap_flag=true 占比)
  - query_count >= hot_topic_threshold 且 gap_share > 0.5 → auto_promote_candidate=True
  - 写 hot_topics 表(每个 theme 一行,window_date=今天)
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import session_scope
from app.models.hot_topic import HotTopic
from app.models.qa import QaMessage

_TOKEN_PATTERN = re.compile(r"[一-鿿]{2,}|[A-Za-z][A-Za-z0-9\-]+")
_STOPWORDS = {
    "什么", "怎么", "如何", "为什么", "请问", "可以", "应该", "有哪些", "哪些",
    "为何", "何时", "目前", "请", "现在", "这个", "那个", "我", "你",
}


def _tokenize_question(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for m in _TOKEN_PATTERN.finditer(text):
        tok = m.group(0)
        if tok in _STOPWORDS:
            continue
        out.append(tok)
    return out


def _read_threshold(wiki_root: Path | None = None) -> int:
    """读 .wiki-schema.md 成长配置中的 hot_topic_threshold,默认 3。"""
    root = Path(wiki_root) if wiki_root else Path(settings.wiki_root_path)
    schema = root / ".wiki-schema.md"
    if not schema.exists():
        return 3
    try:
        text = schema.read_text(encoding="utf-8")
    except Exception:
        return 3
    m = re.search(r"hot_topic_threshold\s*:\s*(\d+)", text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return 3


def _collect_themes(
    db: Session, *, since: datetime
) -> tuple[Counter[str], dict[str, int]]:
    """扫 since 之后的 QaMessage,聚合每个 theme 的 query_count + gap_count。"""
    rows = db.execute(
        select(QaMessage.question, QaMessage.gap_flag).where(
            QaMessage.created_at >= since
        )
    ).all()
    counts: Counter[str] = Counter()
    gap_counts: dict[str, int] = defaultdict(int)
    for question, gap_flag in rows:
        themes = _tokenize_question(question or "")
        seen_in_q: set[str] = set()
        for t in themes:
            if t in seen_in_q:
                continue
            seen_in_q.add(t)
            counts[t] += 1
            if gap_flag:
                gap_counts[t] += 1
    return counts, gap_counts


def refresh_hot_topics_sync(
    *,
    db: Session,
    window_days: int = 30,
    threshold: int | None = None,
    wiki_root: Path | None = None,
) -> int:
    """同步版:写 hot_topics 表;返回更新条数。"""
    threshold = threshold if threshold is not None else _read_threshold(wiki_root)
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    counts, gap_counts = _collect_themes(db, since=since)

    today = date.today()
    db.execute(delete(HotTopic).where(HotTopic.window_date == today))

    written = 0
    for theme, query_count in counts.most_common(50):
        if query_count < threshold:
            continue
        gap = gap_counts.get(theme, 0)
        gap_share = round(gap / query_count, 4)
        auto_promote = query_count >= threshold and gap_share > 0.5
        db.add(
            HotTopic(
                theme=theme,
                window_date=today,
                query_count_30d=query_count,
                gap_share=gap_share,
                auto_promote_candidate=auto_promote,
            )
        )
        written += 1
    db.flush()
    return written


async def refresh_hot_topics(*, window_days: int = 30) -> int:
    with session_scope() as s:
        return refresh_hot_topics_sync(db=s, window_days=window_days)


def list_hot_topics(db: Session, *, limit: int = 30, window_date: date | None = None) -> list[dict]:
    stmt = select(HotTopic)
    if window_date is not None:
        stmt = stmt.where(HotTopic.window_date == window_date)
    stmt = stmt.order_by(HotTopic.query_count_30d.desc()).limit(limit)
    rows = list(db.execute(stmt).scalars())
    return [
        {
            "theme": r.theme,
            "query_count_30d": r.query_count_30d,
            "gap_share": float(r.gap_share),
            "auto_promote_candidate": bool(r.auto_promote_candidate),
            "window_date": r.window_date,
        }
        for r in rows
    ]
