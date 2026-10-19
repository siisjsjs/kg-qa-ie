"""scrape 后的 LLM 整合:把原始 markdown 提炼成 IE 标准 md 文档。

流程:
  1. 渲染 prompt(注入原始 md / URL / 时间)
  2. 单次 LLM 调用产出整合后 md(含 frontmatter)
  3. 剥 ```markdown ... ``` 围栏(LLM 习惯包一层)
  4. 解析 frontmatter 拿到自起的 title;失败回落到原始标题
  5. 检测 __IRRELEVANT__ 判定为非 IE 内容,直接 raise

不写盘、不入库 —— 整合后的 md 直接返回,前端 Blob 下载。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.core.exceptions import ProviderError
from app.services.llm import ChatMessage, get_llm_provider
from app.services.wiki.frontmatter_io import parse_text

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
_TEMPLATE = (PROMPTS_DIR / "scrape_distill.md").read_text(encoding="utf-8")
_FENCE_RE = re.compile(r"```markdown\s*(.+?)\s*```", re.DOTALL)
_IRRELEVANT_RE = re.compile(r"^\s*__IRRELEVANT__\s*:?\s*(.*)$", re.MULTILINE)
# 单页原始 md 上限,避免 token 爆掉(主流 32k 上下文 + 8k 输出预算够这个长度)
_MAX_RAW_CHARS = 60_000

# 兜底:LLM 偶尔会留图片/空链接,在返回前再过一遍
_IMG_MD_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")  # ![alt](url)
_IMG_HTML_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_EMPTY_LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*\)")  # [text]() → text
_BLANK_LINK_RE = re.compile(r"\[\s*\]\([^)]*\)")  # [](url) → 空
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _post_clean(md: str) -> str:
    """二次清洗:删图片 / 空链接 / 多余空行。"""
    md = _IMG_MD_RE.sub("", md)
    md = _IMG_HTML_RE.sub("", md)
    md = _EMPTY_LINK_RE.sub(r"\1", md)
    md = _BLANK_LINK_RE.sub("", md)
    md = _MULTI_BLANK_RE.sub("\n\n", md)
    return md.strip() + "\n"


class IrrelevantContentError(ProviderError):
    """LLM 判定原文与工业工程无关。"""

    status_code = 422
    code = "SCRAPE_IRRELEVANT"


def _render_prompt(*, raw_markdown: str, source_url: str, raw_title: str, scraped_at: datetime) -> str:
    truncated = raw_markdown
    if len(truncated) > _MAX_RAW_CHARS:
        truncated = truncated[:_MAX_RAW_CHARS] + "\n\n...(原文过长,已截断)"
    return (
        _TEMPLATE
        .replace("{source_url}", source_url)
        .replace("{raw_title}", raw_title or "(无)")
        .replace("{scraped_at}", scraped_at.isoformat() if isinstance(scraped_at, datetime) else str(scraped_at))
        .replace("{raw_markdown}", truncated)
    )


def _strip_fence(raw: str) -> str:
    m = _FENCE_RE.search(raw)
    if m:
        return m.group(1).strip()
    return raw.strip()


async def distill_scraped(
    *,
    raw_markdown: str,
    source_url: str,
    raw_title: str,
    scraped_at: datetime,
) -> tuple[str, str]:
    """LLM 整合 → 返回 (final_markdown, final_title)。

    Raises:
        IrrelevantContentError:LLM 判为非 IE 内容
        ProviderError:LLM 调用失败 / 输出空
    """
    prompt = _render_prompt(
        raw_markdown=raw_markdown,
        source_url=source_url,
        raw_title=raw_title,
        scraped_at=scraped_at,
    )

    provider = get_llm_provider()
    messages = [
        ChatMessage(
            role="system",
            content="你是工业工程领域的资料整理员,严格按用户提供的格式输出,不要写多余解释。",
        ),
        ChatMessage(role="user", content=prompt),
    ]
    raw_resp = await provider.chat(messages, stream=False)
    raw = raw_resp if isinstance(raw_resp, str) else ""
    if not raw.strip():
        raise ProviderError("LLM 返回空内容,无法整合")

    body = _strip_fence(raw)

    # 非 IE 内容分支
    irrelevant = _IRRELEVANT_RE.search(body[:500])
    if irrelevant and not body.lstrip().startswith("---"):
        reason = irrelevant.group(1).strip() or "无说明"
        raise IrrelevantContentError(f"原文与工业工程无关:{reason}")

    # 二次清洗:LLM 偶尔会留下图片 / 空链接
    body = _post_clean(body)

    # 解析 frontmatter 拿 title
    final_title = raw_title or source_url
    try:
        fm, _md_body = parse_text(body)
        if fm.get("title"):
            final_title = str(fm["title"])[:200]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"distill: frontmatter parse failed (non-blocking): {e}")

    return body, final_title


__all__ = ["distill_scraped", "IrrelevantContentError"]
