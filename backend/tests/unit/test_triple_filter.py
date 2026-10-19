"""triple_filter.py 单元测试。"""

from app.services.ingestion.triple_filter import filter_entities, filter_relations


def test_filter_entities_removes_short_names():
    es = [
        {"name": "x", "brief": "tiny"},
        {"name": "工效学", "brief": "ok"},
    ]
    out = filter_entities(es, min_length=2)
    names = [e["name"] for e in out]
    assert names == ["工效学"]


def test_filter_entities_removes_stopwords():
    es = [{"name": "the", "brief": "the"}, {"name": "VSM", "brief": "value stream mapping"}]
    out = filter_entities(es)
    assert [e["name"] for e in out] == ["VSM"]


def test_filter_entities_removes_lazy_brief():
    es = [{"name": "工效学", "brief": "工效学"}, {"name": "VSM", "brief": "valuable"}]
    out = filter_entities(es)
    assert [e["name"] for e in out] == ["VSM"]


def test_filter_entities_dedupes_by_name():
    es = [
        {"name": "工效学", "brief": "first"},
        {"name": "工效学", "brief": "duplicate"},
    ]
    out = filter_entities(es)
    assert len(out) == 1
    assert out[0]["brief"] == "first"


def test_filter_relations_removes_self_loops():
    rs = [{"src": "A", "dst": "A", "rel_type": "实现"}]
    out = filter_relations(rs)
    assert out == []


def test_filter_relations_removes_missing_src_or_dst():
    rs = [
        {"src": "A", "dst": "", "rel_type": "实现"},
        {"src": "", "dst": "B", "rel_type": "依赖"},
        {"src": "A", "dst": "B", "rel_type": "实现"},
    ]
    out = filter_relations(rs)
    assert len(out) == 1
    assert out[0]["src"] == "A"


def test_filter_relations_whitelist_filters_unknown_types():
    rs = [
        {"src": "A", "dst": "B", "rel_type": "实现"},
        {"src": "A", "dst": "B", "rel_type": "陌生关系"},
    ]
    out = filter_relations(rs, allowed_types=["实现", "依赖"])
    assert len(out) == 1
    assert out[0]["rel_type"] == "实现"


def test_filter_relations_no_whitelist_keeps_all_valid():
    rs = [
        {"src": "A", "dst": "B", "rel_type": "实现"},
        {"src": "A", "dst": "B", "rel_type": "陌生"},
    ]
    out = filter_relations(rs, allowed_types=None)
    assert len(out) == 2


def test_filter_relations_dedupes():
    rs = [
        {"src": "A", "dst": "B", "rel_type": "实现"},
        {"src": "A", "dst": "B", "rel_type": "实现"},
    ]
    out = filter_relations(rs)
    assert len(out) == 1
