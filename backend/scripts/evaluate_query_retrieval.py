"""Evaluate retrieval and section selection without calling the answer LLM."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.services.ingestion.merger import load_alias_groups
from app.services.query.alias_expand import expand
from app.services.query.hybrid import hybrid_search
from app.services.query.page_reader import read_for_context


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def evaluate(dataset: Path, top_k: int = 5) -> dict:
    cases = _load(dataset)
    wiki_root = settings.wiki_root_path
    aliases = load_alias_groups(wiki_root)
    hits = 0
    reciprocal_rank = 0.0
    section_hits = 0
    details = []
    for case in cases:
        keywords, _ = expand(case["question"], alias_groups=aliases)
        candidates = await hybrid_search(
            question=case["question"], keywords=keywords, wiki_root=wiki_root, top_k=top_k
        )
        slugs = [item["slug"] for item in candidates]
        expected = set(case.get("expected_sources") or [])
        ranks = [slugs.index(slug) + 1 for slug in expected if slug in slugs]
        if ranks:
            hits += 1
            reciprocal_rank += 1 / min(ranks)

        excerpts = []
        for item in candidates[:3]:
            page = await read_for_context(
                slug=item["slug"],
                hit_lines=item.get("hit_lines") or [],
                wiki_root=wiki_root,
                hint_kind=item.get("kind"),
                question=case["question"],
                keywords=keywords,
            )
            if page:
                excerpts.append(page["body_excerpt"])
        required = case.get("expected_section_keywords") or []
        section_ok = not required or any(term in "\n".join(excerpts) for term in required)
        section_hits += int(section_ok)
        details.append({"id": case["id"], "retrieved": slugs, "source_hit": bool(ranks), "section_hit": section_ok})
    total = max(1, len(cases))
    return {
        "cases": len(cases),
        f"recall@{top_k}": round(hits / total, 4),
        "mrr": round(reciprocal_rank / total, 4),
        "section_hit_rate": round(section_hits / total, 4),
        "details": details,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("tests/eval/ie_qa_benchmark.jsonl"))
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(evaluate(args.dataset, args.top_k)), ensure_ascii=False, indent=2))
