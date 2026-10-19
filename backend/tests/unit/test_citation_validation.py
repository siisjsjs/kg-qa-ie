"""validation.py + citations.build_citation_details_v2 单测:引用协议解析与降级。"""

from __future__ import annotations

import pytest

from app.services.query.citations import build_citation_details_v2
from app.services.query.evidence_pool import EvidencePool
from app.services.query.validation import (
    extract_citation_refs,
    extract_cited_slugs,
    parse_citation_token,
    validate_and_clean_answer_v2,
)


# ---------------------------------------------------------------------------
# parse / extract
# ---------------------------------------------------------------------------


def test_parse_citation_token_shapes():
    assert parse_citation_token("工效学#E1") == {"type": "chunk", "slug": "工效学", "ref": "E1"}
    assert parse_citation_token("web#W2") == {"type": "web", "slug": None, "ref": "W2"}
    assert parse_citation_token("工效学") == {"type": "page", "slug": "工效学", "ref": None}
    # '#' 后不是编号 → 当页面引用,丢锚点
    assert parse_citation_token("工效学#简介") == {"type": "page", "slug": "工效学", "ref": None}
    # 编号大小写归一
    assert parse_citation_token("工效学#e3")["ref"] == "E3"
    # 裸编号(模型漏写 slug)按证据引用处理,清洗阶段反查补全
    assert parse_citation_token("E4") == {"type": "chunk", "slug": None, "ref": "E4"}
    assert parse_citation_token("w2") == {"type": "web", "slug": None, "ref": "W2"}


def test_extract_citation_refs_dedup_and_alias_pipe():
    answer = "论断 [[工效学#E1]],再说一次 [[工效学#E1]],别名 [[VSM|价值流图]],网络 [[web#W1]]。"
    refs = extract_citation_refs(answer)
    raws = [r["raw"] for r in refs]
    assert raws == ["工效学#E1", "VSM", "web#W1"]


def test_extract_cited_slugs_excludes_web():
    answer = "[[工效学#E1]] [[工效学]] [[VSM#E2]] [[web#W1]]"
    assert extract_cited_slugs(answer) == ["工效学", "VSM"]


# ---------------------------------------------------------------------------
# validate_and_clean_answer_v2
# ---------------------------------------------------------------------------


def _seed_page(root, slug: str):
    p = root / "wiki" / "entities" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntitle: {slug}\nslug: {slug}\nkind: entity\n---\n\n正文。\n", encoding="utf-8"
    )


def _pool_with_chunk(slug="工效学", text="研究人在工作中的效率与舒适") -> EvidencePool:
    pool = EvidencePool()
    pool.add_chunks(
        [
            {
                "chunk_id": f"{slug}::01",
                "slug": slug,
                "title": slug,
                "kind": "entity",
                "heading": "简介",
                "text": text,
                "line_start": 3,
                "line_end": 5,
            }
        ]
    )
    return pool


def test_valid_chunk_citation_passes(tmp_path):
    _seed_page(tmp_path, "工效学")
    pool = _pool_with_chunk()
    answer = "工效学研究效率与舒适 [[工效学#E1]]。"
    cleaned, v = validate_and_clean_answer_v2(answer, pool, wiki_root=tmp_path)
    assert cleaned == answer
    assert v["passed"] is True
    assert v["invalid_citations"] == []


def test_chunk_citation_wrong_slug_corrected(tmp_path):
    _seed_page(tmp_path, "工效学")
    pool = _pool_with_chunk(slug="工效学")
    answer = "论断 [[人因工程#E1]]。"
    cleaned, _ = validate_and_clean_answer_v2(answer, pool, wiki_root=tmp_path)
    assert "[[工效学#E1]]" in cleaned
    assert "人因工程#E1" not in cleaned


def test_bare_ref_token_resolved_to_full_citation(tmp_path):
    """[[E1]] 裸编号 → 从证据池反查 slug 补全成 [[工效学#E1]],不整个误删。"""
    _seed_page(tmp_path, "工效学")
    pool = _pool_with_chunk()
    answer = "工效学研究效率与舒适 [[E1]]。"
    cleaned, v = validate_and_clean_answer_v2(answer, pool, wiki_root=tmp_path)
    assert "[[工效学#E1]]" in cleaned
    assert v["invalid_citations"] == []
    assert v["paragraph_citation_coverage"] == 1.0


def test_code_wrapped_citation_unwrapped(tmp_path):
    """模型把引用包进行内代码 `[[slug#E1]]` → 清洗后剥掉反引号,正常渲染成角标。"""
    _seed_page(tmp_path, "工效学")
    pool = _pool_with_chunk()
    answer = "工效学研究效率与舒适 `[[工效学#E1]]`,另见 ` [[E1]] `。"
    cleaned, v = validate_and_clean_answer_v2(answer, pool, wiki_root=tmp_path)
    assert "`" not in cleaned
    assert "[[工效学#E1]]" in cleaned
    assert v["invalid_citations"] == []


