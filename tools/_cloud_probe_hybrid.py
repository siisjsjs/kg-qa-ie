"""One hybrid_search on cloud; print semantic vs lexical."""
import asyncio
import json
from pathlib import Path

from app.core.config import settings
from app.services.query.hybrid import hybrid_search, last_retrieval_stats


async def main() -> None:
    questions = [
        "帕累托图是什么",
        "用柱状图找少数关键原因的方法叫什么",
    ]
    for q in questions:
        chunks = await hybrid_search(
            question=q,
            keywords=[q],
            wiki_root=Path(settings.wiki_root),
            top_k=8,
            candidate_k=16,
        )
        stats = last_retrieval_stats()
        print("===", q, "===")
        print("stats", json.dumps(stats, ensure_ascii=False))
        for i, c in enumerate(chunks[:8], 1):
            print(
                f"{i}. {c.get('slug')} src={c.get('retrieval_sources')} "
                f"lex={c.get('lexical_score')} sem={c.get('semantic_score')} "
                f"score={c.get('score')}"
            )
        print()


if __name__ == "__main__":
    asyncio.run(main())
