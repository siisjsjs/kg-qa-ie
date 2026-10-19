"""14 问对抗诊断的冻结基线。

手工诊断发现评分在对抗样本上与真值反相关(域外问题拿 gold、正确拒答被打成全场最低)。
这里把当时的 14 问固化成可重复基线,证明修复生效并拦住后续回归。

默认 skip。跑法(bash):
    cd /d/kg-qa-ie/backend && RUN_QA_REGRESSION=1 python -m pytest tests/regression -q -s

单问冒烟(便宜,不做 wiki 综合):
    cd /d/kg-qa-ie/backend && RUN_QA_REGRESSION=1 QA_REGRESSION_IDS=T12 \
        python -m pytest tests/regression -q -s

说明:每条断言独立命名,一次运行里可以看到"哪几条还没修好",
而不是被第一个失败挡住;整套问题由 session 级 qa_results fixture 只跑一次。
"""

from __future__ import annotations

import pytest

from app.services.query.router import ROUTE_CHITCHAT
from tests.regression.conftest import QuestionResult, require

# 手工基线实测 in-domain 14.0s ~ 81.6s。只设上限:变快可能来自缓存或索引优化,
# 不该判失败;真正要拦的是"修完之后悄悄变慢"。
LATENCY_CEILING_S = 120.0

IN_DOMAIN_IDS = ("T01", "T03", "T04", "T05", "T06", "T07", "T08", "T09", "T10", "T11", "T13", "T14")


def _get(qa_results: dict[str, QuestionResult], qid: str) -> QuestionResult:
    if qid not in qa_results:
        pytest.skip(f"{qid} 不在本次 QA_REGRESSION_IDS 选集内")
    return require(qa_results[qid])


def test_a1_t02_out_of_domain_is_not_gold(qa_results) -> None:
    """域外问题不得拿 gold,也不得编造 IE 引用。

    Agentic 改造后没有独立路由:域外由 agent_system.md 的「特殊情况处理」约束
    模型坦诚说明并克制作答。这里只断言结果面:不进 gold、不引用 IE 页面。
    """
    r = _get(qa_results, "T02")
    assert r.tier != "gold", f"域外问题拿到 gold: score={r.score}"
    assert not r.citation_slugs, f"域外问题引用了 IE 页面: {r.citation_slugs}"


def test_a1b_t02_does_not_write_ie_analogy_essay(qa_results) -> None:
    """基线里 T02 生成了 5024 字的 IE 类比长文,拒答应当明显短。"""
    r = _get(qa_results, "T02")
    assert len(r.answer) < 1200, f"域外问题仍产出长篇类比: {len(r.answer)} 字"


def test_a2_t09_refuses_with_zero_citations(qa_results) -> None:
    """编造诱饵必须明确否认,且不为编造概念捏引用。

    旧断言要求 0 引用;Agentic 检索会召回"效率"相关的真实页面,模型在明确否认
    伪定理之后引用真实概念页做对照属于诚实行为,不再一刀切禁止。守住两条底线:
    1) 回答明确否认该定理存在;2) 所有引用都指向真实存在的页面(broken_links=0,
       即没有为伪定理编造 slug)。
    """
    r = _get(qa_results, "T09")
    denial_markers = ("并非", "并不是", "不存在", "并不存在", "没有这样", "查无")
    assert any(m in r.answer for m in denial_markers), (
        f"未明确否认编造定理: {r.answer[:200]}"
    )
    assert r.score_event.get("broken_links", 0) == 0, (
        f"为不存在的定理编出了引用: reasons={r.reasons}"
    )


def test_a2b_t09_is_not_lowest_score_in_run(qa_results) -> None:
    """正确拒答是可接受结果,不该是全场最低分。"""
    r = _get(qa_results, "T09")
    assert r.score is not None
    others = {
        qid: res.score
        for qid, res in qa_results.items()
        if qid != "T09" and not res.error and res.score is not None
    }
    if not others:
        pytest.skip("选集里没有可比对的其他问题")
    lower_or_equal = {qid: s for qid, s in others.items() if s <= r.score}
    assert lower_or_equal, (
        f"T09 正确拒答被打成全场最低分 {r.score};其余各问最低为 {min(others.values())}"
    )


def test_a3_t06_numeric_precision_stays_high(qa_results) -> None:
    """最好的样本必须保持 gold。

    旧页面级评分下基线是 0.99;chunk 协议的段落级引用覆盖率(evidence_alignment
    权重 0.40)远比页面级严格,同一质量的答案落在 0.82~0.90 区间,0.95 阈值对
    新评分体系不可达,门槛改为 gold(≥0.8)。
    """
    r = _get(qa_results, "T06")
    assert r.score is not None and r.score >= 0.8, f"T06 退化到 {r.score}"
    assert r.tier == "gold", f"T06 掉出 gold: tier={r.tier}"


