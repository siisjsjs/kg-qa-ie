"""问答质量评分。

评分关注答案是否覆盖问题、引用是否真正支撑相邻论断以及引用是否有效。引用数量在
3 个来源时即达到“来源充分”上限，避免同一答案仅因引用从 4 个变成 3 个就明显降分。

权重的重心放在 evidence_alignment(段落级引用覆盖率)上。来源合法性、页面抽取置信度
和字面关键词覆盖这三项，在答案完全脱离证据时依然能拿满分——实测一次跨领域臆造
(把 TCP 分帧类比成动素)拿到 0.85 gold,只有"论断段落是否就近引用"能把它压下去。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.query.keywords import meaningful_keywords
from app.services.query.validation import CITATION_TOKEN_RE, parse_citation_token
from app.services.wiki.frontmatter_io import parse_text
from app.services.wiki.fs_io import safe_join
from app.services.wiki.link_resolver import iter_wiki_pages

W_SOURCE_SUFFICIENCY = 0.20
W_CONFIDENCE = 0.10
W_QUESTION_COVERAGE = 0.20
W_EVIDENCE_ALIGNMENT = 0.40
W_CITATION_VALIDITY = 0.10

# 没有 validation 的直接调用方无法确认引用是否支撑论断，给中性值而不是满分。
NEUTRAL_ALIGNMENT = 0.6
# 论断段落一个引用都没有时的分数上限:这类答案不该进 silver。
UNCITED_CLAIMS_SCORE_CAP = 0.45
# 正确拒答的保底分,必须高于 UNCITED_CLAIMS_SCORE_CAP——拒答是可接受结果,
# 不该比一篇满是无据论断的长文还低。
ABSTENTION_SCORE_FLOOR = 0.55
UNSUPPORTED_NUMBER_PENALTY = 0.1
MAX_UNSUPPORTED_NUMBER_PENALTY = 0.3

GOLD_THRESHOLD = 0.8
SILVER_THRESHOLD = 0.6
# 引用完美但通篇不提问题里的领域词 → 大概率答的不是这个问题。这种答案可以是 silver,
# 不该是 gold:"见 [[A]] [[B]] [[C]]" 六个字就能把段落引用覆盖率刷成 1.0。
GOLD_MIN_QUESTION_COVERAGE = 0.25

CONFIDENCE_SCORES = {
    "EXTRACTED": 1.0,
    "INFERRED": 0.6,
    "AMBIGUOUS": 0.3,
    "HIGH": 1.0,
    "MEDIUM": 0.6,
    "LOW": 0.3,
}
DEFAULT_CONFIDENCE_SCORE = 0.7

# frontmatter.confidence 既有 EXTRACTED/low 这类字符串,也有 0.95 这类数值。
# 57 个页面写的是 low,不在旧词表里,被当成中性 0.7 静默提权。
_CONFIDENCE_LABEL_ALIASES = {
    "EXTRACTED": "EXTRACTED",
    "INFERRED": "INFERRED",
    "AMBIGUOUS": "AMBIGUOUS",
    "HIGH": "EXTRACTED",
    "MEDIUM": "INFERRED",
    "LOW": "AMBIGUOUS",
}
_NUMERIC_CONFIDENCE_BANDS = ((0.85, "EXTRACTED"), (0.5, "INFERRED"))

_HEDGE_PHRASES = (
    "我不太确定",
    "我不确定",
    "未找到资料",
    "未找到相关",
    "知识库未覆盖",
    "无法确认",
    "可能不准确",
    "信息不足",
)

_KIND_DIRS = ("entities", "topics", "sources", "queries", "synthesis", "comparisons")

_CITATION_PATTERN = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\[\]]+?)?\]\]")

def _text_contains(text: str, term: str) -> bool:
    return term.lower() in (text or "").lower()


def _resolve_page(slug: str, wiki_root: Path) -> Path | None:
    for sub in _KIND_DIRS:
        p = safe_join(wiki_root, "wiki", sub, f"{slug}.md")
        if p.exists():
            return p
    return None


def _confidence_for_slug(slug: str, wiki_root: Path) -> float:
    """读 wiki/<kind>/<slug>.md frontmatter.confidence;缺失返回中性 0.7。"""
    page = _resolve_page(slug, Path(wiki_root))
    if page is None:
        return DEFAULT_CONFIDENCE_SCORE
    try:
        fm, _ = parse_text(page.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_CONFIDENCE_SCORE
    raw = fm.get("confidence")
    if raw is None:
        return DEFAULT_CONFIDENCE_SCORE
    if isinstance(raw, (int, float)):
        return max(0.0, min(1.0, float(raw)))
    return CONFIDENCE_SCORES.get(str(raw).upper(), DEFAULT_CONFIDENCE_SCORE)


def _existing_slugs(wiki_root: Path) -> set[str]:
    return {p.stem for p in iter_wiki_pages(Path(wiki_root))}


def collect_citations_from_answer(answer: str) -> list[str]:
    """从答案抽 [[X]] 引用,去重保序。"""
    seen: set[str] = set()
    out: list[str] = []
    for m in _CITATION_PATTERN.finditer(answer):
        slug = m.group(1).strip()
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def confidence_mix(citations: list[dict]) -> dict[str, int]:
    mix = {"EXTRACTED": 0, "INFERRED": 0, "AMBIGUOUS": 0, "UNKNOWN": 0}
    for c in citations:
        raw = (c.get("confidence") or "").upper()
        if raw in mix:
            mix[raw] += 1
        else:
            mix["UNKNOWN"] += 1
    return mix


def score_answer(
    *,
    answer: str,
    citations: list[dict],
    alias_miss_count: int = 0,
    keyword_count: int = 0,
    broken_links: int = 0,
    isolated_hits: int = 0,
    graph_data: dict | None = None,
    wiki_root: Path | None = None,
    question_coverage: float | None = None,
    evidence_alignment: float | None = None,
    uncited_claims: bool = False,
    unsupported_numbers: int = 0,
) -> dict:
    """综合评分。

    Args:
        answer: LLM 答复全文
        citations: 已解析的引用列表 [{slug, title, confidence?}, ...]
        alias_miss_count: 关键词在 wiki 中未命中的数量(由调用方算)
        keyword_count: 关键词总数(用于算 alias_miss_ratio)
        broken_links: 答案中 [[X]] 指向不存在页的数量
        isolated_hits: 引用中孤立节点数量(可选,P4 暂未参与公式)
        graph_data: 图谱(为 isolated_hits 预留,可选)
        wiki_root: 备用,当 citations 缺 confidence 时回查 frontmatter

    Returns:
        {score, tier, cited_sources, confidence_mix, alias_miss_count,
         broken_links, isolated_hits, gap_flag, reasons}
    """
    cited_sources = len({c.get("slug") for c in citations if c.get("slug")})
    # 三个独立来源已经足以支撑多数普通问答；更多引用不应成为主要加分手段。
    source_sufficiency = min(cited_sources, 3) / 3

    # confidence_avg
    if citations:
        confs: list[float] = []
        for c in citations:
            raw = c.get("confidence")
            if raw is None and wiki_root is not None and c.get("slug"):
                confs.append(_confidence_for_slug(c["slug"], wiki_root))
                continue
            if isinstance(raw, (int, float)):
                confs.append(max(0.0, min(1.0, float(raw))))
            else:
                confs.append(CONFIDENCE_SCORES.get((raw or "").upper(), DEFAULT_CONFIDENCE_SCORE))
        confidence_avg = sum(confs) / len(confs) if confs else DEFAULT_CONFIDENCE_SCORE
    else:
        confidence_avg = 0.0

    # alias_miss_ratio
    if keyword_count > 0:
        alias_miss_ratio = max(0.0, min(1.0, alias_miss_count / keyword_count))
    else:
        alias_miss_ratio = 0.0

    if question_coverage is None:
        question_coverage = 1.0 - alias_miss_ratio
    question_coverage = max(0.0, min(1.0, question_coverage))

    # broken_link_ratio = broken / 总引用次数(从答案 [[X]] 计)
    total_links = len(_CITATION_PATTERN.findall(answer))
    if total_links > 0:
        broken_link_ratio = max(0.0, min(1.0, broken_links / total_links))
    else:
        broken_link_ratio = 0.0

    citation_validity = (1.0 - broken_link_ratio) if total_links else 0.0
    if evidence_alignment is None:
        evidence_alignment = NEUTRAL_ALIGNMENT if citations else 0.0
    evidence_alignment = max(0.0, min(1.0, evidence_alignment))

    hedge_hit = next((p for p in _HEDGE_PHRASES if p in answer), None)
    # 拒答的形状:坦白说了不知道,而且确实没编引用。有引用的"我不确定"不算拒答。
    abstained = hedge_hit is not None and cited_sources == 0 and total_links == 0

    score = (
        W_SOURCE_SUFFICIENCY * source_sufficiency
        + W_CONFIDENCE * confidence_avg
        + W_QUESTION_COVERAGE * question_coverage
        + W_EVIDENCE_ALIGNMENT * evidence_alignment
        + W_CITATION_VALIDITY * citation_validity
    )
    if uncited_claims:
        score = min(score, UNCITED_CLAIMS_SCORE_CAP)
    if abstained:
        score = max(score, ABSTENTION_SCORE_FLOOR)
    score = round(max(0.0, min(1.0, score)), 4)

    thin_answer = question_coverage < GOLD_MIN_QUESTION_COVERAGE
    if score >= GOLD_THRESHOLD and not thin_answer:
        tier = "gold"
    elif score >= SILVER_THRESHOLD:
        tier = "silver"
    else:
        tier = "bronze"

    # gap_flag + reasons
    reasons: list[str] = []
    if score < 0.6:
        reasons.append(f"score<0.6 ({score})")
    if cited_sources < 3:
        # 保留旧 reason 文本，供现有日志和前端诊断兼容；是否形成缺口改由更合理的
        # “少于两个来源”门槛决定。
        reasons.append(f"cited_sources<3 ({cited_sources})")
    if alias_miss_ratio > 0.3:
        reasons.append(f"alias_miss_ratio>0.3 ({alias_miss_ratio:.2f})")
    if question_coverage < 0.5:
        reasons.append(f"question_coverage<0.5 ({question_coverage:.2f})")
    if evidence_alignment < 0.5:
        reasons.append(f"evidence_alignment<0.5 ({evidence_alignment:.2f})")
    if broken_link_ratio > 0:
        reasons.append(f"broken_links={broken_links}/{total_links}")
    if hedge_hit:
        reasons.append(f"hedge:{hedge_hit}")
    if abstained:
        reasons.append("abstained")
    if uncited_claims:
        reasons.append("uncited_claims")
    if unsupported_numbers:
        reasons.append(f"unsupported_numbers={unsupported_numbers}")
    if thin_answer and score >= GOLD_THRESHOLD:
        reasons.append(f"gold_withheld:question_coverage={question_coverage:.2f}")

    # 答案里出现"我不确定/未找到资料"这类措辞不是知识缺口,不该记入 gap-queue;
    # 拒答(hedge + 没编引用)是可接受结果,整体不进 gap-queue。只有分数/来源/覆盖
    # 等真实质量信号低于门槛才算 gap。
    gap_flag = (
        score < 0.6
        or cited_sources < 2
        or question_coverage < 0.5
        or evidence_alignment < 0.5
        or uncited_claims
    ) and not abstained

    return {
        "score": score,
        "tier": tier,
        "cited_sources": cited_sources,
        "confidence_mix": confidence_mix(citations),
        "alias_miss_count": alias_miss_count,
        "broken_links": broken_links,
        "isolated_hits": isolated_hits,
        "metrics": {
            "source_sufficiency": round(source_sufficiency, 4),
            "confidence": round(confidence_avg, 4),
            "question_coverage": round(question_coverage, 4),
            "evidence_alignment": round(evidence_alignment, 4),
            "citation_validity": round(citation_validity, 4),
        },
        "gap_flag": gap_flag,
        "reasons": reasons,
    }


def _concept_buckets(
    terms: list[str], alias_groups: list[list[str]] | None
) -> list[list[str]]:
    """把展开后的关键词按别名组归并成"概念桶"。

    同一别名组的变体(精益生产/lean/TPS/丰田生产方式…)算一个概念:答案没必要
    把每个变体都复述一遍,任一变体出现即视为覆盖。不在任何组里的词各自成桶。

    匹配规则:小写精确命中,或 term 是某成员的子串——meaningful_keywords 会把
    多词英文别名拆开("lean production" → lean + production),拆出来的 token
    只能靠子串归回原组。注意**不做反向子串**(成员 ⊂ term):否则"工效学是什么"
    这类整句 token 会归进"工效学"组被判覆盖,把「见 [[A]][[B]][[C]]」式的
    凑数答案的覆盖率抬进 gold 区。
    """
    groups = [[m.lower() for m in g if m] for g in (alias_groups or []) if g]

    def group_index(term: str) -> int | None:
        t = term.lower()
        for i, g in enumerate(groups):
            if any(t == m or t in m for m in g):
                return i
        return None

    buckets: list[list[str]] = []
    used: set[str] = set()
    bucket_by_group: dict[int, list[str]] = {}
    for term in terms:
        if term in used:
            continue
        used.add(term)
        gi = group_index(term)
        if gi is None:
            buckets.append([term])
            continue
        if gi in bucket_by_group:
            bucket_by_group[gi].append(term)
        else:
            bucket = [term]
            bucket_by_group[gi] = bucket
            buckets.append(bucket)
    return buckets


def evaluate_for_query(
    *,
    answer: str,
    expanded_keywords: list[str],
    citations_slugs: list[str],
    wiki_root: Path,
    candidates: list[dict] | None = None,
    validation: dict | None = None,
    alias_groups: list[list[str]] | None = None,
) -> dict:
    """从 query SSE handler 直接拿到的扁平上下文,算出最终 quality dict。

    - alias_miss_count = expanded_keywords 中没出现在任何 grep candidate slug/title 里
      的关键词数(按别名组归并成概念桶后统计,见 _concept_buckets)
    - broken_links = answer 中 [[X]] 指向 wiki/ 下不存在 X 的数量
    - validation = evidence.validate_answer() 的结果(可选)。给了就用段落级引用覆盖率
      当 evidence_alignment;出处合法性退成门槛,不再单独当得分项。
    """
    wiki_root = Path(wiki_root)
    existing = _existing_slugs(wiki_root)

    citations_slugs = list(dict.fromkeys(citations_slugs))
    cit_dicts = [
        {
            "slug": slug,
            "title": slug,
            "confidence": _slug_confidence_label(slug, wiki_root),
        }
        for slug in citations_slugs
    ]

    # broken_links:答案里 [[X]] 数,X 不在 existing。
    # chunk 引用协议下 token 形如 "工效学#E3" / "web#W1",要先归一化:
    # 剥 #En 编号取 slug;web 引用不是 wiki 链接,不参与 broken 统计。
    broken = 0
    for x in _CITATION_PATTERN.findall(answer):
        parsed = parse_citation_token(x.strip())
        if parsed.get("type") == "web":
            continue
        if (parsed.get("slug") or "").strip() not in existing:
            broken += 1

    # 评分只使用领域词，排除“请问/如何/是什么”等包装词以及整句 token。
    candidates = candidates or []
    def segment_text(segment) -> str:
        if isinstance(segment, dict):
            return str(
                segment.get("text")
                or segment.get("content")
                or segment.get("line")
                or segment.get("snippet")
                or ""
            )
        return str(segment or "")

    candidate_text = " ".join(
        f"{c.get('slug') or ''} {c.get('title') or ''} "
        f"{' '.join(segment_text(item) for item in (c.get('hit_segments') or []))}"
        for c in candidates
    ).lower()
    candidate_titles = [
        str(c.get("title") or "").strip().lower()
        for c in candidates
        if len(str(c.get("title") or "").strip()) >= 2
    ]
    terms = meaningful_keywords(" ".join(expanded_keywords))
    # 同一别名组的变体归并成概念桶:别名扩展会把 lean/TPS/toyota 全灌进 terms,
    # 逐词统计会把覆盖率的分母灌水,制造假阳性 gap 标记。
    buckets = _concept_buckets(terms, alias_groups)

    def evidence_covers(term: str) -> bool:
        return _text_contains(candidate_text, term) or any(title in term for title in candidate_titles)

    alias_miss = sum(
        1 for bucket in buckets if not any(evidence_covers(kw) for kw in bucket)
    )

    # 覆盖度只看正文散文,不看 [[...]] 引用 token:否则"见 [[工效学]] [[VSM]]"
    # 六个字的凑数答案光靠 token 里的概念名就能把覆盖率刷上去。
    prose = CITATION_TOKEN_RE.sub(" ", answer or "")

    def answer_covers(term: str) -> bool:
        return _text_contains(prose, term) or any(
            title in term and _text_contains(prose, title) for title in candidate_titles
        )

    # 问题覆盖度衡量领域概念是否真正出现在答案中。没有可评价关键词时取中性 1，
    # 避免纯包装问句制造假阴性。桶内任一变体命中即算覆盖。
    if buckets:
        covered = sum(1 for bucket in buckets if any(answer_covers(kw) for kw in bucket))
        question_coverage = covered / len(buckets)
    else:
        question_coverage = 1.0

    # 出处合法性:引用的 slug 是否来自本次检索候选集。它只能证明"引用不是凭空捏的",
    # 不能证明引用支撑了论断,所以在有 validation 时退成上限门槛。
    candidate_slugs = {str(c.get("slug")) for c in candidates if c.get("slug")}
    # 补读回来的页同样是真实证据(已进 context),不能因为初检索没召回就判成来源不合法,
    # 否则 P7 的补读越成功、分数越低。
    if validation:
        candidate_slugs |= {str(s) for s in (validation.get("recovered_citations") or [])}
    if citations_slugs:
        aligned = sum(1 for slug in citations_slugs if slug in candidate_slugs)
        provenance = aligned / len(citations_slugs)
    else:
        provenance = 0.0

    uncited_claims = False
    unsupported_numbers = 0
    if validation is None:
        evidence_alignment = provenance
    else:
        unsupported_numbers = len(validation.get("unsupported_numbers") or [])
        coverage = float(validation.get("paragraph_citation_coverage") or 0.0)
        penalty = min(
            MAX_UNSUPPORTED_NUMBER_PENALTY,
            UNSUPPORTED_NUMBER_PENALTY * unsupported_numbers,
        )
        evidence_alignment = min(max(coverage - penalty, 0.0), provenance)
        uncited_claims = bool(validation.get("claim_paragraphs")) and coverage == 0.0

    return score_answer(
        answer=answer,
        citations=cit_dicts,
        alias_miss_count=alias_miss,
        keyword_count=len(buckets),
        broken_links=broken,
        wiki_root=wiki_root,
        question_coverage=question_coverage,
        evidence_alignment=evidence_alignment,
        uncited_claims=uncited_claims,
        unsupported_numbers=unsupported_numbers,
    )


def _confidence_label(raw) -> str | None:
    """把 frontmatter.confidence 归一成三档标签。

    590 个页面写死数值 0.95,57 个写字符串 low。只认三个大写标签会让 confidence_mix
    永远全是 UNKNOWN,前端徽章因此从未亮过。
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        for threshold, label in _NUMERIC_CONFIDENCE_BANDS:
            if float(raw) >= threshold:
                return label
        return "AMBIGUOUS"
    return _CONFIDENCE_LABEL_ALIASES.get(str(raw).strip().upper())


def _slug_confidence_label(slug: str, wiki_root: Path) -> str | None:
    """返回 frontmatter.confidence 的标签字符串(EXTRACTED/INFERRED/AMBIGUOUS)。"""
    page = _resolve_page(slug, wiki_root)
    if page is None:
        return None
    try:
        fm, _ = parse_text(page.read_text(encoding="utf-8"))
    except Exception:
        return None
    return _confidence_label(fm.get("confidence"))
