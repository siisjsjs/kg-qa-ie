"""crystallize:用户文本 → synthesis 页。

流程(规划文档 F7 + 0.3 决策):
  1. 渲染 prompt(用户文本 + 目标主题 + 现有相关 wiki 摘要)
  2. 单次 LLM 调用产出 markdown(含 frontmatter + 章节)
  3. 解析提取 title + body + 抽取 [[X]] 出现的实体作为合并候选
  4. 写盘 wiki/synthesis/{slug}.md(或 wiki/comparisons/ 当 kind='comparison')
  5. 对每个 [[X]] 实体如果当前不存在 wiki 页 → 写一条 gap-queue
     (origin=merge_suggestion,suggested_seeds 含该实体名)
  6. 同步 wiki_pages / wiki_links 索引 + 写一条 wiki_node_versions(triggered_by=crystallize)

返回 (slug, title, candidate_slugs)
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy.orm import Session

from app.models.gap_queue import GapQueue
from app.services.llm import ChatMessage, get_llm_provider
from app.services.llm.base import EXTRACTION_MAX_TOKENS
from app.services.wiki.frontmatter_io import parse_text, serialize
from app.services.wiki.fs_io import atomic_write, safe_join, slugify
from app.services.wiki.link_resolver import extract_links

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
CRYSTALLIZE_TEMPLATE = (PROMPTS_DIR / "crystallize.md").read_text(encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _related_pages_summary(wiki_root: Path, target_topic: str | None) -> str:
    """简要列出现有实体页 + 主题页(若 target_topic 命中,放最前)。"""
    parts: list[str] = []
    for kind_dir in ("entities", "topics"):
        d = Path(wiki_root) / "wiki" / kind_dir
        if not d.exists():
            continue
        slugs = sorted(p.stem for p in d.glob("*.md") if p.is_file())[:80]
        if not slugs:
            continue
        # target_topic 命中时排最前
        if target_topic:
            hits = [s for s in slugs if target_topic in s]
            if hits:
                slugs = hits + [s for s in slugs if s not in hits]
        parts.append(f"### {kind_dir}\n" + "\n".join(f"- [[{s}]]" for s in slugs[:50]))
    return "\n\n".join(parts) if parts else "(空)"


def _render_prompt(*, text: str, target_topic: str | None, related: str) -> str:
    return (
        CRYSTALLIZE_TEMPLATE
        .replace("{text}", text)
        .replace("{target_topic}", target_topic or "(未指定)")
        .replace("{related_pages_summary}", related)
        .replace("{now}", _now_iso())
    )


_FENCE_PATTERN = re.compile(r"```markdown\s*(.+?)\s*```", re.DOTALL)


def _strip_markdown_fence(raw: str) -> str:
    """LLM 习惯用 ```markdown ... ``` 包裹整篇内容,剥掉外层围栏。"""
    m = _FENCE_PATTERN.search(raw)
    if m:
        return m.group(1).strip()
    return raw.strip()


def _ensure_frontmatter(
    body_text: str, *, fallback_title: str, kind: str
) -> tuple[dict, str, str]:
    """如果 LLM 没生成 frontmatter,补上。返回 (fm, body, title)。"""
    fm, body = parse_text(body_text)
    if not fm:
        fm = {
            "title": fallback_title,
            "kind": kind,
            "tags": [],
            "domains": [],
            "sources": ["user_input"],
            "version": 1,
        }
    fm.setdefault("kind", kind)
    fm.setdefault("sources", ["user_input"])
    fm.setdefault("version", 1)
    title = str(fm.get("title") or fallback_title)
    return fm, body, title


def _make_synthesis_slug(title: str) -> str:
    today = date.today().isoformat()
    title_slug = slugify(title)
    return f"{today}-{title_slug}"


def _candidate_entity_slugs(body: str) -> list[str]:
    """抽 body 中所有 [[X]] 出现过的目标 slug,去重保序。"""
    seen: set[str] = set()
    out: list[str] = []
    for target, _alias in extract_links(body):
        target = target.strip()
        if target and target not in seen:
            seen.add(target)
            out.append(target)
    return out


async def crystallize_text(
    *,
    text: str,
    target_topic: str | None,
    kind: str,
    wiki_root: Path,
    db: Session,
    user_id: int | None = None,
) -> tuple[str, str, list[str]]:
    """把用户文本结晶为 synthesis 页(或 comparison 页)。

    Args:
        text: 用户提供的笔记/对话/灵感(>=10 字)
        target_topic: 可选,提示 LLM 围绕该主题组织
        kind: synthesis 或 comparison
        wiki_root: 知识库根目录
        db: SA session(本函数不 commit;调用方负责)
        user_id: 操作者(用于 audit 与版本记录)

    Returns:
        (slug, title, candidate_slugs)
        - slug:落盘的 wiki/{synthesis|comparisons}/{slug}.md 的 slug
        - title:页面标题
        - candidate_slugs:body 中引用但 wiki/ 下不存在的实体 slug 列表,
          已逐条写入 gap_queue(origin=merge_suggestion)
    """
    if kind not in ("synthesis", "comparison"):
        raise ValueError(f"crystallize kind must be synthesis|comparison, got {kind!r}")
    if len(text.strip()) < 10:
        raise ValueError("crystallize text too short (min 10 chars)")

    related = _related_pages_summary(Path(wiki_root), target_topic)
    prompt = _render_prompt(text=text, target_topic=target_topic, related=related)

    provider = get_llm_provider()
    messages = [
        ChatMessage(
            role="system",
            content="你是一个把零散笔记凝练为结构化 wiki 页面的助手,使用中文,严格按用户提供的格式输出。",
        ),
        ChatMessage(role="user", content=prompt),
    ]
    # 结晶依赖模型推理,显式恢复(reasoning_effort=None 移除默认的 none)
    raw_resp = await provider.chat(messages, stream=False, reasoning_effort=None, max_tokens=EXTRACTION_MAX_TOKENS)
    raw = raw_resp if isinstance(raw_resp, str) else ""
    body_text = _strip_markdown_fence(raw)

    fallback_title = (target_topic or text.strip().splitlines()[0])[:60].strip() or "用户笔记"
    fm, body, title = _ensure_frontmatter(body_text, fallback_title=fallback_title, kind=kind)
    slug = _make_synthesis_slug(title)
    fm = dict(fm)
    fm["slug"] = slug
    fm["kind"] = kind

    # 写盘
    kind_dir = "synthesis" if kind == "synthesis" else "comparisons"
    target_path = safe_join(Path(wiki_root), "wiki", kind_dir, f"{slug}.md")
    text_full = serialize(fm, body)
    await atomic_write(target_path, text_full)

    # 同步 wiki_pages / wiki_links + 写版本
    rel = f"wiki/{kind_dir}/{slug}.md"
    try:
        from app.services.ingestion.pipeline import _sync_db_indices
        from app.services.wiki.version_history import compute_diff, write_version

        _sync_db_indices(db, wiki_root=Path(wiki_root), written_paths=[rel])
        write_version(
            db,
            slug=slug,
            version=int(fm.get("version") or 1),
            diff=compute_diff(old_fm={}, old_body="", new_fm=fm, new_body=body),
            triggered_by="crystallize",
            title_snapshot=title,
            actor_user_id=user_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crystallize: db sync failed (non-blocking): {e}")

    # 候选合并:body 中 [[X]] 但 wiki/ 下不存在 → 写 gap-queue (origin=merge_suggestion)
    candidates = _candidate_entity_slugs(body)
    missing: list[str] = []
    for cand in candidates:
        # 检查 wiki/ 各 kind 目录有无该 slug
        found = False
        for sub in ("entities", "topics", "synthesis", "comparisons", "sources", "queries"):
            if safe_join(Path(wiki_root), "wiki", sub, f"{cand}.md").exists():
                found = True
                break
        if not found:
            missing.append(cand)

    for cand in missing:
        try:
            row = GapQueue(
                record_date=date.today(),
                origin="merge_suggestion",
                user_question=None,
                query_page=None,
                digest_page=slug,
                gap_reasons=f"crystallize 引用了未建页的节点 [[{cand}]]",
                suggested_seeds_json=json.dumps([cand], ensure_ascii=False),
                status="open",
            )
            db.add(row)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"crystallize: gap_queue insert failed for {cand}: {e}")

    return slug, title, missing