def test_bare_web_ref_token_resolved(tmp_path):
    pool = EvidencePool()
    pool.add_web_results(
        [{"url": "https://a.com/x", "title": "A", "content": "精益生产趋势"}]
    )
    answer = "精益生产趋势正在变化,这一判断来自最近的行业报告 [[W1]]。"
    cleaned, _ = validate_and_clean_answer_v2(answer, pool, wiki_root=tmp_path)
    assert "[[web#W1]]" in cleaned


def test_missing_ref_degrades_to_page_citation(tmp_path):
    _seed_page(tmp_path, "工效学")
    pool = _pool_with_chunk()  # 只有 E1
    answer = "论断 [[工效学#E9]]。"
    cleaned, v = validate_and_clean_answer_v2(answer, pool, wiki_root=tmp_path)
    assert "[[工效学]]" in cleaned
    assert "E9" not in cleaned
    assert "工效学" in v["recovered_citations"]


def test_fabricated_slug_removed_with_residue_line(tmp_path):
    pool = _pool_with_chunk()
    answer = "真论断 [[工效学#E1]]。\n\n引用:\n- [[编造页]]\n"
    _seed_page(tmp_path, "工效学")
    cleaned, v = validate_and_clean_answer_v2(answer, pool, wiki_root=tmp_path)
    assert "编造页" not in cleaned
    # 删除后 "- " 残行整行移除
    assert "- \n" not in cleaned and not cleaned.rstrip().endswith("-")
    assert "编造页" in v["invalid_citations"]
    assert v["revised"] is True


def test_separator_lines_not_eaten_when_no_removal(tmp_path):
    """没有发生删除的行不做残行清理:markdown 分隔线要保留。"""
    _seed_page(tmp_path, "工效学")
    pool = _pool_with_chunk()
    answer = "上文 [[工效学#E1]]。\n\n---\n\n下文 [[假页]]。"
    cleaned, _ = validate_and_clean_answer_v2(answer, pool, wiki_root=tmp_path)
    assert "\n---\n" in cleaned


def test_web_citation_valid_and_invalid(tmp_path):
    pool = EvidencePool()
    pool.add_web_results(
        [{"url": "https://a.com/x", "title": "A", "content": "精益生产趋势"}]
    )
    answer = "网络论断 [[web#W1]],编造 [[web#W9]]。"
    cleaned, v = validate_and_clean_answer_v2(answer, pool, wiki_root=tmp_path)
    assert "[[web#W1]]" in cleaned
    assert "W9" not in cleaned
    assert "web#W9" in v["invalid_citations"]


def test_unsupported_numbers_flagged(tmp_path):
    _seed_page(tmp_path, "工效学")
    pool = _pool_with_chunk(text="标准工时为 4.5 分钟")
    answer = "标准工时 4.5 分钟 [[工效学#E1]],改善率高达 87%。"
    _, v = validate_and_clean_answer_v2(answer, pool, wiki_root=tmp_path)
    assert "87%" in v["unsupported_numbers"]
    assert "4.5" not in v["unsupported_numbers"]


def test_paragraph_citation_coverage(tmp_path):
    _seed_page(tmp_path, "工效学")
    pool = _pool_with_chunk()
    answer = (
        "第一段论断,内容足够长凑到四十个字符以上才算 claim 段落 [[工效学#E1]]。\n\n"
        "第二段论断,同样把内容写得足够长、凑到四十个中文字符以上,但整段没有任何引用标注在句末。"
    )
    _, v = validate_and_clean_answer_v2(answer, pool, wiki_root=tmp_path)
    assert v["claim_paragraphs"] == 2
    assert v["cited_paragraphs"] == 1
    assert v["paragraph_citation_coverage"] == 0.5


# ---------------------------------------------------------------------------
# build_citation_details_v2
# ---------------------------------------------------------------------------


def test_build_citation_details_v2_mixed(tmp_path):
    _seed_page(tmp_path, "工效学")
    _seed_page(tmp_path, "VSM")
    pool = _pool_with_chunk()
    pool.add_web_results(
        [{"url": "https://a.com/x", "title": "外部文章", "content": "外部内容片段"}]
    )
    answer = "论断 [[工效学#E1]],网络 [[web#W1]],页面 [[VSM]]。"
    refs = extract_citation_refs(answer)
    details = build_citation_details_v2(refs, evidence_pool=pool, wiki_root=tmp_path)
    by_type = {d["type"]: d for d in details}
    assert by_type["chunk"]["slug"] == "工效学"
    assert by_type["chunk"]["ref"] == "E1"
    assert by_type["chunk"]["line_start"] == 3
    assert by_type["chunk"]["quote"].startswith("研究人在")
    assert by_type["web"]["url"] == "https://a.com/x"
    assert by_type["web"]["ref"] == "W1"
    assert by_type["page"]["slug"] == "VSM"


def test_build_citation_details_v2_quote_truncated(tmp_path):
    _seed_page(tmp_path, "工效学")
    pool = _pool_with_chunk(text="长" * 500)
    refs = extract_citation_refs("[[工效学#E1]]")
    details = build_citation_details_v2(refs, evidence_pool=pool, wiki_root=tmp_path)
    assert len(details[0]["quote"]) == 200
