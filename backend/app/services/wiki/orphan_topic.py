"""把章节里"无主题分组"的孤立实体综合成高质量 topic 页。

背景:书内目录树里,每个 source(章节)下有 topic→entity 两层,但有一批实体
只引用了 source、没被任何 topic 的 related_entities 覆盖 → 落进"(无主题分组)"桶。
本模块对这些孤立实体做 LLM 综合,生成新的 topic 页(sources 指回该 source,
从而继承 book),让它们被正确归类。

设计:
  - 一个 source 的所有孤立实体一次性喂给 LLM(带各自摘要),产出 1 个 topic。
  - 实体太多时分批(每批 _MAX_ENTITIES_PER_TOPIC),产出多个 topic。
  - 生成的 topic 标 auto_synthesized=true 便于追溯。
  - LLM 调用走全局信号量(沿用 ingest 的 LLM_CONCURRENCY)。
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.llm import ChatMessage, get_llm_provider
from app.services.wiki.book_tree import build_book_index, build_book_tree
from app.services.wiki.frontmatter_io import parse_text, serialize
from app.services.wiki.fs_io import atomic_write, safe_join, safe_slug, slugify
from app.services.wiki.link_resolver import topic_member_slugs

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
_TEMPLATE = PROMPTS_DIR / "synthesize_orphan_topic.md"

_MAX_ENTITIES_PER_TOPIC = 12  # 单个 topic 别塞太多实体,超了分批

_DEFAULT_LLM_CONCURRENCY = 10


def _read_llm_concurrency() -> int:
    """沿用 ingest 的并发配置(ingestion.llm_concurrency),控制综合时的峰值并发。"""
    try:
        from app.services.admin import config_store

        v = config_store.get_sync("ingestion.llm_concurrency", default="")
        if v:
            return max(1, min(int(v), 50))
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_LLM_CONCURRENCY


def _entity_summary(fm: dict, body: str) -> str:
    """取实体的一句话摘要:优先 frontmatter.brief,否则正文首段截断。"""
    brief = fm.get("brief")
    if brief:
        return _strip_wikilinks(str(brief).strip())
    # 取正文第一行非标题/非引用/非列表文本(逐行扫,容忍「## 标题\n正文」单换行粘连)
    for line in body.split("\n"):
        p = line.strip()
        if p and not p.startswith(("#", ">", "-", "|")):
            return _strip_wikilinks(p)[:200]
    return ""


def _strip_wikilinks(s: str) -> str:
    """摘要里的 [[X]] / [[X|别名]] 还原为纯文本。

    摘要会被拼进其它页面的链接段(如 topic「关键实体」),残留双链会被
    extract_section_links 误识别为该段成员 → 幽灵成员。
    """
    return re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), s)


def _read_entity(wiki_root: Path, slug: str) -> tuple[dict, str] | None:
    p = safe_join(wiki_root, "wiki", "entities", f"{slug}.md")
    if not p.exists():
        return None
    try:
        return parse_text(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _build_entities_block(wiki_root: Path, slugs: list[str]) -> tuple[str, list[str], list[str]]:
    """构造喂给 LLM 的实体清单文本,返回 (block, valid_slugs, domains)。"""
    lines: list[str] = []
    valid: list[str] = []
    domains: set[str] = set()
    for slug in slugs:
        got = _read_entity(wiki_root, slug)
        if got is None:
            continue
        fm, body = got
        summary = _entity_summary(fm, body)
        title = str(fm.get("title") or slug)
        for d in fm.get("domains") or []:
            domains.add(str(d))
        lines.append(f"- `{slug}`(标题:{title}):{summary or '(无摘要)'}")
        valid.append(slug)
    return "\n".join(lines), valid, sorted(domains)


def _strip_fence(md: str) -> str:
    md = md.strip()
    md = re.sub(r"^```(?:markdown|md)?\s*\n", "", md)
    md = re.sub(r"\n```\s*$", "", md)
    return md.strip()


def _chunk(lst: list[str], size: int) -> list[list[str]]:
    return [lst[i : i + size] for i in range(0, len(lst), size)]


async def _synthesize_one(
    *,
    wiki_root: Path,
    source_slug: str,
    source_title: str,
    entity_slugs: list[str],
    dry_run: bool,
) -> dict[str, Any]:
    """对一组孤立实体生成 1 个 topic 页。dry_run=True 时只返回预览不落盘。"""
    block, valid, domains = _build_entities_block(wiki_root, entity_slugs)
    if len(valid) < 1:
        return {"ok": False, "error": "valid_entities<1", "source": source_slug}

    template = _TEMPLATE.read_text(encoding="utf-8")
    prompt = (
        template
        .replace("{source_slug}", source_slug)
        .replace("{source_title}", source_title)
        .replace("{entities_block}", block)
    )
    messages = [
        ChatMessage(role="system", content="你是工业工程领域的知识工程师,擅长把零散概念归纳成结构化主题。严格按要求输出,不编造。"),
        ChatMessage(role="user", content=prompt),
    ]
    provider = get_llm_provider()
    raw = await provider.chat(messages, stream=False)
    md = _strip_fence(raw if isinstance(raw, str) else "")
    if not md or "---" not in md:
        return {"ok": False, "error": "LLM 输出无 frontmatter", "source": source_slug}

    try:
        fm, body = parse_text(md)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"frontmatter 解析失败: {e}", "source": source_slug}

    title = str(fm.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "no_title", "source": source_slug}

    # related_entities 收敛到真实给出的实体,过滤幻觉
    valid_set = set(valid)
    related = [s for s in (fm.get("related_entities") or []) if str(s) in valid_set]
    # 保证零孤立:本批所有有效实体都必须被归入(LLM 可能漏选),按 LLM 顺序 + 补齐遗漏
    seen = set(related)
    for s in valid:
        if s not in seen:
            related.append(s)
            seen.add(s)

    slug = slugify(title)
    today = date.today().isoformat()
    fm["title"] = title
    fm["slug"] = slug
    fm["kind"] = "topic"
    fm["sources"] = [source_slug]
    fm["related_entities"] = related
    if domains and not fm.get("domains"):
        fm["domains"] = domains[:3]
    fm.setdefault("version", 1)
    fm.setdefault("created", today)
    fm["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    fm["auto_synthesized"] = True

    target = safe_join(wiki_root, "wiki", "topics", f"{slug}.md")
    result = {
        "ok": True,
        "source": source_slug,
        "topic_slug": slug,
        "topic_title": title,
        "related_count": len(related),
        "exists": target.exists(),
    }
    if dry_run:
        result["preview"] = serialize(fm, body)[:1200]
        return result

    # slug 撞已有 topic 时加后缀,避免覆盖人写的页
    if target.exists():
        slug2 = f"{slug}-综合"
        target = safe_join(wiki_root, "wiki", "topics", f"{slug2}.md")
        fm["slug"] = slug2
        result["topic_slug"] = slug2
    await atomic_write(target, serialize(fm, body))
    rel = target.relative_to(wiki_root).as_posix()
    result["written"] = rel
    logger.info(f"[orphan_topic] {source_slug}: wrote {rel} ({len(related)} entities)")
    return result


def collect_orphan_work(wiki_root: Path) -> list[dict[str, Any]]:
    """扫所有书,收集每个 source 的孤立实体清单 + 该 source 已有主题。

    返回 [{source_slug, source_title, book, orphan_slugs:[...], existing_topic_slugs:[...]}],
    只含有孤立实体的章节。existing_topic_slugs 供分类时"优先归入已有主题"使用。
    """
    wiki_root = Path(wiki_root).resolve()
    idx = build_book_index(wiki_root)
    work: list[dict[str, Any]] = []
    for b in idx["books"]:
        book = b["book"]
        if book == "未分类":
            continue
        tree = build_book_tree(book, wiki_root)
        for c in tree["chapters"]:
            orphans = c.get("orphan_entities") or []
            if not orphans:
                continue
            work.append({
                "source_slug": c["slug"],
                "source_title": c["title"],
                "book": book,
                "orphan_slugs": [e["slug"] for e in orphans],
                "existing_topic_slugs": [t["slug"] for t in (c.get("topics") or [])],
            })
    return work


async def synthesize_orphan_topics(
    *,
    wiki_root: Path,
    dry_run: bool = False,
    limit_sources: int | None = None,
    only_source: str | None = None,
) -> dict[str, Any]:
    """全量(或受限)地把孤立实体综合成 topic 页。

    Args:
        dry_run: 只预览,不落盘
        limit_sources: 只处理前 N 个章节(冒烟测试用)
        only_source: 只处理指定 source_slug(单点测试用)
    """
    wiki_root = Path(wiki_root).resolve()
    work = collect_orphan_work(wiki_root)
    if only_source:
        work = [w for w in work if w["source_slug"] == only_source]
    if limit_sources is not None:
        work = work[:limit_sources]

    generated: list[dict] = []
    failed: list[dict] = []

    # 把所有 (source, batch) 摊平成任务列表,统一用信号量限流并发跑 —
    # 沿用 ingest 的 llm_concurrency 配置,避免 70+ 任务一次性 fire。
    tasks: list[tuple[dict, list[str]]] = []
    for w in work:
        for batch in _chunk(w["orphan_slugs"], _MAX_ENTITIES_PER_TOPIC):
            tasks.append((w, batch))

    sem = asyncio.Semaphore(_read_llm_concurrency())

    async def _bounded(w: dict, batch: list[str]) -> dict[str, Any]:
        async with sem:
            try:
                return await _synthesize_one(
                    wiki_root=wiki_root,
                    source_slug=w["source_slug"],
                    source_title=w["source_title"],
                    entity_slugs=batch,
                    dry_run=dry_run,
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(f"[orphan_topic] exception on {w['source_slug']}")
                return {"ok": False, "source": w["source_slug"], "error": str(e)}

    results = await asyncio.gather(*[_bounded(w, b) for w, b in tasks])
    for r in results:
        if r.get("ok"):
            generated.append(r)
        else:
            failed.append(r)

    return {
        "sources_processed": len(work),
        "topics_generated": len(generated),
        "failed": len(failed),
        "generated": generated,
        "failures": failed,
        "dry_run": dry_run,
    }


# ============================================================================
# 孤立实体分类(三层):
#   1) LLM 把每个实体分配到本章节"已有主题"或标 __new__
#   2) 标 __new__ 的实体集合 → 走 _synthesize_one 合成新主题
#   3) LLM 调用失败 / 返回非法 → 用字面相似度把实体挂到最相近已有主题(绝不留"未归类"页)
# 目标:每个出现的实体都被归入一个语义合理的主题,零孤立、零垃圾桶页。
# ============================================================================

_NEW_TOPIC_SENTINEL = "__new__"
_CLASSIFY_TEMPLATE = PROMPTS_DIR / "classify_orphan_entities.md"


def _read_topic(wiki_root: Path, slug: str) -> tuple[dict, str] | None:
    p = safe_join(wiki_root, "wiki", "topics", f"{slug}.md")
    if not p.exists():
        return None
    try:
        return parse_text(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _topic_summary(fm: dict, body: str) -> str:
    """取主题的一句话概要:优先 frontmatter.brief,否则正文「## 主题概要」段首段。"""
    brief = fm.get("brief")
    if brief:
        return str(brief).strip()[:200]
    # 抓「## 主题概要」之后的第一段
    m = re.search(r"##\s*主题概要\s*\n+(.+?)(?:\n##|\Z)", body, re.DOTALL)
    if m:
        return m.group(1).strip()[:200]
    for para in body.split("\n\n"):
        p = para.strip()
        if p and not p.startswith("#"):
            return p[:200]
    return ""


