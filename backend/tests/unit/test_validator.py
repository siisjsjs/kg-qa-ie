"""validator.py 单元测试。

覆盖:source_summary 必填、entities/topics/connections 字段校验、
confidence 枚举、new_vs_existing 嵌套对象、normalize_step1 空数组补齐。
"""

from app.services.ingestion.validator import normalize_step1, validate_step1


def _good_payload() -> dict:
    return {
        "source_summary": {
            "title": "易树平基础工业工程第3章笔记",
            "kind": "note",
            "domain_tags": ["人因工程"],
            "key_takeaways": ["工效学是关注人机交互的学科", "包含人体测量与作业测定"],
        },
        "entities": [
            {
                "name": "工效学",
                "kind": "concept",
                "brief": "研究人在工作中的效率与舒适的学科",
                "aliases": ["人因工程", "ergonomics"],
                "evidence": "原文第 1 段",
                "confidence": "EXTRACTED",
                "domain_tags": ["人因工程"],
            },
        ],
        "topics": [
            {
                "name": "工效学的研究范畴",
                "scope": "限于作业测定与人体测量",
                "related_entities": ["工效学"],
                "evidence": "原文第 2 段",
                "confidence": "INFERRED",
            },
        ],
        "connections": [
            {
                "src": "工效学",
                "dst": "作业测定",
                "rel_type": "实现",
                "evidence": "原文",
                "confidence": "EXTRACTED",
            },
        ],
        "contradictions": [],
        "new_vs_existing": {
            "new_entity_names": ["工效学"],
            "matched_entity_pages": [],
            "new_topic_names": ["工效学的研究范畴"],
            "matched_topic_pages": [],
        },
    }


def test_validate_good_payload():
    ok, errors = validate_step1(_good_payload())
    assert ok, errors
    assert errors == []


def test_validate_missing_source_summary():
    bad = _good_payload()
    del bad["source_summary"]
    ok, errors = validate_step1(bad)
    assert not ok
    assert any("source_summary" in e for e in errors)


def test_validate_source_summary_must_have_domain_tags():
    bad = _good_payload()
    bad["source_summary"]["domain_tags"] = []
    ok, errors = validate_step1(bad)
    assert not ok
    assert any("domain_tags" in e for e in errors)


def test_validate_invalid_confidence():
    bad = _good_payload()
    bad["entities"][0]["confidence"] = "MAYBE"  # 非枚举
    ok, errors = validate_step1(bad)
    assert not ok
    assert any("confidence" in e for e in errors)


def test_validate_invalid_entity_kind():
    bad = _good_payload()
    bad["entities"][0]["kind"] = "vegetable"
    ok, errors = validate_step1(bad)
    assert not ok
    assert any("kind" in e for e in errors)


def test_validate_connection_must_have_rel_type():
    bad = _good_payload()
    bad["connections"][0]["rel_type"] = ""
    ok, errors = validate_step1(bad)
    assert not ok
    assert any("rel_type" in e for e in errors)


def test_validate_matched_entity_pages_match_kind_enum():
    bad = _good_payload()
    bad["new_vs_existing"]["matched_entity_pages"] = [
        {"name": "工效学", "slug": "工效学", "match_kind": "vibe"}
    ]
    ok, errors = validate_step1(bad)
    assert not ok
    assert any("match_kind" in e for e in errors)


def test_validate_root_must_be_dict():
    ok, errors = validate_step1([])
    assert not ok
    assert "<root>" in errors[0]


def test_normalize_step1_fills_missing_arrays():
    p = {
        "source_summary": _good_payload()["source_summary"],
        "new_vs_existing": {},
    }
    out = normalize_step1(p)
    assert out["entities"] == []
    assert out["topics"] == []
    assert out["connections"] == []
    assert out["contradictions"] == []
    nve = out["new_vs_existing"]
    assert nve["new_entity_names"] == []
    assert nve["matched_entity_pages"] == []
    assert nve["new_topic_names"] == []
    assert nve["matched_topic_pages"] == []


def test_validate_optional_arrays_can_be_omitted():
    """entities/topics/connections/contradictions 缺省时不应报错。"""
    payload = {
        "source_summary": _good_payload()["source_summary"],
        "new_vs_existing": _good_payload()["new_vs_existing"],
    }
    ok, errors = validate_step1(payload)
    assert ok, errors
