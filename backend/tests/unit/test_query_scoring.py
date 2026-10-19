"""query/scoring.py 单元测试 — P4 answer-quality。"""

from __future__ import annotations

import pytest

from app.services.query.scoring import (
    collect_citations_from_answer,
    confidence_mix,
    evaluate_for_query,
    score_answer,
)


def test_score_answer_perfect():
    out = score_answer(
        answer="本节涉及 [[A]] [[B]] [[C]] [[D]] [[E]]",
        citations=[
            {"slug": s, "confidence": "EXTRACTED"} for s in ["A", "B", "C", "D", "E"]
        ],
        alias_miss_count=0,
        keyword_count=5,
        broken_links=0,
    )
    assert out["tier"] == "gold"
    assert out["score"] >= 0.8
    assert out["gap_flag"] is False
    assert out["cited_sources"] == 5


def test_score_answer_below_threshold_triggers_gap():
    out = score_answer(
        answer="也许是 [[A]] 吧",
        citations=[{"slug": "A", "confidence": "AMBIGUOUS"}],
        alias_miss_count=3,
        keyword_count=4,
        broken_links=0,
    )
    assert out["tier"] == "bronze"
    assert out["gap_flag"] is True
    assert out["cited_sources"] == 1
    # 三个 reasons:cited<3 + alias_miss_ratio>.3 + score<0.6
    assert any("cited_sources<3" in r for r in out["reasons"])
    assert any("alias_miss_ratio" in r for r in out["reasons"])


def test_score_answer_silver_band():
    out = score_answer(
        answer="见 [[A]] [[B]] [[C]]",
        citations=[
            {"slug": "A", "confidence": "INFERRED"},
            {"slug": "B", "confidence": "EXTRACTED"},
            {"slug": "C", "confidence": "AMBIGUOUS"},
        ],
        alias_miss_count=0,
        keyword_count=3,
        broken_links=0,
    )
    assert out["tier"] in ("silver", "gold")
    assert out["cited_sources"] == 3
    assert 0.6 <= out["score"] <= 1.0


def test_score_answer_hedge_phrase_does_not_trigger_gap():
    # 答案里出现"我不确定"这类措辞不是知识缺口:引用/覆盖/对齐都健康时不记 gap,
    # 措辞本身仍留在 reasons 里供诊断。
    out = score_answer(
        answer="我不确定具体步骤,见 [[A]] [[B]] [[C]] [[D]] [[E]]",
        citations=[{"slug": s, "confidence": "EXTRACTED"} for s in "ABCDE"],
        alias_miss_count=0,
        keyword_count=3,
        broken_links=0,
    )
    assert out["gap_flag"] is False
    assert any("hedge" in r for r in out["reasons"])


def test_score_answer_abstention_does_not_trigger_gap():
    # 纯拒答:坦白没找到资料且没编引用 → 可接受结果,不进 gap-queue,拿拒答保底分。
    out = score_answer(
        answer="我不确定,未找到相关资料。",
        citations=[],
        alias_miss_count=0,
        keyword_count=2,
        broken_links=0,
    )
    assert out["gap_flag"] is False
    assert any("abstained" in r for r in out["reasons"])
    assert out["score"] >= 0.5


def test_score_answer_broken_link_lowers_score():
    out = score_answer(
        answer="见 [[A]] [[B]] [[C]] [[D]] [[X]]",
        citations=[{"slug": s, "confidence": "EXTRACTED"} for s in ["A", "B", "C", "D", "X"]],
        alias_miss_count=0,
        keyword_count=4,
        broken_links=1,
    )
    # broken_link_ratio=1/5,扣 0.02 分
    assert out["broken_links"] == 1


def test_score_answer_no_citations():
    out = score_answer(
        answer="无引用",
        citations=[],
        alias_miss_count=0,
        keyword_count=2,
        broken_links=0,
    )
    assert out["cited_sources"] == 0
    assert out["gap_flag"] is True
    assert out["tier"] == "bronze"


def test_collect_citations_from_answer_dedupe():
    answer = "见 [[A]] 与 [[B]],再次提到 [[A|alpha]]"
    assert collect_citations_from_answer(answer) == ["A", "B"]


def test_confidence_mix_counts():
    cits = [
        {"confidence": "EXTRACTED"},
        {"confidence": "INFERRED"},
        {"confidence": "EXTRACTED"},
        {"confidence": "AMBIGUOUS"},
        {},  # 没有 confidence → UNKNOWN
    ]
    mix = confidence_mix(cits)
    assert mix == {"EXTRACTED": 2, "INFERRED": 1, "AMBIGUOUS": 1, "UNKNOWN": 1}