def _build_topics_block(wiki_root: Path, topic_slugs: list[str]) -> tuple[str, list[str]]:
    """构造喂给 LLM 的"已有主题"清单文本,返回 (block, valid_topic_slugs)。"""
    lines: list[str] = []
    valid: list[str] = []
    for slug in topic_slugs:
        got = _read_topic(wiki_root, slug)
        if got is None:
            continue
        fm, body = got
        title = str(fm.get("title") or slug)
        summary = _topic_summary(fm, body)
        lines.append(f"- `{slug}`(标题:{title}):{summary or '(无概要)'}")
        valid.append(slug)
    return "\n".join(lines), valid


def _entity_text_for_match(wiki_root: Path, slug: str) -> str:
    """实体用于相似度匹配的文本:标题 + slug + 摘要。"""
    got = _read_entity(wiki_root, slug)
    if got is None:
        return slug
    fm, body = got
    title = str(fm.get("title") or slug)
    return f"{title} {slug} {_entity_summary(fm, body)}"


def _best_topic_by_similarity(
    wiki_root: Path, entity_slug: str, topic_slugs: list[str]
) -> str | None:
    """字面相似度兜底:把实体挂到与其文本重合度最高的已有主题。

    用字符 bigram Jaccard(中文友好,不依赖分词),取最高分主题。
    无候选主题时返回 None。
    """
    if not topic_slugs:
        return None
    ent_text = _entity_text_for_match(wiki_root, entity_slug)
    ent_grams = _char_bigrams(ent_text)
    best, best_score = None, -1.0
    for tslug in topic_slugs:
        got = _read_topic(wiki_root, tslug)
        if got is None:
            continue
        fm, body = got
        ttext = f"{fm.get('title') or tslug} {tslug} {_topic_summary(fm, body)}"
        score = _jaccard(ent_grams, _char_bigrams(ttext))
        if score > best_score:
            best, best_score = tslug, score
    # 即便分数为 0 也回最靠前的候选 —— 用户要求"必须归类",不留孤立
    return best if best is not None else topic_slugs[0]


