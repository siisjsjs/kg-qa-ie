"""一次性:对现有 wiki 历史数据跑 frontmatter 回写 + wiki_links 重建。

复用 pipeline._resync_frontmatter_from_links(以 wiki_links 为权威基准),
把 topic.related_entities 与 source 抽取统计刷新到与图谱一致。
先重建 wiki_links 索引确保基准最新,再回写。

用法: py -X utf8 scripts/resync_frontmatter_now.py
"""
from __future__ import annotations

from app.core.config import settings
from app.services.ingestion.pipeline import _resync_frontmatter_from_links
from app.services.wiki.link_resolver import rebuild_links_index, aggregate_edges


def main() -> None:
    wiki_root = settings.wiki_root_path
    print(f"wiki_root = {wiki_root}")
    # 1. 重建 wiki_links 索引(权威基准)
    edges = aggregate_edges(rebuild_links_index(wiki_root))
    print(f"wiki_links 重建: {len(edges)} 条边")
    # 2. 回写 frontmatter
    stat = _resync_frontmatter_from_links(wiki_root=wiki_root)
    print(f"回写完成: topics_resynced={stat['topics_resynced']} sources_resynced={stat['sources_resynced']}")


if __name__ == "__main__":
    main()
