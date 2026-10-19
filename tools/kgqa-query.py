"""query:SSE 流式查询(Agentic RAG 主链路,改造计划 §6.4)。

链路:
  正则快路径命中闲聊 → chitchat 流式秒回
  主路径:并行 [指代改写] [chunk 级混合检索投机预取]
    → Agent 循环(原生 function calling,最多 2 轮工具,§6.1)
    → 后处理:引用校验清洗 → citations(chunk 级 + web)→ score → persist
    → done → followups → image_ready|image_failed*(若有 pending 图)

事件序列:
  progress(stage=rewrite)? → progress(stage=retrieval, candidates=[chunk摘要]) →
  [tool_start → tool_result]* → token* → replace_answer? →
  citations(chunk级+web) → path → score → persist →
  done → followup_suggestions? → image_ready|image_failed*
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings
from app.core.db import session_scope
from app.core.deps import CurrentUserId
from app.schemas.query import (
    AnswerQuality,
    Citation,
    QueryFinalResponse,
    QueryRequest,
)
from app.services.conversations import (
    add_assistant_message,
    add_user_message,
    load_history_payload,
    maybe_auto_summarize,
)
from app.services.growth.gap_queue import append_gap
from app.services.ingestion.merger import load_alias_groups
from app.services.query.agent import run_agent_stream
from app.services.query.alias_expand import expand as expand_aliases
from app.services.query.chitchat import compose_stream as compose_chitchat_stream
from app.services.query.citations import build_citation_details, build_citation_details_v2
from app.services.query.evidence_pool import EvidencePool
from app.services.query.followups import generate_followups
from app.services.query.hybrid import hybrid_search
from app.services.query.inflight import InflightQueryManager, make_inflight_key
from app.services.query.persistor import maybe_persist
from app.services.query.rewriter import rewrite as rewrite_question
from app.services.query.router import ROUTE_CHITCHAT, _fast_kb_kind, _fast_path
from app.services.query.scoring import evaluate_for_query
from app.services.query.validation import (
    extract_citation_refs,
    extract_cited_slugs,
    validate_and_clean_answer_v2,
)
from app.services.query.vector_index import marker_path as chunk_index_marker_path
from app.services.tools import build_tools
from app.services.tools.chart_templates import (
    build_catalog_hint,
    build_chart_hint,
    build_session_chart_hint,
    build_template_cards,
    match_templates_for_question,
    zhihuitu_base_url,
)
from app.services.tools.imagegen import IMAGE_ID_RE
from app.services.wiki.frontmatter_io import parse_text
from app.services.wiki.fs_io import safe_join

router = APIRouter()
_retrieval_inflight: InflightQueryManager[dict] = InflightQueryManager()

# Agent 循环整体兜底超时(§5.4)
_AGENT_TIMEOUT_SECONDS = 120
# done 之后等待 pending 图片的最长时间(§5.3);须覆盖 imagegen 300s 读超时 + 一次重试的大部分场景
_IMAGE_WAIT_SECONDS = 320


def _sse(event: str, payload) -> dict:
    return {"event": event, "data": json.dumps(payload, ensure_ascii=False)}


_DSML_TAG_RE = re.compile(
    r"<invoke\b[^>]*>.*?</invoke>|<parameter\b[^>]*>.*?</parameter>"
    r"|<parameter\b[^>]*/>|</invoke>|</parameter>",
    re.DOTALL | re.IGNORECASE,
)
_IMAGE_PLACEHOLDER_RE = re.compile(r"\{\{\s*image\s*:\s*([A-Za-z0-9_-]+)\s*\}\}")
_CLAIMS_IMAGE_RE = re.compile(
    r"(上图|如下图|见图|附图|示意图如下|图表如下|已生成.*图|下面这张图)"
)


def _claims_image_present(text: str) -> bool:
    return bool(_CLAIMS_IMAGE_RE.search(text or ""))


def _strip_fake_tool_calls(text: str) -> str:
    """清掉模型在无工具的纯文本回合输出的 DSML 假工具调用。

    DeepSeek 系列在纯文本流里可能输出 <invoke>/<parameter> 标签(DSML 格式),
    这不是真正的工具调用(不会被解析执行),只会污染正文——典型症状是
    generate_chart 的 params 残缺(values 空)且没有图。整体删除 invoke 块、
    清掉孤立标签,并收敛多出来的空行。
    """
    if "<invoke" not in text and "<parameter" not in text:
        return text
    cleaned = _DSML_TAG_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _strip_fake_image_placeholders(text: str, known_ids: set[str] | None = None) -> str:
    """清掉模型照抄提示词的假占位,例如 {{image:image_id}}。

    只保留工具真正返回、且格式为 img_ + 12 位十六进制的 id。
    """
    known = known_ids or set()

    def _keep(match: re.Match[str]) -> str:
        image_id = match.group(1)
        if IMAGE_ID_RE.match(image_id) and image_id in known:
            return match.group(0)
        return ""

    cleaned = _IMAGE_PLACEHOLDER_RE.sub(_keep, text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _persist_user_msg(session_id: int, user_id: int, content: str) -> None:
    """同步落 user 消息(短开短关 session)。"""
    with session_scope() as db:
        try:
            add_user_message(db, session_id=session_id, user_id=user_id, content=content)
        except LookupError:
            pass


def _persist_assistant_msg(
    session_id: int, user_id: int, content: str, meta: dict, persisted_query_slug: str | None
) -> int | None:
    """落库助手消息,返回消息 id(供图片后台任务完成后回写 meta.images)。"""
    with session_scope() as db:
        try:
            msg = add_assistant_message(
                db,
                session_id=session_id,
                user_id=user_id,
                content=content,
                meta=meta,
                persisted_query_slug=persisted_query_slug,
            )
            db.flush()
            return msg.id
        except LookupError:
            return None


def _image_meta_entry(img: dict) -> dict:
    """ctx["images"] 条目 → 落库 meta.images 条目(caption/chart 供前端历史还原)。"""
    entry = {
        "image_id": img["image_id"],
        "status": img["status"],
        "prompt": img.get("prompt", ""),
    }
    if img.get("caption"):
        entry["caption"] = img["caption"]
    if img.get("chart"):
        entry["chart"] = img["chart"]
    return entry


def _update_message_images(message_id: int, images_meta: list[dict]) -> None:
    """图片后台任务完成后,把最终 status 回写到已落库消息的 meta.images。

    没有这一步的话:SSE 关闭后才完成的图片会让历史会话永远停在 pending。
    """
    from app.models.qa import QaMessage

    with session_scope() as db:
        msg = db.get(QaMessage, message_id)
        if msg is None:
            return
        try:
            meta = json.loads(msg.meta_json or "{}")
        except Exception:  # noqa: BLE001
            meta = {}
        meta["images"] = images_meta
        msg.meta_json = json.dumps(meta, ensure_ascii=False)


def _attach_image_meta_writeback(message_id: int, ctx: dict) -> None:
    """给未完成的图片任务挂回调:任务一结束就把 ctx["images"] 的最终态回写 DB。

    与 SSE 生命周期解耦——即使客户端提前断开、等待窗口超时,历史记录也能拿到
    ready/failed 终态而不是永远 pending。
    """

    def _writeback(_task: asyncio.Task | None = None) -> None:
        images_meta = [_image_meta_entry(img) for img in ctx["images"].values()]
        try:
            asyncio.get_running_loop().create_task(
                asyncio.to_thread(_update_message_images, message_id, images_meta)
            )
        except RuntimeError:
            _update_message_images(message_id, images_meta)

    for task in ctx.get("image_tasks", {}).values():
        if task.done():
            _writeback()
        else:
            task.add_done_callback(_writeback)


async def _trigger_auto_summarize(session_id: int, user_id: int) -> None:
    """流式结束后异步触发,失败不影响用户。"""
    try:
        with session_scope() as db:
            await maybe_auto_summarize(db, session_id=session_id, user_id=user_id)
    except Exception:  # noqa: BLE001
        pass


def _load_session_charts(
    session_id: int | None, user_id: int, *, limit: int = 3
) -> list[dict]:
    """取会话中最近生成的绘图平台图表(每个模板取最新一次,时间正序)。

    history 只有纯文本,模型看不到图表参数——这里从消息 meta.images.chart 里把
    template_id + params 捞出来,注入「图表修改提示」,支撑追问改图(改参重画)。
    """
    if session_id is None:
        return []
    from sqlalchemy import select

    from app.models.qa import QaMessage, QaSession

    charts: list[dict] = []
    seen_templates: set[str] = set()
    try:
        with session_scope() as db:
            s = db.get(QaSession, session_id)
            if s is None or s.user_id != user_id:
                return []
            rows = (
                db.execute(
                    select(QaMessage)
                    .where(
                        QaMessage.session_id == session_id,
                        QaMessage.role == "assistant",
                    )
                    .order_by(QaMessage.id.desc())
                    .limit(20)
                )
                .scalars()
                .all()
            )
            for msg in rows:  # 新 → 旧
                try:
                    meta = json.loads(msg.meta_json or "{}")
                except Exception:  # noqa: BLE001
                    continue
                for img in reversed(meta.get("images") or []):
                    chart = img.get("chart") or {}
                    template_id = str(chart.get("template_id") or "")
                    params = chart.get("params")
                    if not template_id or template_id in seen_templates:
                        continue
                    if not isinstance(params, dict) or not params:
                        continue
                    seen_templates.add(template_id)
                    charts.append(
                        {
                            "image_id": str(img.get("image_id") or ""),
                            "template_id": template_id,
                            "name": str(chart.get("name") or ""),
                            "params": params,
                        }
                    )
                    if len(charts) >= limit:
                        break
                if len(charts) >= limit:
                    break
    except Exception as exc:  # noqa: BLE001
        logger.warning("load session charts failed: {!r}", exc)
        return []
    charts.reverse()
    return charts


def _load_history_for_session(session_id: int | None, user_id: int) -> tuple[list[dict], str]:
    """从 DB 读 (history, summary)。session_id=None / 不属于 user → 空历史。"""
    if not session_id:
        return [], ""
    try:
        with session_scope() as db:
            return load_history_payload(db, session_id=session_id, user_id=user_id)
    except LookupError:
        return [], ""


async def _stream_chitchat(
    *,
    question: str,
    history: list[dict] | None,
    summary: str | None,
    user_id: int,
    session_id: int | None,
) -> AsyncIterator[dict]:
    """闲聊快路径:不检索、不评分、不持久化,但发齐前端期望的事件。"""
    yield _sse("progress", {"stage": "route", "route": "chitchat"})
    yield _sse("progress", {"stage": "compose_chitchat"})
    answer_buf: list[str] = []
    async for token in compose_chitchat_stream(
        question=question, history=history, summary=summary
    ):
        answer_buf.append(token)
        yield _sse("token", {"text": token})
    answer = "".join(answer_buf)
    yield _sse("citations", {"citations": [], "domain_ie": False, "kind": "chitchat"})
    yield _sse(
        "score",
        {
            "score": 1.0,
            "tier": "unknown",
            "cited_sources": 0,
            "confidence_mix": {"EXTRACTED": 0, "INFERRED": 0, "AMBIGUOUS": 0, "UNKNOWN": 0},
            "alias_miss_count": 0,
            "broken_links": 0,
            "isolated_hits": 0,
            "gap_flag": False,
            "reasons": ["chitchat"],
        },
    )
    yield _sse("persist", {"persisted_query_slug": None, "reason": "chitchat"})
    yield _sse("done", {"tokens": len(answer), "cited_count": 0})
    if session_id is not None:
        _persist_assistant_msg(
            session_id, user_id, answer,
            meta={"route": "chitchat"},
            persisted_query_slug=None,
        )
        await _trigger_auto_summarize(session_id, user_id)


async def _stream_answer(
    *,
    question: str,
    domain_ie_override: bool | None,
    persist_override: bool | None,
    user_id: int,
    history: list[dict] | None = None,
    summary: str | None = None,
    session_id: int | None = None,
    kgqa_token: str = "",
) -> AsyncIterator[dict]:
    """组装 SSE 事件流(主路径 = Agent 循环)。"""
    wiki_root = settings.wiki_root_path

    # 0) 闲聊正则快路径(LLM 路由分类已删除,省 1-2s)
    if _fast_path(question) == ROUTE_CHITCHAT:
        async for event in _stream_chitchat(
            question=question, history=history, summary=summary,
            user_id=user_id, session_id=session_id,
        ):
            yield event
        return

    # 1) 并行:指代改写(有代词才调 LLM)+ 别名扩展 + chunk 检索投机预取
    rewrite_task = asyncio.create_task(
        rewrite_question(question=question, history=history, summary=summary)
    )
    alias_groups = load_alias_groups(wiki_root)
    domain_ie = bool(domain_ie_override) if domain_ie_override is not None else _detect_domain_ie(wiki_root)

    rewritten, did_rewrite = await rewrite_task
    if did_rewrite:
        yield _sse(
            "progress",
            {"stage": "rewrite", "original": question, "rewritten": rewritten},
        )
    grep_question = rewritten if did_rewrite else question
    keywords, hit_canonicals = expand_aliases(grep_question, alias_groups=alias_groups)
    kind = _fast_kb_kind(grep_question) or "general"

    # 绘图模板确定性预匹配(与检索并行;问题里直接出现模板名 → 自动推模板卡
    # + 给模型注入带参数 schema 的绘图提示,保证绘图工具能被触发)
    chart_match_task = asyncio.create_task(match_templates_for_question(grep_question))
    # 全量短目录(与检索并行):模型看见平台当前全部模板,新模板上架后自动出现
    catalog_task = asyncio.create_task(build_catalog_hint())
    # 会话内已生成图表(与检索并行):追问改图时把旧参数+schema 注入,支持改参重画
    session_charts_task = asyncio.create_task(
        asyncio.to_thread(_load_session_charts, session_id, user_id)
    )

    async def _kb_prefetch() -> list[dict]:
        try:
            marker_version = chunk_index_marker_path().stat().st_mtime_ns
        except OSError:
            marker_version = 0
        key = make_inflight_key(
            user_id=user_id,
            question=grep_question,
            history=[],
            summary="",
            scope={"wiki_root": str(wiki_root), "index_version": marker_version},
            model_config={"stage": "retrieval-v2-chunk"},
        )

        async def _produce_retrieval() -> AsyncIterator[dict]:
            chunks = await hybrid_search(
                question=grep_question,
                keywords=keywords,
                wiki_root=wiki_root,
                top_k=10,
                candidate_k=20,
            )
            yield {"chunks": chunks}

        result: dict | None = None
        async for payload in _retrieval_inflight.stream(key, _produce_retrieval):
            result = payload
        if result is None:
            return []
        # 共享检索结果必须深拷贝:证据池会往 chunk 里写 ref 等字段
        return copy.deepcopy(result["chunks"])

    prefetched = await _kb_prefetch()
    yield _sse(
        "progress",
        {
            "stage": "retrieval",
            "keywords": keywords,
            "hit_canonicals": hit_canonicals,
            "candidates": [
                {
                    "chunk_id": c.get("chunk_id"),
                    "slug": c.get("slug"),
                    "title": c.get("title"),
                    "kind": c.get("kind"),
                    "heading": c.get("heading"),
                    "score": c.get("score"),
                    "retrieval_sources": c.get("retrieval_sources", []),
                }
                for c in prefetched
            ],
        },
    )

    # 1.5) 短目录 + 预匹配:目录几乎每次都注入;名字命中再推卡片和单模板 schema
    tool_hints = ""
    try:
        catalog_hint = await asyncio.wait_for(catalog_task, timeout=4.0)
    except Exception:  # noqa: BLE001
        catalog_task.cancel()
        catalog_hint = ""
    if catalog_hint:
        tool_hints = catalog_hint
    chart_payload: dict | None = None
    try:
        matched_templates = await asyncio.wait_for(chart_match_task, timeout=4.0)
    except Exception:  # noqa: BLE001
        chart_match_task.cancel()
        matched_templates = []
    if matched_templates:
        base_url = zhihuitu_base_url()
        chart_payload = {
            "base_url": base_url,
            "templates": build_template_cards(matched_templates, base_url),
        }
        yield _sse("chart_templates", chart_payload)
        try:
            match_hint = await build_chart_hint(matched_templates)
        except Exception:  # noqa: BLE001
            match_hint = ""
        if match_hint:
            tool_hints = f"{tool_hints}\n\n{match_hint}" if tool_hints else match_hint

    # 1.6) 会话内已生成图表 → 「图表修改提示」(追问改图的关键:没有它,
    # 模型看不到旧图参数,只会说"图片无法修改")
    try:
        session_charts = await asyncio.wait_for(session_charts_task, timeout=3.0)
    except Exception:  # noqa: BLE001
        session_charts_task.cancel()
        session_charts = []
    if session_charts:
        try:
            modify_hint = await build_session_chart_hint(session_charts)
        except Exception:  # noqa: BLE001
            modify_hint = ""
        if modify_hint:
            tool_hints = f"{tool_hints}\n\n{modify_hint}" if tool_hints else modify_hint

    # 2) Agent 循环
    evidence = EvidencePool()
    evidence.add_chunks(prefetched)
    # 问题点名了专业模板 → 本轮摘掉 generate_image,避免抢答画示意图
    blocked_tools = {"generate_image"} if matched_templates else None
    tools = build_tools(exclude=blocked_tools)
    image_queue: asyncio.Queue = asyncio.Queue()
    ctx: dict = {
        "wiki_root": wiki_root,
        "alias_groups": alias_groups,
        "evidence": evidence,
        "image_queue": image_queue,
        "images": {},
        "image_tasks": {},
        "events": [],
        # 当前用户的 kgqa token:generate_chart 用它换智绘图 token,
        # 以提问用户本人身份出图/存「我的图表」(账号同步,无共享服务账号)
        "kgqa_token": kgqa_token,
    }
    if chart_payload is not None:
        # 预匹配的模板卡先落 ctx;模型若再调 recommend_chart_templates 会覆盖为更准的结果
        ctx["chart_templates"] = chart_payload
    llm_stats: dict = {}
    tool_events: list[dict] = []
    answer_buf: list[str] = []
    answer: str | None = None
    timed_out = False

    agent_iter = run_agent_stream(
        question=question,
        history=history,
        summary=summary,
        evidence=evidence,
        tools=tools,
        ctx=ctx,
        llm_stats=llm_stats,
        tool_hints=tool_hints,
        required_first="generate_chart" if matched_templates else None,
    )
    try:
        async with asyncio.timeout(_AGENT_TIMEOUT_SECONDS):
            async for event in agent_iter:
                etype = event["type"]
                if etype == "token":
                    answer_buf.append(event["text"])
                    yield _sse("token", {"text": event["text"]})
                elif etype == "replace_answer":
                    answer_buf = [event["text"]]
                    yield _sse("replace_answer", {"text": event["text"]})
                elif etype == "tool_start":
                    payload = {
                        "tool": event["tool"],
                        "args": event.get("args") or {},
                        "reason": event.get("reason") or "",
                    }
                    tool_events.append({"type": "tool_start", **payload})
                    yield _sse("tool_start", payload)
                elif etype == "tool_result":
                    payload = {
                        "tool": event["tool"],
                        "ok": event.get("ok", True),
                        "summary": event.get("summary") or "",
                    }
                    tool_events.append({"type": "tool_result", **payload})
                    yield _sse("tool_result", payload)
                elif etype == "sse":
                    yield _sse(event["event"], event["data"])
                elif etype == "agent_done":
                    answer = event["answer"]
    except (asyncio.TimeoutError, TimeoutError):
        timed_out = True
        logger.warning("agent loop timed out after {}s", _AGENT_TIMEOUT_SECONDS)
        yield _sse(
            "warn",
            {
                "code": "agent_timeout",
                "message": "回答生成超时,以下为已生成的部分内容。",
            },
        )
    if answer is None:
        answer = "".join(answer_buf)

    if llm_stats.get("truncated") and not timed_out:
        yield _sse(
            "warn",
            {
                "code": "answer_truncated",
                "message": "答案在模型输出上限处被截断,内容可能不完整。可换个更聚焦的问法重试。",
                "usage": llm_stats.get("usage"),
            },
        )

    # 3) 引用校验清洗(chunk 协议,不做二次模型调用)
    # 3.0) 先清掉纯文本回合可能残留的 DSML 假工具调用,再进引用校验
    stripped = _strip_fake_tool_calls(answer)
    if stripped != answer:
        yield _sse("replace_answer", {"text": stripped})
        answer = stripped
    known_image_ids = {str(k) for k in (ctx.get("images") or {}) if k}
    stripped = _strip_fake_image_placeholders(answer, known_image_ids)
    if stripped != answer:
        yield _sse("replace_answer", {"text": stripped})
        answer = stripped
    # 正文暗示「上图」但本轮没有真实出图 → 补一句,避免用户以为图丢了
    if not known_image_ids and _claims_image_present(answer):
        note = "\n\n> 提示:本轮未生成图片。点名具体图表名(如帕累托图、技术路线图)后重试。"
        answer = answer.rstrip() + note
        yield _sse("replace_answer", {"text": answer})
    cleaned, evidence_validation = validate_and_clean_answer_v2(
        answer, evidence, wiki_root=wiki_root
    )
    if cleaned != answer:
        yield _sse("replace_answer", {"text": cleaned})
    answer = cleaned
    yield _sse("evidence_validation", evidence_validation)

    # 4) 引用详情(chunk 级 + web)+ path payload
    refs = extract_citation_refs(answer)
    citation_details = build_citation_details_v2(
        refs, evidence_pool=evidence, wiki_root=wiki_root
    )
    kb_slugs = extract_cited_slugs(answer)
    yield _sse(
        "citations",
        {"citations": citation_details, "domain_ie": domain_ie, "kind": kind},
    )
    yield _sse("path", {"nodes": kb_slugs, "edges": []})

    # 5) answer-quality 评分(candidates 用证据池 chunk 构造)
    scoring_candidates = [
        {
            "slug": it.get("slug"),
            "title": it.get("title"),
            "hit_segments": [{"text": it.get("text") or ""}],
        }
        for it in evidence.kb_items
    ]
    quality = evaluate_for_query(
        answer=answer,
        expanded_keywords=keywords,
        citations_slugs=kb_slugs,
        wiki_root=wiki_root,
        candidates=scoring_candidates,
        validation=evidence_validation,
        alias_groups=alias_groups,
    )
    yield _sse("score", quality)

    if quality["gap_flag"]:
        try:
            await append_gap(
                origin="query",
                user_question=question,
                gap_reasons=quality["reasons"],
                suggested_seeds=keywords[:6],
            )
        except Exception as e:  # noqa: BLE001
            yield _sse("warn", {"stage": "gap_queue", "error": str(e)})

    # 6) 持久化(按 question_kind 分目录;引用/图片与问答窗口同形)
    images_meta = [_image_meta_entry(img) for img in ctx["images"].values()]
    web_citations_meta = [
        {"ref": it["ref"], "title": it["title"], "url": it["url"]}
        for it in evidence.web_items
    ]
    persisted_slug = await maybe_persist(
        question=question,
        answer=answer,
        citations=kb_slugs,
        wiki_root=wiki_root,
        quality=quality,
        domain_ie=domain_ie,
        force=persist_override,
        user_id=user_id,
        question_kind=kind,
        session_id=session_id,
        images=images_meta,
        web_citations=web_citations_meta,
        citation_details=citation_details,
    )
    yield _sse(
        "persist",
        {
            "persisted_query_slug": persisted_slug,
            "force": persist_override,
            "cited_count": len(kb_slugs),
            "kind": kind,
        },
    )

    # 7) done 先发,追问建议随后补一个事件
    yield _sse("done", {"tokens": len(answer), "cited_count": len(kb_slugs)})
    followups = await generate_followups(question=question, answer=answer)
    if followups:
        yield _sse("followup_suggestions", {"suggestions": followups})

    # 8) 落库(富媒体数据必须全部进 meta,前端 loadSession 会覆盖本地状态)
    if session_id is not None:
        meta = {
            "route": "knowledge_base",
            "quality": quality,
            "citations": citation_details,
            "path": {"nodes": kb_slugs, "edges": []},
            "domain_ie": domain_ie,
            "kind": kind,
            "followups": followups,
            "evidence_validation": evidence_validation,
            "tool_events": tool_events,
            "images": images_meta,
            "chart_templates": ctx.get("chart_templates"),
            "web_citations": web_citations_meta,
            "persist": {
                "persisted_query_slug": persisted_slug,
                "force": persist_override,
            },
        }
        if did_rewrite:
            meta["rewritten"] = rewritten
        msg_id = _persist_assistant_msg(
            session_id, user_id, answer,
            meta=meta,
            persisted_query_slug=persisted_slug,
        )
        if msg_id is not None and ctx["image_tasks"]:
            _attach_image_meta_writeback(msg_id, ctx)
        await _trigger_auto_summarize(session_id, user_id)

    # 9) done 之后保持连接等 pending 图片(最多 60s),超时发 warn 后关闭
    # 每个后台图片任务恰好往 image_queue 推 1 个事件(image_ready 或 image_failed)
    expected_events = len(ctx["image_tasks"])
    if expected_events:
        deadline = asyncio.get_event_loop().time() + _IMAGE_WAIT_SECONDS
        delivered = 0
        while delivered < expected_events:
            timeout = deadline - asyncio.get_event_loop().time()
            try:
                evt = await asyncio.wait_for(image_queue.get(), timeout=max(timeout, 0.01))
            except asyncio.TimeoutError:
                yield _sse(
                    "warn",
                    {
                        "code": "image_wait_timeout",
                        "message": "图片生成超时,请稍后在历史会话中查看。",
                    },
                )
                break
            delivered += 1
            yield _sse(evt["event"], evt["data"])


def _detect_domain_ie(wiki_root) -> bool:
    """从 .wiki-schema.md 成长配置读 domain_ie;读不到默认 False。"""
    p = wiki_root / ".wiki-schema.md"
    if not p.exists():
        return False
    txt = p.read_text(encoding="utf-8")
    return "domain_ie: true" in txt or "domain_ie:true" in txt


@router.post("/query")
async def query_stream(
    payload: QueryRequest,
    user_id: CurrentUserId,
    authorization: str | None = Header(default=None),
) -> EventSourceResponse:
    """SSE 流式查询。

    必须带 session_id(前端先 POST /conversations 拿一个)。
    history/summary 由后端从 DB 装载;前端可不传(老前端兼容时仍读 payload.history)。
    """
    # 1) 装载历史:优先从 DB 按 session_id 读,前端没传 session_id 时回退用 payload
    history: list[dict]
    summary: str
    if payload.session_id is not None:
        history, summary = _load_history_for_session(payload.session_id, user_id)
    else:
        history = [m.model_dump() for m in (payload.history or [])]
        summary = payload.summary or ""

    # 2) 持久化用户消息(在 SSE 启动前,避免 SSE 中途断连丢消息)
    if payload.session_id is not None:
        _persist_user_msg(payload.session_id, user_id, payload.question)

    # 提取原始 bearer token:绘图工具用它以提问用户身份对接智绘图
    kgqa_token = ""
    if authorization and authorization.lower().startswith("bearer "):
        kgqa_token = authorization.split(" ", 1)[1].strip()

    return EventSourceResponse(
        _stream_answer(
            question=payload.question,
            domain_ie_override=payload.domain_ie,
            persist_override=payload.persist,
            user_id=user_id,
            history=history,
            summary=summary,
            session_id=payload.session_id,
            kgqa_token=kgqa_token,
        )
    )


@router.get("/queries/{slug}", response_model=QueryFinalResponse)
async def get_query_page(slug: str, user_id: CurrentUserId) -> QueryFinalResponse:
    """读已持久化的 query 页(wiki/queries/{slug}.md)。"""
    wiki_root = settings.wiki_root_path
    p = safe_join(wiki_root, "wiki", "queries", f"{slug}.md")
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"query page {slug!r} not found")
    fm, body = parse_text(p.read_text(encoding="utf-8"))

    citations_slugs = list(fm.get("citations") or [])
    citation_details = build_citation_details(citations_slugs, wiki_root=wiki_root)
    citations = [Citation(**item) for item in citation_details]

    score = fm.get("answer_quality")
    tier = fm.get("tier")
    quality = AnswerQuality(
        score=float(score) if score is not None else 0.0,
        tier=tier if tier in ("gold", "silver", "bronze") else "unknown",
        cited_sources=len(citations_slugs),
        gap_flag=bool(fm.get("gap_flag", False)),
    )
    # 抽 "## 答案" 段
    answer = body
    marker = "## 答案"
    if marker in body:
        rest = body.split(marker, 1)[1]
        # 切到下一个 H2 之前
        next_h2 = rest.find("\n## ")
        answer = (rest[:next_h2] if next_h2 != -1 else rest).strip()
    return QueryFinalResponse(
        answer=answer,
        citations=citations,
        quality=quality,
        persisted_query_slug=slug,
        path_payload={"nodes": citations_slugs, "edges": []},
    )