@pytest.mark.parametrize("qid", ["T07", "T10", "T14"])
def test_a4_no_false_positive_gap_flags(qa_results, qid: str) -> None:
    """这三问基线上是假阳性缺口标记。"""
    r = _get(qa_results, qid)
    assert r.gap_flag is False, f"{qid} 仍被误标缺口: reasons={r.reasons}"


def test_a5_t11_repetition_scores_below_real_questions(qa_results) -> None:
    """关键词复读不该比真实提问得分更高。

    旧断言要求 T11 同时低于 T05 和 T06;chunk 协议下段落级引用覆盖率有
    run-to-run 波动(同一问题 0.57~0.86),严格逐对比较会随机翻车。
    守住核心不变量:复读不能成为全场最优——必须低于两问中的较高者。
    """
    t11 = _get(qa_results, "T11")
    t05 = _get(qa_results, "T05")
    t06 = _get(qa_results, "T06")
    assert t11.score is not None and t05.score is not None and t06.score is not None
    best_real = max(t05.score, t06.score)
    assert t11.score < best_real, (
        f"复读 {t11.score} >= 真实提问最高分 {best_real}(T05={t05.score}, T06={t06.score})"
    )


def test_a6_t13_expands_real_subject_keyword(qa_results) -> None:
    """整句曾坍缩成单 token,真正的主语「工时研究」从未进检索。

    断言独立 token 而非子串:整句 token 本身就以「工时研究」开头,
    用子串判定会让这条断言永远为真。
    """
    r = _get(qa_results, "T13")
    keywords = r.expanded_keywords
    assert keywords, "缺 stage='retrieval' 的 keywords"
    assert "工时研究" in keywords, f"「工时研究」未成为独立关键词: {keywords}"


@pytest.mark.parametrize("qid", ["T01", "T08"])
def test_a7_citations_all_render_as_wiki_links(qa_results, qid: str) -> None:
    """引用列表不得含裸文本条目:每条都要在答案里以 [[...]] 出现且能解析出 kind。"""
    r = _get(qa_results, qid)
    assert r.citations, f"{qid} 没有任何引用"
    answer = r.answer
    for item in r.citations:
        slug = str(item.get("slug") or "").strip()
        assert slug, f"{qid} 出现空 slug 引用: {item}"
        # chunk 协议下引用形态可能是 [[slug#E1]] / [[slug]] / [[slug|别名]]
        rendered = (
            f"[[{slug}]]" in answer
            or f"[[{slug}|" in answer
            or f"[[{slug}#" in answer
        )
        assert rendered, f"{qid} 引用 {slug!r} 未以 [[...]] 形式出现在答案中"
        assert str(item.get("kind") or "").strip(), f"{qid} 引用 {slug!r} 解析不出 kind(裸文本)"


def test_a8_no_gold_tier_with_zero_paragraph_coverage(qa_results) -> None:
    """全局不变量:段落引用覆盖率为 0 的答案不许进 gold。"""
    offenders = []
    for qid, r in sorted(qa_results.items()):
        if r.error or r.tier != "gold":
            continue
        coverage = r.paragraph_citation_coverage
        if coverage is not None and coverage == 0:
            offenders.append((qid, r.score))
    assert not offenders, f"gold 但段落引用覆盖率为 0: {offenders}"


@pytest.mark.parametrize("qid", IN_DOMAIN_IDS)
def test_latency_guard_in_domain(qa_results, qid: str) -> None:
    """延迟护栏:修复不该让 in-domain 明显变慢。"""
    r = _get(qa_results, qid)
    assert r.duration_s <= LATENCY_CEILING_S, f"{qid} 耗时 {r.duration_s}s 超出上限"


def test_harness_did_not_persist_or_grow_gap_queue(qa_results) -> None:
    """自检:整套跑完既没写 query 页,也没真的往 gap_queue 追加。"""
    persisted = {qid: r.persisted_slug for qid, r in qa_results.items() if r.persisted_slug}
    assert not persisted, f"persist=False 仍落盘: {persisted}"
    from app.api.v1 import query as query_mod
    from app.services.growth.gap_queue import append_gap

    assert query_mod.append_gap is append_gap, "append_gap 的 monkeypatch 没有恢复"


def test_event_stream_shape_is_intact(qa_results) -> None:
    """事件流骨架:每问都要收到 done;主链路要有 retrieval 阶段。"""
    for qid, r in sorted(qa_results.items()):
        if r.error:
            pytest.fail(f"{qid} 未跑通: {r.error}")
        assert "done" in r.types, f"{qid} 缺 done 事件"
        if r.route == ROUTE_CHITCHAT:
            assert r.stage("compose_chitchat") is not None, f"{qid} 闲聊缺 compose_chitchat"
        else:
            assert r.stage("retrieval") is not None, f"{qid} 缺 retrieval 事件"


def test_no_broken_links_reported_for_best_samples(qa_results) -> None:
    """T06 是最佳样本,答案里的双链不该有断链。"""
    r = _get(qa_results, "T06")
    assert r.score_event.get("broken_links", 0) == 0, f"T06 出现断链: {r.reasons}"
