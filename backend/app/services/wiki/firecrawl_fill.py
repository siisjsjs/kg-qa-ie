"""用 firecrawl 联网抓料 + LLM 综合,为虚空连接(void link)生成高质量实体页。

与 void_link_cleaner.fill_void_entity 的区别:
  - 那个只用"本 wiki 已引用片段"作证据,资料不足时大段写"待补充"。
  - 本模块额外**联网检索**(firecrawl search → scrape),拿真实网络资料充实内容,
    再让 LLM 把"网络资料 + 本知识库语境"综合成实体页 → 内容更完整。

流程(每个 slug):
  1. _gather_ref_context:收集本 wiki 里引用 [[slug]] 的片段(确定该实体在教材里的语境)
  2. firecrawl.search_web:搜候选 URL
  3. firecrawl.scrape_url:抓前 N 篇正文,拼成 web_material
  4. LLM(firecrawl_fill_entity.md):综合成实体页 markdown
  5. 解析 + 注入 auto_filled/confidence/kind → 写 wiki/entities/{slug}.md

噪声过滤(collect_fillable_targets):
  - 跳过日期前缀的 source 式 slug(如 2020-05-25-capacity-planning)
  - 跳过含 HTML 注释残渣的 slug(如 标准件<!-- type: 实体 -->)
  - 全角/半角括号、空格变体归一去重(保留 ref_count 最高的)
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.core.exceptions import ProviderError
from app.services.lint.void_link_cleaner import _gather_ref_context, scan_void_links
from app.services.llm import ChatMessage, get_llm_provider
from app.services.scraper.firecrawl import scrape_url, search_web
from app.services.wiki.frontmatter_io import parse_text, serialize
from app.services.wiki.fs_io import atomic_write, safe_join

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
_TEMPLATE = PROMPTS_DIR / "firecrawl_fill_entity.md"

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_HTML_JUNK = re.compile(r"<!--")
_BRACKET_RE = re.compile(r"[（(][^）)]*[)）]")

_SCRAPE_TOP_N = 3            # 每个 slug 抓前 N 篇搜索结果
_MAX_WEB_CHARS = 24_000      # 拼给 LLM 的网络资料上限
_DEFAULT_LLM_CONCURRENCY = 5  # 联网任务比纯 LLM 重,默认并发小一点


def _read_llm_concurrency() -> int:
    try:
        from app.services.admin import config_store

        v = config_store.get_sync("ingestion.llm_concurrency", default="")
        if v:
            return max(1, min(int(v), 20))
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_LLM_CONCURRENCY


def _norm(s: str) -> str:
    """全/半角括号统一 + 去空格 + lower,用于去重等价 slug。"""
    s = _BRACKET_RE.sub(lambda m: m.group(0).translate(str.maketrans("（）", "()")), s)
    return s.replace(" ", "").replace("　", "").lower()


def collect_fillable_targets(
    *, wiki_root: Path, min_ref_count: int = 1
) -> list[dict[str, Any]]:
    """扫 void link,过滤噪声,返回值得联网填充的 fillable 目标(按 ref_count 降序)。

    Args:
        min_ref_count: 只保留引用次数 >= 此值的目标(>=2 可只填"被多处引用"的高价值实体)
    """
    wiki_root = Path(wiki_root).resolve()
    scan = scan_void_links(wiki_root=wiki_root)
    fillable = scan.get("fillable") or []

    groups: dict[str, dict[str, Any]] = {}
    for f in fillable:
        tgt = f["broken_target"]
        if _DATE_PREFIX.match(tgt) or _HTML_JUNK.search(tgt):
            continue
        if int(f.get("ref_count") or 0) < min_ref_count:
            continue
        key = _norm(tgt)
        if not key:
            continue
        # 同一归一化键只保留 ref_count 最高的那个原始 slug
        cur = groups.get(key)
        if cur is None or f["ref_count"] > cur["ref_count"]:
            groups[key] = f

    out = sorted(groups.values(), key=lambda x: x["ref_count"], reverse=True)
    return out


def _strip_fence(md: str) -> str:
    md = md.strip()
    md = re.sub(r"^```(?:markdown|md)?\s*\n", "", md)
    md = re.sub(r"\n```\s*$", "", md)
    return md.strip()


def _slug_to_filename(slug: str) -> str:
    return (slug.replace("/", "_").replace("\\", "_").strip()) or "unnamed"


async def _fetch_web_material(slug: str, *, extra_terms: str = "工业工程") -> tuple[str, list[str]]:
    """搜 + 抓:返回 (拼好的网络资料文本, 实际成功抓取的 URL 列表)。"""
    query = f"{slug} {extra_terms}".strip()
    try:
        hits = await search_web(query, limit=_SCRAPE_TOP_N + 2, lang="zh")
    except ProviderError as e:
        logger.warning(f"[firecrawl_fill] search failed for {slug!r}: {e}")
        return "", []

    blocks: list[str] = []
    used_urls: list[str] = []
    total = 0
    for h in hits:
        if len(used_urls) >= _SCRAPE_TOP_N:
            break
        url = h["url"]
        try:
            scraped = await scrape_url(url)
        except ProviderError as e:
            logger.info(f"[firecrawl_fill] scrape skip {url}: {e}")
            continue
        md = (scraped.get("markdown") or "").strip()
        if not md:
            continue
        # 单篇截断,避免被一篇长文吃满预算
        snippet = md[:8000]
        block = f"### 资料来源:{scraped.get('source_url') or url}\n标题:{scraped.get('title') or ''}\n\n{snippet}"
        if total + len(block) > _MAX_WEB_CHARS:
            break
        blocks.append(block)
        used_urls.append(scraped.get("source_url") or url)
        total += len(block)

    return ("\n\n---\n\n".join(blocks), used_urls)


async def fill_one_entity(
    *, wiki_root: Path, slug: str, dry_run: bool = False
) -> dict[str, Any]:
    """联网填充单个 void slug → 实体页。dry_run 只返回预览不落盘。"""
    wiki_root = Path(wiki_root).resolve()
    target = safe_join(wiki_root, "wiki", "entities", f"{_slug_to_filename(slug)}.md")
    if target.exists():
        return {"ok": False, "slug": slug, "error": "already exists"}

    ref_context = _gather_ref_context(wiki_root, slug)
    web_material, used_urls = await _fetch_web_material(slug)
    if not web_material:
        return {"ok": False, "slug": slug, "error": "no_web_material: 搜不到/抓不到可用网络资料"}

    template = _TEMPLATE.read_text(encoding="utf-8")
    today = date.today().isoformat()
    prompt = (
        template
        .replace("{slug}", slug)
        .replace("{title}", slug)
        .replace("{ref_context}", ref_context)
        .replace("{web_material}", web_material)
    )
    messages = [
        ChatMessage(role="system", content="你是工业工程领域的知识工程师,擅长把网络资料综合成结构化实体页。严格按要求输出,不编造。"),
        ChatMessage(role="user", content=prompt),
    ]
    provider = get_llm_provider()
    raw = await provider.chat(messages, stream=False)
    md = _strip_fence(raw if isinstance(raw, str) else "")
    if not md or "---" not in md:
        return {"ok": False, "slug": slug, "error": "LLM 输出无 frontmatter"}

    try:
        fm, body = parse_text(md)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "slug": slug, "error": f"frontmatter 解析失败: {e}"}

    fm["slug"] = slug
    fm.setdefault("title", slug)
    fm["kind"] = "entity"
    fm.setdefault("version", 1)
    fm.setdefault("created", today)
    fm["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    fm["auto_filled"] = True
    fm["confidence"] = "medium"  # 有真实网络资料,比纯上下文版(low)可信
    fm["fill_source"] = "firecrawl"
    if used_urls:
        fm["source_urls"] = used_urls

    result = {
        "ok": True,
        "slug": slug,
        "title": str(fm.get("title") or slug),
        "urls": used_urls,
        "body_chars": len(body),
    }
    if dry_run:
        result["preview"] = serialize(fm, body)[:1500]
        return result

    target.parent.mkdir(parents=True, exist_ok=True)
    await atomic_write(target, serialize(fm, body))
    rel = target.relative_to(wiki_root).as_posix()
    result["written"] = rel
    logger.info(f"[firecrawl_fill] wrote {rel} ({len(body)} chars, {len(used_urls)} urls)")
    return result


async def fill_void_entities_via_web(
    *,
    wiki_root: Path,
    dry_run: bool = False,
    min_ref_count: int = 1,
    limit: int | None = None,
    only_slug: str | None = None,
) -> dict[str, Any]:
    """批量联网填充 fillable void link。

    Args:
        min_ref_count: 只填引用次数 >= 此值的目标(>=2 聚焦高价值)
        limit: 只处理前 N 个(按 ref_count 降序)
        only_slug: 只处理指定 slug(单点测试)
    """
    wiki_root = Path(wiki_root).resolve()
    targets = collect_fillable_targets(wiki_root=wiki_root, min_ref_count=min_ref_count)
    if only_slug:
        targets = [t for t in targets if t["broken_target"] == only_slug]
    if limit is not None:
        targets = targets[:limit]

    sem = asyncio.Semaphore(_read_llm_concurrency())

    async def _bounded(slug: str) -> dict[str, Any]:
        async with sem:
            try:
                return await fill_one_entity(wiki_root=wiki_root, slug=slug, dry_run=dry_run)
            except Exception as e:  # noqa: BLE001
                logger.exception(f"[firecrawl_fill] exception on {slug}")
                return {"ok": False, "slug": slug, "error": str(e)}

    results = await asyncio.gather(*[_bounded(t["broken_target"]) for t in targets])
    generated = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]

    return {
        "targets": len(targets),
        "filled": len(generated),
        "failed": len(failed),
        "generated": generated,
        "failures": failed,
        "dry_run": dry_run,
    }
