"""growth/followups.py:从各页 frontmatter.followups 聚合待跟进。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import session_scope
from app.models.followup_queue import FollowupQueue
from app.services.wiki.frontmatter_io import parse_text


def _scan_followups(wiki_root: Path) -> list[dict]:
    """扫 wiki/queries/ 与 wiki/synthesis/ 与 wiki/comparisons/ frontmatter.followups。"""
    out: list[dict] = []
    today = date.today()
    for sub in ("queries", "synthesis", "comparisons"):
        d = Path(wiki_root) / "wiki" / sub
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            try:
                fm, body = parse_text(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            followups = fm.get("followups") or []
            if not isinstance(followups, list):
                continue
            slug = p.stem
            created_raw = fm.get("created") or fm.get("updated")
            try:
                if created_raw:
                    s = str(created_raw)
                    # ISO 解析
                    if s.endswith("Z"):
                        s = s.replace("Z", "+00:00")
                    created = datetime.fromisoformat(s)
                    age = max(0, (today - created.date()).days)
                else:
                    age = 0
            except Exception:
                age = 0
            for item in followups:
                text = str(item).strip()
                if text:
                    out.append({
                        "source_page": slug,
                        "followup_text": text[:512],
                        "age_days": age,
                    })
            # 兜底:解析 ## 尚待解决的问题 段
            if "## 尚待解决" in body:
                from app.services.digest.parsing import find_section, split_sections

                sections = split_sections(body)
                sec = find_section(sections, "尚待解决")
                if sec:
                    for raw in (sec["content"] or "").splitlines():
                        line = raw.strip()
                        if line.startswith(("- ", "* ")):
                            text = line[2:].strip()
                            if text and text not in {x["followup_text"] for x in out if x["source_page"] == slug}:
                                out.append({
                                    "source_page": slug,
                                    "followup_text": text[:512],
                                    "age_days": age,
                                })
    return out


def refresh_followups_sync(*, db: Session, wiki_root: Path) -> int:
    items = _scan_followups(wiki_root)
    db.execute(delete(FollowupQueue))
    for it in items:
        db.add(FollowupQueue(
            source_page=it["source_page"],
            followup_text=it["followup_text"],
            age_days=it["age_days"],
            status="open",
        ))
    db.flush()
    return len(items)


async def refresh_followups(*, wiki_root: Path | None = None) -> int:
    from app.core.config import settings as _settings

    root = Path(wiki_root) if wiki_root else Path(_settings.wiki_root_path)
    with session_scope() as s:
        return refresh_followups_sync(db=s, wiki_root=root)


def list_followups(db: Session, *, limit: int = 100) -> list[dict]:
    stmt = select(FollowupQueue).order_by(FollowupQueue.age_days.desc()).limit(limit)
    rows = list(db.execute(stmt).scalars())
    return [
        {
            "source_page": r.source_page,
            "followup_text": r.followup_text,
            "age_days": int(r.age_days),
            "status": r.status,
        }
        for r in rows
    ]