def _char_bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s or "")
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _add_entities_to_topic(
    wiki_root: Path, topic_slug: str, entity_slugs: list[str]
) -> str | None:
    """把实体追加进已有主题的 related_entities(去重),正文「## 关键实体」补 [[实体]] 行。

    返回写入的相对路径;主题不存在返回 None。
    """
    got = _read_topic(wiki_root, topic_slug)
    if got is None:
        return None
    fm, body = got
    # 去重基准 = 正文「## 关键实体」段(主题成员的唯一权威口径,目录树/侧栏/图谱皆以此为准)。
    # 不能用 fm.related_entities 去重:LLM 生成主题时常把实体塞进 fm.related 却没落到
    # 正文段,若按 fm 去重会误判「已存在」直接跳过 → 实体正文缺行 → 永远卡在「无主题分组」。
    body_members = set(topic_member_slugs(body))
    added = [s for s in entity_slugs if s not in body_members]
    if not added:
        return None  # 正文段已全含,无需改写
    # fm.related_entities 同步取并集,保持 frontmatter 与正文一致
    related = [str(x) for x in (fm.get("related_entities") or []) if x]
    related_seen = set(related)
    for s in added:
        if s not in related_seen:
            related.append(s)
            related_seen.add(s)
    fm["related_entities"] = related
    fm["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # 正文「## 关键实体」段补链接(带实体简介,与 LLM 生成行同格式);无该段则在末尾追加一段
    def _bullet(s: str) -> str:
        got_e = _read_entity(wiki_root, s)
        summary = _entity_summary(*got_e) if got_e else ""
        return f"- [[{s}]]:{summary}\n" if summary else f"- [[{s}]]\n"

    new_bullets = "".join(_bullet(s) for s in added)
    if re.search(r"##\s*关键实体\s*\n", body):
        body = re.sub(
            r"(##\s*关键实体\s*\n)",
            lambda m: m.group(1) + new_bullets,
            body,
            count=1,
        )
    else:
        body = body.rstrip() + "\n\n## 关键实体\n\n" + new_bullets

    target = safe_join(wiki_root, "wiki", "topics", f"{topic_slug}.md")
    target.write_text(serialize(fm, body), encoding="utf-8")
    return target.relative_to(wiki_root).as_posix()


async def _llm_assign_to_existing(
    *,
    wiki_root: Path,
    source_slug: str,
    source_title: str,
    entity_slugs: list[str],
    topic_slugs: list[str],
) -> dict[str, str]:
    """LLM 把每个孤立实体分配到已有主题 slug 或 __new__。

    返回 {entity_slug: target}(target ∈ 已有 topic slug | __new__)。
    LLM 失败 / 输出非法 → 抛异常,由上层走相似度兜底。
    """
    from app.services.ingestion.step1_extract import (
        _extract_json_object,
        _loads_lenient,
    )

    ent_block, valid_ents, _domains = _build_entities_block(wiki_root, entity_slugs)
    topics_block, valid_topics = _build_topics_block(wiki_root, topic_slugs)
    if not valid_ents:
        return {}

    template = _CLASSIFY_TEMPLATE.read_text(encoding="utf-8")
    prompt = (
        template
        .replace("{source_slug}", source_slug)
        .replace("{source_title}", source_title)
        .replace("{topics_block}", topics_block or "(本章节当前没有任何已有主题)")
        .replace("{entities_block}", ent_block)
    )
    messages = [
        ChatMessage(role="system", content="你是工业工程领域知识工程师,擅长把零散概念精确归类到已有主题。严格按要求输出 JSON,不编造。"),
        ChatMessage(role="user", content=prompt),
    ]
    provider = get_llm_provider()
    raw = await provider.chat(messages, stream=False)
    obj = _loads_lenient(_extract_json_object(raw if isinstance(raw, str) else ""))
    if not isinstance(obj, dict):
        raise ValueError("classify LLM 输出非 JSON 对象")

    topic_set = set(valid_topics)
    ent_set = set(valid_ents)
    out: dict[str, str] = {}
    for ent in valid_ents:
        target = obj.get(ent)
        if isinstance(target, str) and target in topic_set:
            out[ent] = target
        else:
            # 缺失 / 非法目标 / 显式 __new__ → 归为新建
            out[ent] = _NEW_TOPIC_SENTINEL
    # 忽略 LLM 多吐的不存在实体键
    _ = ent_set
    return out


async def classify_orphan_entities(
    *,
    wiki_root: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """三层确定性分类:保证每个孤立实体都归入一个语义合理的主题,零孤立、零"未归类"页。

    每个 source:
      1. LLM 把实体分配到已有主题 slug 或 __new__;
      2. 归入已有主题的 → 追加进该 topic 的 related_entities + 正文链接;
      3. 标 __new__ 的 → 走 _synthesize_one 合成新主题;
      4. LLM 调用失败 / 合成失败的实体 → 用字面相似度挂到最相近已有主题(绝不留孤立)。
    """
    wiki_root = Path(wiki_root).resolve()
    work = collect_orphan_work(wiki_root)
    sem = asyncio.Semaphore(_read_llm_concurrency())

    written: list[str] = []
    assigned_existing = 0
    synthesized_new = 0
    fallback_matched = 0
    failures: list[dict] = []

    async def _process_source(w: dict) -> None:
        nonlocal assigned_existing, synthesized_new, fallback_matched
        source_slug = w["source_slug"]
        source_title = w["source_title"]
        orphan_slugs = list(w["orphan_slugs"])
        topic_slugs = list(w.get("existing_topic_slugs") or [])

        # ---- 第1层:LLM 分配 ----
        try:
            async with sem:
                assignment = await _llm_assign_to_existing(
                    wiki_root=wiki_root,
                    source_slug=source_slug,
                    source_title=source_title,
                    entity_slugs=orphan_slugs,
                    topic_slugs=topic_slugs,
                )
            llm_ok = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[classify] {source_slug}: LLM 分配失败,走相似度兜底: {e}")
            assignment = {}
            llm_ok = False

        if dry_run:
            return

        # ---- LLM 失败:全部走相似度兜底(归入最相近已有主题) ----
        if not llm_ok:
            for ent in orphan_slugs:
                best = _best_topic_by_similarity(wiki_root, ent, topic_slugs)
                if best:
                    rel = _add_entities_to_topic(wiki_root, best, [ent])
                    if rel:
                        written.append(rel)
                    fallback_matched += 1
                else:
                    failures.append({"source": source_slug, "entity": ent, "reason": "no_topic_to_match"})
            return

        # ---- 归入已有主题(按目标 topic 聚合,一次性追加) ----
        to_existing: dict[str, list[str]] = {}
        to_new: list[str] = []
        for ent in orphan_slugs:
            target = assignment.get(ent, _NEW_TOPIC_SENTINEL)
            if target == _NEW_TOPIC_SENTINEL:
                to_new.append(ent)
            else:
                to_existing.setdefault(target, []).append(ent)

        for tslug, ents in to_existing.items():
            rel = _add_entities_to_topic(wiki_root, tslug, ents)
            if rel:
                written.append(rel)
            assigned_existing += len(ents)

        # ---- 标 __new__ 的:合成新主题(分批) ----
        for batch in _chunk(to_new, _MAX_ENTITIES_PER_TOPIC):
            try:
                async with sem:
                    r = await _synthesize_one(
                        wiki_root=wiki_root,
                        source_slug=source_slug,
                        source_title=source_title,
                        entity_slugs=batch,
                        dry_run=False,
                    )
                if r.get("ok") and r.get("written"):
                    written.append(r["written"])
                    synthesized_new += r.get("related_count") or len(batch)
                else:
                    raise ValueError(r.get("error") or "synthesize failed")
            except Exception as e:  # noqa: BLE001
                # 合成失败 → 这批实体也不能悬空:相似度挂到最相近已有主题
                logger.warning(f"[classify] {source_slug}: 新主题合成失败,batch 走相似度兜底: {e}")
                for ent in batch:
                    best = _best_topic_by_similarity(wiki_root, ent, topic_slugs)
                    if best:
                        rel = _add_entities_to_topic(wiki_root, best, [ent])
                        if rel:
                            written.append(rel)
                        fallback_matched += 1
                    else:
                        failures.append({"source": source_slug, "entity": ent, "reason": "synth_failed_no_topic"})

    await asyncio.gather(*[_process_source(w) for w in work])

    return {
        "sources_processed": len(work),
        "assigned_to_existing": assigned_existing,
        "synthesized_new": synthesized_new,
        "fallback_matched": fallback_matched,
        "failures": failures,
        "written": sorted(set(written)),
        "dry_run": dry_run,
    }


# ============================================================================
# 全量「按 topic 覆盖」兜底分类(Task 4):
#   不按 source 遍历,而是直扫 wiki/entities/ 全集,凡未被任何 topic 的
#   related_entities 覆盖的实体 → 待分类。判定不依赖 sources,无论实体从哪冒
#   出来(void_fill / step2 / 手工)都能抓到。分类时给无 source 实体补 sources。
# ============================================================================


def _all_entity_slugs(wiki_root: Path) -> list[str]:
    p = safe_join(wiki_root, "wiki", "entities")
    return sorted(f.stem for f in p.glob("*.md") if f.is_file()) if p.exists() else []


def _all_topic_slugs(wiki_root: Path) -> list[str]:
    p = safe_join(wiki_root, "wiki", "topics")
    return sorted(f.stem for f in p.glob("*.md") if f.is_file()) if p.exists() else []


def _topic_covered_entity_slugs(wiki_root: Path, topic_slugs: list[str]) -> set[str]:
    """所有 topic 的 related_entities 并集 = 已被覆盖的实体集合。"""
    covered: set[str] = set()
    for tslug in topic_slugs:
        got = _read_topic(wiki_root, tslug)
        if got is None:
            continue
        fm, _ = got
        for e in fm.get("related_entities") or []:
            if e:
                covered.add(str(e))
    return covered


def _entity_sources(wiki_root: Path, slug: str) -> list[str]:
    got = _read_entity(wiki_root, slug)
    if got is None:
        return []
    fm, _ = got
    return [str(s).strip() for s in (fm.get("sources") or []) if str(s).strip()]


def _topic_sources(wiki_root: Path, slug: str) -> list[str]:
    got = _read_topic(wiki_root, slug)
    if got is None:
        return []
    fm, _ = got
    return [str(s).strip() for s in (fm.get("sources") or []) if str(s).strip()]


def _read_source_title(wiki_root: Path, slug: str) -> str:
    p = safe_join(wiki_root, "wiki", "sources", f"{slug}.md")
    if not p.exists():
        return slug
    try:
        fm, _ = parse_text(p.read_text(encoding="utf-8"))
        return str(fm.get("title") or slug)
    except Exception:  # noqa: BLE001
        return slug


def _supplement_entity_sources(wiki_root: Path, ent_slug: str, source_slugs: list[str]) -> None:
    """给实体补 sources(union 追加);已全含则不写。"""
    src = [s for s in source_slugs if s]
    if not src:
        return
    got = _read_entity(wiki_root, ent_slug)
    if got is None:
        return
    fm, body = got
    cur = [str(s).strip() for s in (fm.get("sources") or []) if str(s).strip()]
    merged = cur[:]
    for s in src:
        if s not in merged:
            merged.append(s)
    if merged == cur:
        return
    fm["sources"] = merged
    fm["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    target = safe_join(wiki_root, "wiki", "entities", f"{ent_slug}.md")
    target.write_text(serialize(fm, body), encoding="utf-8")


async def classify_all_unclassified_entities(
    *,
    wiki_root: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """全量兜底分类:扫 wiki/entities/ 全集,把未被任何 topic 覆盖的实体归入主题。

    与 classify_orphan_entities 的区别:**判定按 topic 覆盖、不按 source 遍历**,
    因此能抓到「无 source / source 归 未分类书」的实体(void_fill 漏网者)。

    流程(每个实体按其 sources[0] 锚定到一个章节的主题池):
      - 有 source 的:在该 source 的主题池里走三层(LLM 归入 → 合成新主题 → 相似度兜底);
        归类后给实体补该主题的 sources。
      - 无 source 的:在**全局主题池**里只做「归入已有 / 相似度兜底」(不合成新主题,
        因为没有 source 可锚定),归类后从命中主题继承 sources。全库零主题时记 failure。
    """
    wiki_root = Path(wiki_root).resolve()
    all_topics = _all_topic_slugs(wiki_root)
    covered = _topic_covered_entity_slugs(wiki_root, all_topics)
    unclassified = [e for e in _all_entity_slugs(wiki_root) if e not in covered]

    result: dict[str, Any] = {
        "unclassified_found": len(unclassified),
        "assigned_to_existing": 0,
        "synthesized_new": 0,
        "fallback_matched": 0,
        "failures": [],
        "written": [],
        "dry_run": dry_run,
    }
    if not unclassified or dry_run:
        return result

    # source slug → 该 source 的主题池(topic.sources 含此 source)
    src_to_topics: dict[str, list[str]] = {}
    for tslug in all_topics:
        for s in _topic_sources(wiki_root, tslug):
            src_to_topics.setdefault(s, []).append(tslug)

    # 按锚定 source(sources[0])分组;无 source 单独成桶
    groups: dict[str, list[str]] = {}
    no_source: list[str] = []
    for ent in unclassified:
        srcs = _entity_sources(wiki_root, ent)
        if srcs:
            groups.setdefault(srcs[0], []).append(ent)
        else:
            no_source.append(ent)

    sem = asyncio.Semaphore(_read_llm_concurrency())
    written: list[str] = []
    failures: list[dict] = []
    counters = {"existing": 0, "new": 0, "fallback": 0}

    async def _run_group(source_slug: str, ents: list[str], topic_slugs: list[str], allow_new: bool) -> None:
        source_title = _read_source_title(wiki_root, source_slug) if source_slug else source_slug
        # ---- 第1层:LLM 分配 ----
        try:
            async with sem:
                assignment = await _llm_assign_to_existing(
                    wiki_root=wiki_root,
                    source_slug=source_slug,
                    source_title=source_title,
                    entity_slugs=ents,
                    topic_slugs=topic_slugs,
                )
            llm_ok = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[classify_all] {source_slug or '(no-source)'}: LLM 失败,走相似度兜底: {e}")
            assignment = {}
            llm_ok = False

        to_existing: dict[str, list[str]] = {}
        to_new: list[str] = []
        if llm_ok:
            for ent in ents:
                tgt = assignment.get(ent, _NEW_TOPIC_SENTINEL)
                if tgt != _NEW_TOPIC_SENTINEL:
                    to_existing.setdefault(tgt, []).append(ent)
                elif allow_new:
                    to_new.append(ent)
                else:
                    # 无 source 锚定,不能合成新主题 → 相似度挂到最相近已有主题
                    best = _best_topic_by_similarity(wiki_root, ent, topic_slugs)
                    if best:
                        to_existing.setdefault(best, []).append(ent)
                    else:
                        failures.append({"source": source_slug, "entity": ent, "reason": "no_topic_to_match"})
        else:
            # LLM 失败:全走相似度兜底(归入最相近已有主题)
            for ent in ents:
                best = _best_topic_by_similarity(wiki_root, ent, topic_slugs)
                if best:
                    to_existing.setdefault(best, []).append(ent)
                else:
                    failures.append({"source": source_slug, "entity": ent, "reason": "no_topic_to_match"})

        # 归入已有主题 + 补 source
        for tslug, group_ents in to_existing.items():
            rel = _add_entities_to_topic(wiki_root, tslug, group_ents)
            if rel:
                written.append(rel)
            for ent in group_ents:
                _supplement_entity_sources(wiki_root, ent, _topic_sources(wiki_root, tslug))
            counters["existing"] += len(group_ents)

        # __new__:合成新主题(仅有 source 锚定时)
        for batch in _chunk(to_new, _MAX_ENTITIES_PER_TOPIC):
            try:
                async with sem:
                    r = await _synthesize_one(
                        wiki_root=wiki_root,
                        source_slug=source_slug,
                        source_title=source_title,
                        entity_slugs=batch,
                        dry_run=False,
                    )
                if r.get("ok") and r.get("written"):
                    written.append(r["written"])
                    counters["new"] += r.get("related_count") or len(batch)
                    for ent in batch:
                        _supplement_entity_sources(wiki_root, ent, [source_slug])
                else:
                    raise ValueError(r.get("error") or "synthesize failed")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[classify_all] {source_slug}: 新主题合成失败,batch 走相似度兜底: {e}")
                for ent in batch:
                    best = _best_topic_by_similarity(wiki_root, ent, topic_slugs)
                    if best:
                        rel = _add_entities_to_topic(wiki_root, best, [ent])
                        if rel:
                            written.append(rel)
                        _supplement_entity_sources(wiki_root, ent, _topic_sources(wiki_root, best))
                        counters["fallback"] += 1
                    else:
                        failures.append({"source": source_slug, "entity": ent, "reason": "synth_failed_no_topic"})

    tasks = [
        _run_group(src, ents, src_to_topics.get(src, []), allow_new=True)
        for src, ents in groups.items()
    ]
    if no_source:
        # 无 source 桶:全局主题池,不合成新主题(无锚定 source)
        tasks.append(_run_group("", no_source, all_topics, allow_new=False))

    await asyncio.gather(*tasks)

    result["assigned_to_existing"] = counters["existing"]
    result["synthesized_new"] = counters["new"]
    result["fallback_matched"] = counters["fallback"]
    result["failures"] = failures
    result["written"] = sorted(set(written))
    return result
