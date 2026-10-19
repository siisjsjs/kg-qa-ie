"""digest:深度报告 / 对比表 / 时间线 三模板。"""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.core.deps import CurrentUserId, DbSession
from app.schemas.digest import DigestRequest, DigestResponse, DigestSection
from app.services.digest.comparison import compose_comparison
from app.services.digest.deep_dive import compose_deep_dive
from app.services.digest.router import detect_mode
from app.services.digest.timeline import compose_timeline
from app.services.growth.gap_queue import append_gap
from app.services.wiki.frontmatter_io import parse_text, serialize
from app.services.wiki.fs_io import atomic_write, safe_join, slugify

router = APIRouter()


def _make_slug(topic: str, mode: str) -> str:
    today = date.today().isoformat()
    base = slugify(f"{mode}-{topic}", max_len=60)
    return f"{today}-{base}"


def _digest_dir(mode: str) -> str:
    return "comparisons" if mode == "comparison" else "synthesis"


def _quality_score(mode: str, score: dict) -> float:
    """从 score dict 萃出一个 0-1 标量 quality_score(给前端展示)。"""
    if mode == "deep_dive":
        return float(score.get("fill_rate") or 0.0)
    if mode == "comparison":
        return float(score.get("fill_rate") or 0.0)
    if mode == "timeline":
        n = int(score.get("events_count") or 0)
        return min(1.0, n / 5.0)
    return 0.0


def _build_response_sections(parts: list[dict]) -> list[DigestSection]:
    return [DigestSection(heading=p.get("heading", ""), content=p.get("content", ""))
            for p in parts if (p.get("heading") or "").strip()]


@router.post("/digest", response_model=DigestResponse)
async def create_digest(
    payload: DigestRequest,
    user_id: CurrentUserId,
    db: DbSession,
) -> DigestResponse:
    """生成 digest:自动判断 deep_dive / comparison / timeline。"""
    wiki_root = settings.wiki_root_path
    topic = payload.topic.strip()
    if not topic:
        raise HTTPException(status_code=422, detail="topic is required")

    mode = payload.mode if payload.mode != "auto" else detect_mode(topic)

    if mode == "deep_dive":
        result = await compose_deep_dive(topic=topic, wiki_root=wiki_root)
    elif mode == "comparison":
        result = await compose_comparison(
            topic=topic, objects=payload.objects, wiki_root=wiki_root
        )
    elif mode == "timeline":
        result = await compose_timeline(topic=topic, wiki_root=wiki_root)
    else:
        raise HTTPException(status_code=422, detail=f"unknown digest mode: {mode}")

    score_block = result.get("score") or {}
    gap_flag = bool(score_block.get("gap_flag"))
    citations = result.get("citations") or []
    md = result.get("raw_markdown") or ""
    sections_dicts = result.get("sections") or []

    slug = _make_slug(topic, mode)
    target_dir = _digest_dir(mode)
    target_path = safe_join(wiki_root, "wiki", target_dir, f"{slug}.md")

    now = datetime.now(timezone.utc).isoformat()
    fm = {
        "title": topic[:80],
        "slug": slug,
        "kind": "synthesis" if mode != "comparison" else "comparison",
        "digest_mode": mode,
        "topic": topic,
        "objects": result.get("objects") or payload.objects or [],
        "tags": [],
        "domains": [],
        "sources": ["digest"],
        "citations": citations,
        "answer_quality": _quality_score(mode, score_block),
        "fill_rate": score_block.get("fill_rate"),
        "events_count": score_block.get("events_count"),
        "sections_missing": score_block.get("sections_missing") or [],
        "gap_flag": gap_flag,
        "followups": result.get("followups") or [],
        "asked_by": user_id,
        "created": now,
        "updated": now,
        "version": 1,
    }
    body = md if md.strip() else "_LLM 未返回内容_"
    full_text = serialize(fm, body)
    await atomic_write(target_path, full_text)

    # 同步索引
    try:
        from app.services.ingestion.pipeline import _sync_db_indices
        rel = f"wiki/{target_dir}/{slug}.md"
        _sync_db_indices(db, wiki_root=wiki_root, written_paths=[rel])
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()

    # 入 gap_queue
    if mode == "deep_dive" and gap_flag:
        missing = score_block.get("sections_missing") or []
        placeholder = score_block.get("placeholder_sections") or []
        await append_gap(
            db=db,
            origin="digest",
            digest_page=slug,
            gap_reasons=[f"deep_dive 缺章节:{', '.join(missing + placeholder)}"],
            suggested_seeds=missing + placeholder,
        )
        db.commit()
    elif mode == "comparison":
        empty = score_block.get("empty_cells") or []
        for dim, obj in empty:
            await append_gap(
                db=db,
                origin="comparison_cell",
                digest_page=slug,
                gap_reasons=[f"对比表空格:{dim} × {obj}"],
                suggested_seeds=[dim, obj],
            )
        if empty:
            db.commit()
    elif mode == "timeline" and gap_flag:
        await append_gap(
            db=db,
            origin="digest",
            digest_page=slug,
            gap_reasons=[f"timeline 事件不足({score_block.get('events_count')} 个)"],
            suggested_seeds=[topic],
        )
        db.commit()

    response_sections = _build_response_sections(sections_dicts)

    return DigestResponse(
        slug=slug,
        mode=mode,
        sections=response_sections,
        citations=citations,
        quality_score=_quality_score(mode, score_block),
        gap_flag=gap_flag,
        sections_missing=score_block.get("sections_missing") or [],
        fill_rate=(score_block.get("fill_rate") if mode in ("comparison", "deep_dive") else None),
    )


@router.get("/synthesis/{slug}", response_model=DigestResponse)
async def get_synthesis(slug: str, user_id: CurrentUserId, db: DbSession) -> DigestResponse:  # noqa: ARG001
    """读已生成的 digest 报告(支持 synthesis 与 comparisons 两个目录)。"""
    wiki_root = settings.wiki_root_path
    p = None
    for sub in ("synthesis", "comparisons"):
        candidate = safe_join(wiki_root, "wiki", sub, f"{slug}.md")
        if candidate.exists():
            p = candidate
            break
    if p is None:
        raise HTTPException(status_code=404, detail=f"digest {slug!r} not found")

    fm, body = parse_text(p.read_text(encoding="utf-8"))
    mode = str(fm.get("digest_mode") or "deep_dive")

    # 把 body 按 H2 切片
    from app.services.digest.parsing import split_sections

    raw_sections = split_sections(body)
    response_sections = _build_response_sections(raw_sections)

    return DigestResponse(
        slug=slug,
        mode=mode if mode in ("deep_dive", "comparison", "timeline") else "deep_dive",
        sections=response_sections,
        citations=list(fm.get("citations") or []),
        quality_score=float(fm.get("answer_quality") or 0.0),
        gap_flag=bool(fm.get("gap_flag", False)),
        sections_missing=list(fm.get("sections_missing") or []),
        fill_rate=fm.get("fill_rate"),
    )
