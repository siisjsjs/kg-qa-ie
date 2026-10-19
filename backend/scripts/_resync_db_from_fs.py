"""一次性:从文件系统(真相源)全量重建 wiki_pages / wiki_links。

背景:delete_source 的文件侧已完成(删 source + 29 孤儿页、改写 19 老页),
但其 DB 同步在 db.flush() 撞到一条历史遗留的大小写不敏感重复 wiki_links 行
(5W1H提问技术-工作研究-related)而回滚。导致 FS 正确、DB 陈旧。

delete.py 的 link 重抽是裸 db.add(无 SAVEPOINT),而 _sync_db_indices 用
begin_nested 包每条 INSERT,撞 UNIQUE 自动跳过。故这里清空两表后用
_sync_db_indices 重灌,既修复本次陈旧,也顺带清掉历史重复残渣。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.core.db import session_scope
from app.models.wiki_page import WikiPage
from app.models.wiki_link import WikiLink
from app.services.wiki.link_resolver import iter_wiki_pages
from app.services.ingestion.pipeline import _sync_db_indices


def main() -> None:
    wiki_root = settings.wiki_root_path
    # 只同步六类内容目录;_growth / _bak* 等系统/备份目录的 md 不进 wiki_pages
    # (其 kind 不在枚举内会 DataError),也不该出现在索引里。
    content_dirs = {"entities", "topics", "sources", "queries", "synthesis", "comparisons"}
    rels = []
    for p in iter_wiki_pages(wiki_root):
        rel = str(p.relative_to(wiki_root)).replace("\\", "/")
        parts = rel.split("/")
        if len(parts) >= 2 and parts[0] == "wiki" and parts[1] in content_dirs:
            rels.append(rel)
    print(f"FS 内容页总数: {len(rels)}")

    with session_scope() as db:
        n_pg = db.query(WikiPage).delete(synchronize_session=False)
        n_lk = db.query(WikiLink).delete(synchronize_session=False)
        db.flush()
        print(f"清空 wiki_pages={n_pg} wiki_links={n_lk}")
        stat = _sync_db_indices(db, wiki_root=wiki_root, written_paths=rels)
        db.commit()
        print(f"resync: {stat}")


if __name__ == "__main__":
    main()
