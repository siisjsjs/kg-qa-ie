"""ingestion/triple_filter.py:三元组兜底清洗。

Step1/Step2 prompt 已强约束,本层只过滤明显错误:
  - 实体名长度 < 2 或全是停用词
  - 实体名 == 实体 brief(LLM 偷懒)
  - 关系 src/dst 完全相同
  - 关系 rel_type 不在白名单(白名单为空时不过滤)

设计目的:让流水线对偶发的低质量 LLM 输出更鲁棒,而不是替代 Step1 的语义校验。
"""

from __future__ import annotations

# 中文常见停用词(简短,只覆盖会被误抽成实体的)
_STOPWORDS = frozenset({
    "的", "了", "是", "和", "或", "在", "也", "都", "等", "及", "与",
    "the", "a", "an", "of", "and", "or", "is", "are", "to", "in", "for",
})


def filter_entities(entities: list[dict], *, min_length: int = 2) -> list[dict]:
    """过滤明显低质量的实体候选。

    规则:
      - name 长度 < min_length(默认 2)
      - name 是停用词
      - name == brief(LLM 偷懒)
      - 同名重复(只保留第一条)
    """
    seen: set[str] = set()
    out: list[dict] = []
    for e in entities:
        name = (e.get("name") or "").strip()
        if not name or len(name) < min_length:
            continue
        if name.lower() in _STOPWORDS:
            continue
        brief = (e.get("brief") or "").strip()
        if brief and name == brief:
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append(e)
    return out


def filter_relations(
    connections: list[dict],
    *,
    allowed_types: list[str] | None = None,
) -> list[dict]:
    """过滤明显错误的关系。

    规则:
      - src == dst(自环)
      - 缺 src 或 dst
      - rel_type 不在 allowed_types(白名单为 None / 空 list 时不过滤)
      - 完全重复(src,dst,rel_type 三元组)
    """
    allowed = set(allowed_types) if allowed_types else None
    seen: set[tuple[str, str, str]] = set()
    out: list[dict] = []
    for c in connections:
        src = (c.get("src") or "").strip()
        dst = (c.get("dst") or "").strip()
        rel = (c.get("rel_type") or "").strip()
        if not src or not dst or src == dst:
            continue
        if allowed and rel and rel not in allowed:
            continue
        key = (src, dst, rel)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
