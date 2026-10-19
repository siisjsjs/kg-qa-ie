"""ingestion/step2_compose.py:Step2 页面生成 + 跨 source 合并(深度模式)。

P5+ 改造为 3 阶段:
  - Step2a (overview):一次 LLM 调用产出 source 摘要页 + 已有实体追加段
                       (用 prompts/ingest_step2_overview.md)
  - Step2b (entity):  对每个 new_entity_name 单独调一次 LLM,产出深度实体页
                       (用 prompts/ingest_step2_entity.md)
  - Step2c (topic):   对每个 new_topic_name 单独调一次 LLM,产出深度主题页
                       (用 prompts/ingest_step2_topic.md)

每个新建实体/主题独占一次 LLM 调用 + 充分 token 预算,确保单页深度。
单实体/单主题失败不阻塞其他 — 失败那条 logger.warning 跳过。

Step2b 与 Step2c 在同一个 gather 里并发(它们互相不依赖),并用全局信号量
`_LLM_CONCURRENCY`(默认 5)控制实际峰值并发,避免 30+ 任务一次性 fire 触发
provider 限流或 client-side pool wait → 全部 ReadTimeout 风暴。

兼容旧调用方:`step2_compose(...)` 入口签名不变,仍返回 {file_path: md_text}。
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from loguru import logger

from app.services.llm import ChatMessage, get_llm_provider
from app.services.llm.base import EXTRACTION_MAX_TOKENS
from app.services.wiki.frontmatter_io import parse_text, serialize

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
STEP2_OVERVIEW_TEMPLATE = (PROMPTS_DIR / "ingest_step2_overview.md").read_text(encoding="utf-8")
STEP2_ENTITY_TEMPLATE = (PROMPTS_DIR / "ingest_step2_entity.md").read_text(encoding="utf-8")
STEP2_TOPIC_TEMPLATE = (PROMPTS_DIR / "ingest_step2_topic.md").read_text(encoding="utf-8")

FILE_DELIMITER_PATTERN = re.compile(
    # 容错形式:
    #   1) =====<file: X>=====      (单行,正常)
    #   2) =====\n<file: X>=====    (LLM 把分隔符换行,line 1 = '=====',line 2 = '<file: X>=====')
    #   3) =====<file: X>=====\n=====  (LLM 把分隔符尾再换一行)
    #   4) =====\n<file: X>\n=====   (LLM 把 '>' 与尾部 '=====' 之间也换行,三行式)
    # 关键约束:`<file:` 前后允许有 `=====`,`>` 与尾部 `=` 间允许 \s(含换行),提取 path
    r"=+\s*<file:\s*([^>]+?)\s*>\s*=+",
    re.MULTILINE | re.DOTALL,
)

# Step2b/2c 同时在飞的 LLM 调用上限。从 config_store 读;读不到默认值。
# 值过小退化为串行;值过大触发 provider 限流 + httpx 连接池阻塞 → ReadTimeout 风暴。
# 默认 20:实测(2026-06)当前网关并发 14 仍全部成功、单次≈30s、总耗时≈最慢单次(无限流),
# 一章典型产出 ~27 个实体/主题页,提到 30 可让绝大多数任务一批并发完成,
# compose 墙钟从 ~177s(并发10,约3批)降到 ~45s(约1.5批)。需要更高可由
# config_store "ingestion.llm_concurrency" 覆盖(上限 50)。
_DEFAULT_LLM_CONCURRENCY = 30


def _read_llm_concurrency() -> int:
    try:
        from app.services.admin import config_store

        v = config_store.get_sync("ingestion.llm_concurrency", default="")
        if v:
            n = int(v)
            return max(1, min(n, 50))
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_LLM_CONCURRENCY


# ---------------------------------------------------------------------------
# 工具:prompt 渲染辅助
# ---------------------------------------------------------------------------


def _build_related_pages_block(related_pages: dict[str, str]) -> str:
    """把 {slug: page_text} 拼成给 LLM 的"现有相关 wiki 页"区块。

    单页正文超 1500 字时只截前 1500 字 + 省略号,避免 prompt 过长。
    """
    if not related_pages:
        return "(无)"
    parts: list[str] = []
    for slug, text in related_pages.items():
        snippet = text if len(text) <= 1500 else text[:1500] + "\n…(已截断)"
        parts.append(f"### slug: {slug}\n\n{snippet}")
    return "\n\n---\n\n".join(parts)


def _build_aliases_block(alias_groups: list[list[str]]) -> str:
    if not alias_groups:
        return "(无)"
    return "\n".join(f"- " + " = ".join(g) for g in alias_groups[:60])


# ---------------------------------------------------------------------------
# 输出解析
# ---------------------------------------------------------------------------


def parse_files(text: str) -> dict[str, str]:
    """从 LLM 输出按 `=====<file: ...>=====` 拆成 {file_path: md_text}。

    分隔符必须独占一行。两个分隔符之间的所有文本(去掉首尾空白)即为该文件内容。
    第一个分隔符之前的"前言"会被丢弃。
    """
    matches = list(FILE_DELIMITER_PATTERN.finditer(text))
    if not matches:
        return {}
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        path = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip("\n")
        # 去除可能的 ```markdown 围栏
        content = re.sub(r"^```(?:markdown|md)?\s*\n", "", content)
        content = re.sub(r"\n```\s*$", "", content)
        out[path] = content.strip("\n") + "\n"
    return out


def _strip_md_fence(text: str) -> str:
    """去除单独包裹的 ```markdown / ``` 围栏。"""
    s = text.strip()
    s = re.sub(r"^```(?:markdown|md)?\s*\n", "", s)
    s = re.sub(r"\n```\s*$", "", s)
    return s.strip("\n") + "\n"


# ---------------------------------------------------------------------------
# 各阶段调用
# ---------------------------------------------------------------------------


async def _call_llm(messages: list[ChatMessage], *, max_tokens: int = EXTRACTION_MAX_TOKENS, _label: str = "llm") -> str:
    import time as _t
    provider = get_llm_provider()
    t0 = _t.perf_counter()
    # 默认给足输出上限(质量优先);调用方仍可传更小值覆盖。
    kwargs = {"max_tokens": max_tokens} if max_tokens and max_tokens > 0 else {}
    # 结构化编排依赖模型推理,显式恢复(reasoning_effort=None 移除默认的 none)
    raw = await provider.chat(messages, stream=False, reasoning_effort=None, **kwargs)
    dt = _t.perf_counter() - t0
    txt = raw if isinstance(raw, str) else ""
    logger.info(f"[ingest-trace] {_label} done: {dt:.2f}s, out_chars={len(txt)}")
    return txt


async def step2a_overview(
    *,
    raw_md: str,
    step1_result: dict,
    related_pages: dict[str, str],
) -> dict[str, str]:
    """Step2a:source 摘要页 + 已有实体追加段。

    返回 {file_path: full_md_text}。
    """
    from app.services.ingestion.step1_extract import _read_max_raw_md_chars
    max_chars = _read_max_raw_md_chars()
    prompt = (
        STEP2_OVERVIEW_TEMPLATE
        .replace("{step1_json}", json.dumps(step1_result, ensure_ascii=False, indent=2))
        .replace("{raw_md}", raw_md[:max_chars])
        .replace("{related_pages_block}", _build_related_pages_block(related_pages))
    )
    messages = [
        ChatMessage(
            role="system",
            content="你是 wiki 页面生成助手。严格按 ===== 分隔符输出,不要其他解释。",
        ),
        ChatMessage(role="user", content=prompt),
    ]
    raw_text = await _call_llm(messages, _label="step2a_overview")
    files = parse_files(raw_text)
    if not files:
        raise ValueError(
            f"Step2a LLM 输出未包含任何 =====<file: ...>===== 分隔符。原始片段:{raw_text[:300]}"
        )
    return files


async def step2b_entity(
    *,
    entity: dict,
    raw_md: str,
    source_slug: str,
    related_pages: dict[str, str],
    alias_groups: list[list[str]],
    entity_names: list[str],
) -> tuple[str, str]:
    """Step2b:为单个新建实体生成深度页。

    返回 (file_path, md_text)。失败时抛异常,由调用方捕获并跳过。
    """
    from app.services.ingestion.step1_extract import _read_max_raw_md_chars
    max_chars = _read_max_raw_md_chars()
    entity_block = "\n".join(f"- {n}" for n in entity_names[:120]) or "(无)"
    prompt = (
        STEP2_ENTITY_TEMPLATE
        .replace("{entity_json}", json.dumps(entity, ensure_ascii=False, indent=2))
        .replace("{raw_md}", raw_md[:max_chars])
        .replace("{source_slug}", source_slug or "")
        .replace("{related_pages_block}", _build_related_pages_block(related_pages))
        .replace("{aliases_block}", _build_aliases_block(alias_groups))
        .replace("{entity_names_block}", entity_block)
    )
    messages = [
        ChatMessage(
            role="system",
            content=(
                "你是工业工程知识库的实体页生成专家。"
                "直接输出一份完整的 markdown(含 frontmatter),不要分隔符、不要前后解释、不要 ```markdown 围栏。"
                "严格按 9 段骨架展开,每段都要写满,不允许省略。"
            ),
        ),
        ChatMessage(role="user", content=prompt),
    ]
    raw_text = await _call_llm(messages, _label=f"step2b_entity[{entity.get('name')}]")
    md = _strip_md_fence(raw_text)
    if not md.lstrip().startswith("---"):
        raise ValueError(f"Step2b 实体 {entity.get('name')!r} 输出未以 frontmatter 开头:{md[:200]}")
    name = entity.get("name") or "unknown"
    md = _align_fm_slug(md, name=name, kind="entity")
    file_path = f"wiki/entities/{name}.md"
    return file_path, md


async def step2c_topic(
    *,
    topic: dict,
    raw_md: str,
    source_slug: str,
    entity_names: list[str],
    related_pages: dict[str, str],
) -> tuple[str, str]:
    """Step2c:为单个新建主题生成深度页。

    返回 (file_path, md_text)。
    """
    from app.services.ingestion.step1_extract import _read_max_raw_md_chars
    max_chars = _read_max_raw_md_chars()
    entity_block = "\n".join(f"- {n}" for n in entity_names[:80]) or "(无)"
    prompt = (
        STEP2_TOPIC_TEMPLATE
        .replace("{topic_json}", json.dumps(topic, ensure_ascii=False, indent=2))
        .replace("{raw_md}", raw_md[:max_chars])
        .replace("{source_slug}", source_slug or "")
        .replace("{entity_names_block}", entity_block)
        .replace("{related_pages_block}", _build_related_pages_block(related_pages))
    )
    messages = [
        ChatMessage(
            role="system",
            content=(
                "你是工业工程知识库的主题页生成专家。"
                "直接输出一份完整的 markdown(含 frontmatter),不要分隔符、不要前后解释。"
                "严格按 8 段骨架展开,每段都要写满。"
            ),
        ),
        ChatMessage(role="user", content=prompt),
    ]
    raw_text = await _call_llm(messages, _label=f"step2c_topic[{topic.get('name')}]")
    md = _strip_md_fence(raw_text)
    if not md.lstrip().startswith("---"):
        raise ValueError(f"Step2c 主题 {topic.get('name')!r} 输出未以 frontmatter 开头:{md[:200]}")
    name = topic.get("name") or "unknown"
    md = _align_fm_slug(md, name=name, kind="topic")
    file_path = f"wiki/topics/{name}.md"
    return file_path, md


# ---------------------------------------------------------------------------
# 顶层入口:对外签名兼容
# ---------------------------------------------------------------------------


async def step2_compose(
    *,
    raw_md: str,
    step1_result: dict,
    related_pages: dict[str, str],
    alias_groups: list[list[str]] | None = None,
) -> dict[str, str]:
    """深度模式 Step2:Step2a → Step2b × N(并发)→ Step2c × M(并发)。

    Args:
        raw_md: 原始素材文本
        step1_result: validate_step1 通过后的字典
        related_pages: {slug: existing_page_text} — matched_entity_pages /
                       matched_topic_pages 对应的现有 wiki 页正文,供 Step2a 做合并参考。
        alias_groups: 别名词表,Step2b 写实体页时透传给 LLM 作为已知规范名约束。

    Returns:
        {"wiki/sources/...md": "...", "wiki/entities/...md": "...", ...}

    单实体/单主题失败不阻塞其他 — 失败那条 logger.warning 跳过。

    **向后兼容**:若 Step2a 的 LLM 输出已经包含所有 new_entity / new_topic 的完整页
    (老式一次产出多页),则跳过 Step2b/2c 直接返回。
    """
    alias_groups = alias_groups or []
    out_files: dict[str, str] = {}

    # ---- Step2a ----
    overview_files = await step2a_overview(
        raw_md=raw_md,
        step1_result=step1_result,
        related_pages=related_pages,
    )
    out_files.update(overview_files)

    # 提取 source slug:source 摘要页一定有,Step2a 必产
    source_slug: str = ""
    for path in overview_files:
        if path.startswith("wiki/sources/") and path.endswith(".md"):
            source_slug = Path(path).stem
            break
    if not source_slug:
        # Step2a 没产 source 页 → 以一个 fallback slug 兜底,避免后续实体页 sources=[""]
        from datetime import datetime, timezone
        source_slug = f"unknown-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        logger.warning(f"Step2a 未生成 source 摘要页;使用 fallback slug={source_slug}")

    # Step2a 只产 source 摘要页 + matched 观察段;新建实体/主题页一律由 Step2b/2c 深度生成。
    # 曾有一处"向后兼容旁路"在 new_vs_existing 为空(step1 输出被截断丢了尾部)时直接跳过
    # 2b/2c → 整批实体只剩 void-fill 桩。已移除:深度生成不再可被静默跳过。
    nve = step1_result.get("new_vs_existing") or {}

    entities_by_name: dict[str, dict] = {
        e.get("name"): e for e in (step1_result.get("entities") or []) if e.get("name")
    }
    topics_by_name: dict[str, dict] = {
        t.get("name"): t for t in (step1_result.get("topics") or []) if t.get("name")
    }
    matched_entity_names: set[str] = {
        m.get("name") for m in (nve.get("matched_entity_pages") or []) if m.get("name")
    }
    matched_entity_slugs: set[str] = {
        m.get("slug") for m in (nve.get("matched_entity_pages") or []) if m.get("slug")
    }
    matched_names_all: set[str] = matched_entity_names | matched_entity_slugs
    all_entity_names = list(entities_by_name.keys()) + [
        m.get("name") for m in (nve.get("matched_entity_pages") or []) if m.get("name")
    ]

    # —— Req2 无虚空实体:调度集 = 所有"识别出的"实体,而非仅新建清单 ——
    # 主题的 related_entities 里出现、但既不在新建清单、也没匹配到现有页的实体,
    # 过去会被漏掉 → 主题里 [[X]] 变 broken link(虚空实体)。这里把它们一并纳入生成。
    related_entity_names: set[str] = set()
    for t in (step1_result.get("topics") or []):
        for en in t.get("related_entities") or []:
            if en:
                related_entity_names.add(str(en))

    # —— P2 跨素材深合并:matched(命中现有页)实体也跑深度 Step2b ——
    # 旧逻辑把 matched 排除出 Step2b,只靠 Step2a 产 ~300 字浅摘要追加 → 现有实体页被
    # 后续素材"再次命中"时,新素材的内容信息稀薄(典型:工业工程 第二章建页很深,第一章
    # 命中后只补 800 字)。改为:matched 也生成一份本素材视角的深度页,materialize 合并时
    # 经 distill_observation 蒸馏为「## 不同素材中的观点 / ### 来源」下的多段落观点块,
    # 每个 source 的贡献都拿到充分篇幅,而非 300 字摘要。
    schedule_entity_names: set[str] = (
        set(entities_by_name.keys())
        | related_entity_names | matched_names_all
    )

    # ---- Step2b + Step2c 一并起,统一信号量限流 ----
    # 关键:实体页生成与主题页生成互不依赖,放同一个 gather 里 — 比之前
    # "等所有实体完再开始主题"省一半墙钟时间。信号量保证峰值并发可控。
    sem = asyncio.Semaphore(_read_llm_concurrency())

    async def _bounded_step2b(entity: dict) -> tuple[str, str] | None:
        async with sem:
            return await _safe_step2b(
                entity=entity,
                raw_md=raw_md,
                source_slug=source_slug,
                related_pages=related_pages,
                alias_groups=alias_groups,
                entity_names=all_entity_names,
            )

    async def _bounded_step2c(topic: dict) -> tuple[str, str] | None:
        async with sem:
            return await _safe_step2c(
                topic=topic,
                raw_md=raw_md,
                source_slug=source_slug,
                entity_names=all_entity_names,
                related_pages=related_pages,
            )

    tasks: list = []
    for name in sorted(schedule_entity_names):
        # matched 实体:Step2a 只产了"观点段薄桩"(非完整页),必须照常跑深度 Step2b,
        # 其产出会在 out_files 里覆盖薄桩;故 matched 不受 overview 已存在的跳过约束。
        if name not in matched_names_all and f"wiki/entities/{name}.md" in overview_files:
            continue
        # Step1 抽到详情就用详情;只在 related_entities 里出现(无详情)则合成最小 dict,
        # 让 Step2b 凭 raw_md 全文 + 相关页上下文也能生成一份完整实体页。
        ent = entities_by_name.get(name) or {"name": name}
        tasks.append(_bounded_step2b(ent))

    for name in sorted(topics_by_name.keys()):
        if f"wiki/topics/{name}.md" in overview_files:
            continue
        tp = topics_by_name.get(name)
        if tp is None:
            continue
        tasks.append(_bounded_step2c(tp))

    if tasks:
        logger.info(
            f"step2_compose: 并发跑 {len(tasks)} 个 step2b/2c 任务,"
            f"信号量上限={sem._value if hasattr(sem, '_value') else '?'}"
        )
        results = await asyncio.gather(*tasks, return_exceptions=False)
        for r in results:
            if r is None:
                continue
            path, md = r
            out_files[path] = md

    return out_files


async def _safe_step2b(**kwargs) -> tuple[str, str] | None:
    """Step2b 单实体调用包装:重试至多 3 次仍失败 → 抛 IngestQualityError(不再产占位页)。

    质量优先(2026-06-11):不再落「_待补充_」骨架占位页(那是污染数据)。任一实体
    三次都生成失败 → 抛异常 → gather 整体抛出 → 整篇文档 job 失败,磁盘零写入。
    """
    from app.core.exceptions import IngestQualityError

    entity = kwargs.get("entity") or {}
    name = entity.get("name", "?")
    last_err: Exception | None = None
    for attempt in (1, 2, 3):
        try:
            return await step2b_entity(**kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(f"Step2b 实体 {name!r} 生成失败(第{attempt}次):{e}")
    raise IngestQualityError(
        f"实体 {name!r} 成稿在 3 次尝试后仍失败: {last_err}"
    ) from last_err


async def _safe_step2c(**kwargs) -> tuple[str, str] | None:
    """Step2c 单主题调用包装:重试至多 3 次仍失败 → 抛 IngestQualityError(不再产占位页)。"""
    from app.core.exceptions import IngestQualityError

    topic = kwargs.get("topic") or {}
    name = topic.get("name", "?")
    last_err: Exception | None = None
    for attempt in (1, 2, 3):
        try:
            return await step2c_topic(**kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(f"Step2c 主题 {name!r} 生成失败(第{attempt}次):{e}")
    raise IngestQualityError(
        f"主题 {name!r} 成稿在 3 次尝试后仍失败: {last_err}"
    ) from last_err


def _align_fm_slug(md: str, *, name: str, kind: str) -> str:
    """强制 frontmatter.slug/title = 文件名 name(权威规范名)。

    LLM 写实体/主题页时偶尔会把 slug/title 改成语义近似的别名(如把「精益生产」
    写成「丰田生产方式」)。文件名按 name 落盘,frontmatter.slug 却成了别的 → 文件名
    与 slug 错位:磁盘按文件名扫描判 0 虚空,DB 按 slug 入库,正文 [[name]] 在 DB
    join 不到页面 → 前端虚空链接 + 该 name 成无主题悬空节点。

    落盘前把 slug/title 钉死为 name;若 LLM 写的名字与 name 不同,降级收进 aliases
    (不丢信息)。质量优先(2026-06-11):解析失败不再原样返回(那会落一份 slug 错位
    的污染页),直接抛 IngestQualityError → 整篇 job 失败。
    """
    from app.core.exceptions import IngestQualityError

    try:
        fm, body = parse_text(md)
    except Exception as e:  # noqa: BLE001
        raise IngestQualityError(
            f"{kind} 页 {name!r} 的 frontmatter 解析失败,无法对齐 slug: {e}"
        ) from e
    llm_slug = str(fm.get("slug") or "").strip()
    llm_title = str(fm.get("title") or "").strip()
    if llm_slug and llm_slug != name:
        aliases = [str(a).strip() for a in (fm.get("aliases") or []) if str(a).strip()]
        for cand in (llm_slug, llm_title):
            if cand and cand != name and cand not in aliases:
                aliases.append(cand)
        fm["aliases"] = aliases
    fm["slug"] = name
    fm["title"] = name
    fm["kind"] = kind
    return serialize(fm, body)

