"""ingestion/source_writer.py:写 wiki/sources/{date}-{slug}.md。

对应 §2.3.1 文件系统结构 + §3.1 流水线 step1 上传与登记。
源摘要页是 LLM 处理产物的"门面",含原始文件副本路径、抽取实体/主题列表、内容 hash。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.services.wiki.frontmatter_io import serialize
from app.services.wiki.fs_io import atomic_write, safe_join, slugify


def make_source_slug(*, title: str, date: str | None = None) -> str:
    """生成 source 页 slug:`{YYYY-MM-DD}-{slugified-title}`。"""
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    return f"{date_str}-{slugify(title)}"


def render_source_md(
    *,
    title: str,
    slug: str,
    summary: str,
    raw_excerpt: str,
    source_type: str,
    raw_path: str,
    original_filename: str,
    content_hash: str,
    tags: list[str] | None = None,
    domains: list[str] | None = None,
    extracted_entities: list[str] | None = None,
    extracted_topics: list[str] | None = None,
    now_iso: str | None = None,
) -> str:
    """组装 source 摘要页完整 md(含 frontmatter)。

    frontmatter 字段顺序由 frontmatter_io.serialize 统一处理。
    """
    now = now_iso or datetime.now(timezone.utc).isoformat()
    fm = {
        "title": title,
        "slug": slug,
        "kind": "source",
        "source_type": source_type,
        "tags": list(tags or []),
        "domains": list(domains or []),
        "extracted_entities": list(extracted_entities or []),
        "extracted_topics": list(extracted_topics or []),
        "content_hash": content_hash,
        "ingested_at": now,
        "version": 1,
    }

    entity_lines = (
        "\n".join(f"- [[{e}]]" for e in (extracted_entities or [])) or "_暂无_"
    )
    topic_lines = (
        "\n".join(f"- [[{t}]]" for t in (extracted_topics or [])) or "_暂无_"
    )

    body = (
        "## 来源元数据\n\n"
        f"- **类型**:{source_type}\n"
        f"- **原始文件**:{original_filename}\n"
        f"- **素材路径**:{raw_path}\n"
        f"- **内容 hash**:`{content_hash[:12]}…`\n\n"
        "## 摘要\n\n"
        f"{summary or '_LLM 摘要待补充_'}\n\n"
        "## 抽取的实体\n\n"
        f"{entity_lines}\n\n"
        "## 抽取的主题\n\n"
        f"{topic_lines}\n\n"
        "## 原文(节选)\n\n"
        f"> {raw_excerpt}\n"
    )
    return serialize(fm, body)


async def write_source_page(
    *,
    wiki_root: Path,
    title: str,
    summary: str,
    raw_excerpt: str,
    source_type: str,
    raw_path: str,
    original_filename: str,
    content_hash: str,
    tags: list[str] | None = None,
    domains: list[str] | None = None,
    extracted_entities: list[str] | None = None,
    extracted_topics: list[str] | None = None,
    date: str | None = None,
) -> str:
    """落 wiki/sources/{date}-{slug}.md,返回 slug(不含目录前缀,不含扩展名)。

    若同 slug 文件已存在,采取 -2 / -3 后缀避让;sources 是按上传时间一份一份累积,
    不做 merge,所以同名应少见(date 戳已包含日期)。
    """
    slug = make_source_slug(title=title, date=date)
    dest = safe_join(Path(wiki_root), "wiki", "sources", f"{slug}.md")

    # 简单避让:若已存在,递增后缀
    counter = 2
    while dest.exists():
        slug_dup = f"{slug}-{counter}"
        dest = safe_join(Path(wiki_root), "wiki", "sources", f"{slug_dup}.md")
        counter += 1

    final_slug = dest.stem  # 含 -2 后缀的最终 slug
    md_text = render_source_md(
        title=title,
        slug=final_slug,
        summary=summary,
        raw_excerpt=raw_excerpt,
        source_type=source_type,
        raw_path=raw_path,
        original_filename=original_filename,
        content_hash=content_hash,
        tags=tags,
        domains=domains,
        extracted_entities=extracted_entities,
        extracted_topics=extracted_topics,
    )
    await atomic_write(dest, md_text)
    return final_slug