def test_evaluate_for_query_uses_wiki_existence_for_broken_links(tmp_path):
    # seed 两页:存在 A,不存在 X
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki" / "entities" / "A.md").write_text(
        "---\ntitle: A\nslug: A\nkind: entity\nconfidence: EXTRACTED\n---\n\n## 简介\n",
        encoding="utf-8",
    )
    answer = "见 [[A]] [[X]]"
    out = evaluate_for_query(
        answer=answer,
        expanded_keywords=["A"],
        citations_slugs=["A", "X"],
        wiki_root=tmp_path,
        candidates=[{"slug": "A", "title": "A"}],
    )
    assert out["broken_links"] == 1
    assert out["cited_sources"] == 2
    # X 在 wiki/ 下不存在 → broken_link 计入,confidence 走 default
    assert out["alias_miss_count"] == 0


def test_evaluate_for_query_alias_miss_count(tmp_path):
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    answer = "见 [[A]]"
    out = evaluate_for_query(
        answer=answer,
        expanded_keywords=["精益生产", "六西格玛", "A"],
        citations_slugs=["A"],
        wiki_root=tmp_path,
        candidates=[{"slug": "A", "title": "A"}],
    )
    # "精益生产"和"六西格玛"在 candidates 里都没出现 → 2 个 miss
    assert out["alias_miss_count"] == 2


def test_score_answer_clipped_to_0_1():
    """score 越界时被 clip 到 [0,1]。"""
    out = score_answer(
        answer="",
        citations=[],
        alias_miss_count=999,
        keyword_count=1,
        broken_links=999,
    )
    assert 0.0 <= out["score"] <= 1.0


def test_score_answer_includes_isolated_hits_field():
    out = score_answer(
        answer="[[A]]",
        citations=[{"slug": "A", "confidence": "EXTRACTED"}],
        alias_miss_count=0,
        keyword_count=1,
        broken_links=0,
        isolated_hits=2,
    )
    assert out["isolated_hits"] == 2


def test_three_and_four_good_citations_have_same_score():
    def scored(slugs):
        return score_answer(
            answer=" ".join(f"[[{slug}]]" for slug in slugs),
            citations=[{"slug": slug, "confidence": "EXTRACTED"} for slug in slugs],
            keyword_count=1,
        )

    assert scored(["A", "B", "C"])["score"] == scored(["A", "B", "C", "D"])["score"]


def test_evaluate_ignores_question_wrapper_in_alias_miss(tmp_path):
    (tmp_path / "wiki" / "topics").mkdir(parents=True)
    (tmp_path / "wiki" / "topics" / "智能制造.md").write_text(
        "---\ntitle: 智能制造\nslug: 智能制造\nkind: topic\nconfidence: EXTRACTED\n---\n",
        encoding="utf-8",
    )
    out = evaluate_for_query(
        answer="智能制造通过数据连接生产过程。[[智能制造]]",
        expanded_keywords=["请问智能制造是什么"],
        citations_slugs=["智能制造"],
        wiki_root=tmp_path,
        candidates=[{"slug": "智能制造", "title": "智能制造"}],
    )

    assert out["alias_miss_count"] == 0
    assert out["metrics"]["question_coverage"] == 1.0
    assert out["metrics"]["evidence_alignment"] == 1.0


def test_evaluate_matches_candidate_title_inside_compound_question_term(tmp_path):
    (tmp_path / "wiki" / "topics").mkdir(parents=True)
    out = evaluate_for_query(
        answer="精益生产的核心是持续消除浪费。[[lean]]",
        expanded_keywords=["精益生产的核心是什么"],
        citations_slugs=["lean"],
        wiki_root=tmp_path,
        candidates=[{"slug": "lean", "title": "精益生产"}],
    )

    assert out["alias_miss_count"] == 0
    assert out["metrics"]["question_coverage"] == 1.0


def test_quality_metrics_explain_answer_and_evidence_coverage(tmp_path):
    (tmp_path / "wiki" / "topics").mkdir(parents=True)
    out = evaluate_for_query(
        answer="精益生产强调消除浪费。[[lean]] [[unretrieved]]",
        expanded_keywords=["精益生产", "六西格玛"],
        citations_slugs=["lean", "unretrieved"],
        wiki_root=tmp_path,
        candidates=[{"slug": "lean", "title": "精益生产"}],
    )

    assert out["metrics"]["question_coverage"] == 0.5
    assert out["metrics"]["evidence_alignment"] == 0.5
    assert set(out["metrics"]) == {
        "source_sufficiency",
        "confidence",
        "question_coverage",
        "evidence_alignment",
        "citation_validity",
    }
