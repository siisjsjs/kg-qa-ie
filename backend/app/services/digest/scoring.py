"""digest/scoring.py:digest 评分 + 缺口入队。

- deep_dive:5 个 H2 缺几个 → sections_missing;sections_missing > 1 → gap_flag
- comparison:对比表 fill_rate < 0.7 → gap_flag;空格逐格入 gap-queue
- timeline:events < 3 → sparse=true;events_per_decade < 2 → 标 sparse(不强制 gap)
"""

from __future__ import annotations

from app.services.digest.parsing import is_blank_cell

DEEP_DIVE_REQUIRED_SECTIONS = [
    "背景概述",
    "核心观点",
    "不同视角对比",
    "知识脉络",
    "尚待解决的问题",
]


def score_deep_dive(*, sections: list[dict]) -> dict:
    """5 个 H2 缺几个 → sections_missing;缺则 gap_flag=True。"""
    headings = [s.get("heading", "") for s in sections]
    missing: list[str] = []
    for required in DEEP_DIVE_REQUIRED_SECTIONS:
        if not any(required in h for h in headings):
            missing.append(required)

    # 检查内容是否实质为空(只有"待补充"等占位符)
    placeholder_sections: list[str] = []
    for s in sections:
        h = s.get("heading", "")
        for required in DEEP_DIVE_REQUIRED_SECTIONS:
            if required in h:
                content = (s.get("content") or "").strip()
                if not content or content.startswith("> 待补充") or len(content) < 20:
                    placeholder_sections.append(required)
                break

    fill_count = len(DEEP_DIVE_REQUIRED_SECTIONS) - len(missing) - len(placeholder_sections)
    fill_rate = fill_count / len(DEEP_DIVE_REQUIRED_SECTIONS)
    gap_flag = (len(missing) + len(placeholder_sections)) > 1

    return {
        "sections_missing": missing,
        "placeholder_sections": placeholder_sections,
        "fill_rate": round(fill_rate, 4),
        "gap_flag": gap_flag,
    }


def score_comparison(
    *,
    table: list[list[str]],
    dimensions: list[str],
    objects: list[str],
) -> dict:
    """fill_rate = 非空格数 / 总格数;< 0.7 → gap_flag。

    返回 empty_cells 列表 [(dimension, object), ...] 供逐格入队。
    """
    if not table or not objects:
        return {
            "fill_rate": 0.0,
            "total_cells": 0,
            "filled_cells": 0,
            "empty_cells": [],
            "gap_flag": True,
            "sections_missing": ["对比表"],
        }

    empty_cells: list[tuple[str, str]] = []
    total = 0
    filled = 0
    for row in table:
        if not row:
            continue
        dim = (row[0] if row else "").strip()
        cells = row[1:] if len(row) > 1 else []
        for j, cell in enumerate(cells):
            if j >= len(objects):
                break
            obj = objects[j].strip()
            total += 1
            if is_blank_cell(cell):
                empty_cells.append((dim, obj))
            else:
                filled += 1

    fill_rate = filled / total if total else 0.0
    gap_flag = fill_rate < 0.7

    return {
        "fill_rate": round(fill_rate, 4),
        "total_cells": total,
        "filled_cells": filled,
        "empty_cells": empty_cells,
        "gap_flag": gap_flag,
        "sections_missing": [],
    }


def score_timeline(*, events: list[dict]) -> dict:
    """events < 3 → sparse=True + gap_flag=True;计算 events_per_decade。"""
    n = len(events)
    sparse = n < 3
    gap_flag = sparse

    # events_per_decade
    epd = 0.0
    if events:
        years: list[int] = []
        for e in events:
            ds = (e.get("date") or "").split("-")[0]
            try:
                years.append(int(ds))
            except (ValueError, TypeError):
                continue
        if len(years) >= 2:
            span = max(years) - min(years)
            decades = max(span / 10.0, 1.0)
            epd = round(n / decades, 2)
        elif years:
            epd = round(n, 2)

    return {
        "events_count": n,
        "sparse": sparse,
        "events_per_decade": epd,
        "gap_flag": gap_flag,
        "sections_missing": [] if not sparse else ["事件说明"],
    }
