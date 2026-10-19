"""ingestion/md_parser.py:解析上传 md 的 frontmatter + body。

按规划文档 §0.3 — md 等同于一份 source,**不切章节**;此处仅做最轻量的拆解:
  - 抽出 frontmatter(可选)
  - 把 body 按"空行"分段,得到 paragraphs(给上层 LLM prompt 做长度提示)
  - 计算字符数 / 行数

不规范的 frontmatter(YAML 解析失败)降级为空 dict + body 保持完整文本,不抛异常。
"""

from __future__ import annotations

from app.services.wiki.frontmatter_io import parse_text


def parse(content: str) -> dict:
    """解析 md 文本,返回 {frontmatter, body, paragraphs, length, line_count}。"""
    try:
        fm, body = parse_text(content)
    except Exception:
        fm, body = {}, content

    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    return {
        "frontmatter": fm,
        "body": body,
        "paragraphs": paragraphs,
        "length": len(body),
        "line_count": body.count("\n") + (0 if body.endswith("\n") else 1) if body else 0,
    }


def excerpt(body: str, *, max_chars: int = 500) -> str:
    """从 body 中抽出"前 max_chars 个字符"作为 source 摘要原文节选。"""
    body = body.strip()
    if len(body) <= max_chars:
        return body
    cut = body[:max_chars]
    # 尽量在标点边界切
    for sep in ("\n\n", "。", ".", "\n"):
        idx = cut.rfind(sep)
        if idx > max_chars * 0.6:
            cut = cut[: idx + 1]
            break
    return cut.rstrip() + "…"
