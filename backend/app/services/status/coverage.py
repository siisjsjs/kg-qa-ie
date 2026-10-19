"""status/coverage.py:来源信号覆盖统计。

- source_type_distribution:每种 raw/<source_type>/ 下的素材数
- entity_source_distribution:实体页 frontmatter.sources 中各 source 出现次数
- single_source_orphan_rate:实体页 frontmatter.sources 长度 == 1 的比例
- cross_source_fusion_rate:实体页含"## 不同素材中的观点"段的比例
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from app.services.wiki.frontmatter_io import parse_text


async def coverage(*, wiki_root: Path) -> dict:
    wiki_root = Path(wiki_root).resolve()

    # source_type_distribution(raw/ 下)
    raw_root = wiki_root / "raw"
    source_type_counts: dict[str, int] = {}
    if raw_root.exists():
        for sub in raw_root.iterdir():
            if sub.is_dir():
                source_type_counts[sub.name] = sum(
                    1 for p in sub.rglob("*.md") if p.is_file()
                )

    entities_dir = wiki_root / "wiki" / "entities"
    entity_count = 0
    cross_source_count = 0
    single_source_count = 0
    entity_source_counter: Counter[str] = Counter()

    if entities_dir.exists():
        for p in entities_dir.glob("*.md"):
            try:
                fm, body = parse_text(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            entity_count += 1
            sources = list(fm.get("sources") or [])
            for s in sources:
                entity_source_counter[str(s)] += 1
            if len(sources) <= 1:
                single_source_count += 1
            if "## 不同素材中的观点" in body:
                cross_source_count += 1

    return {
        "source_type_distribution": source_type_counts,
        "entity_source_distribution": dict(entity_source_counter.most_common(50)),
        "entity_count": entity_count,
        "single_source_orphan_rate": round(
            (single_source_count / entity_count) if entity_count else 0.0, 4
        ),
        "cross_source_fusion_rate": round(
            (cross_source_count / entity_count) if entity_count else 0.0, 4
        ),
    }
