"""ingestion/validator.py:Step1 输出 JSON schema 校验。

按规划文档 §3.1.1 严格对齐:
  - source_summary 是对象,含 title/kind/domain_tags/key_takeaways
  - entities 含 name/kind/brief/aliases/evidence/confidence/domain_tags
  - topics 含 name/scope/related_entities/evidence/confidence
  - connections 含 src/dst/rel_type/evidence/confidence
  - new_vs_existing 含 new_entity_names/matched_entity_pages/new_topic_names/matched_topic_pages
  - confidence 三态枚举:EXTRACTED / INFERRED / AMBIGUOUS

不依赖外部 jsonschema 库 — 手写校验,错误信息以 dotted path 列出。
"""

from __future__ import annotations

import re
from typing import Any

CONFIDENCE_VALUES = {"EXTRACTED", "INFERRED", "AMBIGUOUS"}
ENTITY_KINDS = {"method", "tool", "concept", "person", "org", "metric"}
SOURCE_KINDS = {"textbook", "article", "note", "case", "other"}
DEFAULT_REL_TYPES = {"实现", "依赖", "对比", "矛盾", "衍生"}

# 源标识/文件名格式的伪实体名:以日期前缀开头(YYYY-MM-DD-xxx),
# 这类名字来自 source slug / 文件名 / 素材元数据,不是工业工程概念,
# 若被 LLM 误抽成实体会生成 confidence:low 的占位空壳页污染知识库。
# 在 normalize 阶段统一剔除并清理对它们的引用。
_DIRTY_NAME_RE = re.compile(r"^\s*\d{4}-\d{1,2}-\d{1,2}-")


def is_dirty_entity_name(name: Any) -> bool:
    """判定实体名是否为源 slug/文件名格式的伪实体(应被过滤)。"""
    if not isinstance(name, str):
        return False
    return bool(_DIRTY_NAME_RE.match(name))


def _err(errors: list[str], path: str, msg: str) -> None:
    errors.append(f"{path}: {msg}")


def _check_str(val: Any, path: str, errors: list[str], *, required: bool = True, max_len: int | None = None) -> None:
    if val is None or val == "":
        if required:
            _err(errors, path, "required string is missing/empty")
        return
    if not isinstance(val, str):
        _err(errors, path, f"expected string, got {type(val).__name__}")
        return
    if max_len is not None and len(val) > max_len:
        _err(errors, path, f"string longer than {max_len} chars (got {len(val)})")


def _check_str_list(val: Any, path: str, errors: list[str], *, required: bool = False, min_items: int = 0) -> None:
    if val is None:
        if required:
            _err(errors, path, "required list is missing")
        return
    if not isinstance(val, list):
        _err(errors, path, f"expected list, got {type(val).__name__}")
        return
    if len(val) < min_items:
        _err(errors, path, f"list shorter than {min_items} items (got {len(val)})")
    for i, item in enumerate(val):
        if not isinstance(item, str):
            _err(errors, f"{path}[{i}]", f"expected string, got {type(item).__name__}")


def _check_confidence(val: Any, path: str, errors: list[str], *, required: bool = False) -> None:
    if val is None:
        if required:
            _err(errors, path, f"required confidence missing (one of {sorted(CONFIDENCE_VALUES)})")
        return
    if val not in CONFIDENCE_VALUES:
        _err(errors, path, f"invalid confidence {val!r}, expect one of {sorted(CONFIDENCE_VALUES)}")


def _check_source_summary(val: Any, errors: list[str]) -> None:
    if not isinstance(val, dict):
        _err(errors, "source_summary", f"expected object, got {type(val).__name__}")
        return
    _check_str(val.get("title"), "source_summary.title", errors)
    kind = val.get("kind")
    if kind is not None and kind not in SOURCE_KINDS:
        _err(errors, "source_summary.kind", f"invalid kind {kind!r}, expect one of {sorted(SOURCE_KINDS)}")
    _check_str_list(val.get("domain_tags"), "source_summary.domain_tags", errors, required=True, min_items=1)
    _check_str_list(val.get("key_takeaways"), "source_summary.key_takeaways", errors, required=True, min_items=1)


def _check_facet(val: Any, path: str, errors: list[str]) -> None:
    if not isinstance(val, dict):
        _err(errors, path, f"expected object, got {type(val).__name__}")
        return
    _check_str(val.get("facet"), f"{path}.facet", errors)
    _check_str(val.get("content"), f"{path}.content", errors)
    _check_str(val.get("evidence"), f"{path}.evidence", errors, required=False)


def _check_entity(val: Any, path: str, errors: list[str]) -> None:
    if not isinstance(val, dict):
        _err(errors, path, f"expected object, got {type(val).__name__}")
        return
    _check_str(val.get("name"), f"{path}.name", errors)
    kind = val.get("kind")
    if kind is not None and kind not in ENTITY_KINDS:
        _err(errors, f"{path}.kind", f"invalid kind {kind!r}, expect one of {sorted(ENTITY_KINDS)}")
    # brief 给到 600 字余量(原 400),允许 LLM 在边界处波动
    _check_str(val.get("brief"), f"{path}.brief", errors, max_len=600)
    _check_str_list(val.get("aliases"), f"{path}.aliases", errors)
    _check_str(val.get("evidence"), f"{path}.evidence", errors, required=False)
    _check_confidence(val.get("confidence"), f"{path}.confidence", errors)
    _check_str_list(val.get("domain_tags"), f"{path}.domain_tags", errors)
    # detailed_facets 是新增字段,可选(向后兼容旧 prompt),但若提供则校验结构
    facets = val.get("detailed_facets")
    if facets is not None:
        if not isinstance(facets, list):
            _err(errors, f"{path}.detailed_facets", f"expected list, got {type(facets).__name__}")
        else:
            for i, f in enumerate(facets):
                _check_facet(f, f"{path}.detailed_facets[{i}]", errors)


def _check_topic(val: Any, path: str, errors: list[str]) -> None:
    if not isinstance(val, dict):
        _err(errors, path, f"expected object, got {type(val).__name__}")
        return
    _check_str(val.get("name"), f"{path}.name", errors)
    _check_str(val.get("scope"), f"{path}.scope", errors, required=False)
    _check_str_list(val.get("related_entities"), f"{path}.related_entities", errors)
    # key_points 新增,向后兼容
    kp = val.get("key_points")
    if kp is not None:
        _check_str_list(kp, f"{path}.key_points", errors)
    _check_str(val.get("evidence"), f"{path}.evidence", errors, required=False)
    _check_confidence(val.get("confidence"), f"{path}.confidence", errors)


def _check_connection(val: Any, path: str, errors: list[str]) -> None:
    if not isinstance(val, dict):
        _err(errors, path, f"expected object, got {type(val).__name__}")
        return
    _check_str(val.get("src"), f"{path}.src", errors)
    _check_str(val.get("dst"), f"{path}.dst", errors)
    _check_str(val.get("rel_type"), f"{path}.rel_type", errors)
    _check_str(val.get("evidence"), f"{path}.evidence", errors, required=False)
    _check_confidence(val.get("confidence"), f"{path}.confidence", errors)


def _check_contradiction(val: Any, path: str, errors: list[str]) -> None:
    if not isinstance(val, dict):
        _err(errors, path, f"expected object, got {type(val).__name__}")
        return
    _check_str(val.get("with_page"), f"{path}.with_page", errors)
    _check_str(val.get("claim_old"), f"{path}.claim_old", errors)
    _check_str(val.get("claim_new"), f"{path}.claim_new", errors)
    _check_str(val.get("evidence"), f"{path}.evidence", errors, required=False)


def _check_new_vs_existing(val: Any, errors: list[str]) -> None:
    if not isinstance(val, dict):
        _err(errors, "new_vs_existing", f"expected object, got {type(val).__name__}")
        return
    _check_str_list(val.get("new_entity_names"), "new_vs_existing.new_entity_names", errors)
    _check_str_list(val.get("new_topic_names"), "new_vs_existing.new_topic_names", errors)

    matched_e = val.get("matched_entity_pages") or []
    if not isinstance(matched_e, list):
        _err(errors, "new_vs_existing.matched_entity_pages", "expected list")
    else:
        for i, item in enumerate(matched_e):
            base = f"new_vs_existing.matched_entity_pages[{i}]"
            if not isinstance(item, dict):
                _err(errors, base, "expected object")
                continue
            _check_str(item.get("name"), f"{base}.name", errors)
            _check_str(item.get("slug"), f"{base}.slug", errors)
            mk = item.get("match_kind")
            if mk is not None and mk not in {"exact", "alias", "similarity"}:
                _err(errors, f"{base}.match_kind", f"invalid match_kind {mk!r}")

    matched_t = val.get("matched_topic_pages") or []
    if not isinstance(matched_t, list):
        _err(errors, "new_vs_existing.matched_topic_pages", "expected list")
    else:
        for i, item in enumerate(matched_t):
            base = f"new_vs_existing.matched_topic_pages[{i}]"
            if not isinstance(item, dict):
                _err(errors, base, "expected object")
                continue
            _check_str(item.get("name"), f"{base}.name", errors)
            _check_str(item.get("slug"), f"{base}.slug", errors)


def validate_step1(payload: Any) -> tuple[bool, list[str]]:
    """对 Step1 LLM 输出做校验。

    返回 (ok, errors)。errors 为可读的 dotted-path 错误描述,空列表代表通过。
    """
    errors: list[str] = []

    if not isinstance(payload, dict):
        return False, [f"<root>: expected object, got {type(payload).__name__}"]

    if "source_summary" not in payload:
        _err(errors, "source_summary", "required field missing")
    else:
        _check_source_summary(payload["source_summary"], errors)

    if "new_vs_existing" not in payload:
        _err(errors, "new_vs_existing", "required field missing")
    else:
        _check_new_vs_existing(payload["new_vs_existing"], errors)

    for arr_key, item_check in (
        ("entities", _check_entity),
        ("topics", _check_topic),
        ("connections", _check_connection),
        ("contradictions", _check_contradiction),
    ):
        arr = payload.get(arr_key)
        if arr is None:
            continue  # 这些数组可缺省,等价于空列表
        if not isinstance(arr, list):
            _err(errors, arr_key, f"expected list, got {type(arr).__name__}")
            continue
        for i, item in enumerate(arr):
            item_check(item, f"{arr_key}[{i}]", errors)

    return (not errors), errors


def normalize_step1(payload: dict) -> dict:
    """把缺省的可选数组补成空列表,方便下游 step2/merger 直接消费。

    同时剔除源 slug/文件名格式的伪实体(见 is_dirty_entity_name),
    并清理 entities / new_entity_names / connections / topics.related_entities
    里对它们的悬空引用,避免生成占位空壳页污染知识库。
    """
    out = dict(payload)
    for k in ("entities", "topics", "connections", "contradictions"):
        if out.get(k) is None:
            out[k] = []

    # 收集被剔除的脏实体名(用于后续清理引用)
    dirty: set[str] = set()
    clean_entities = []
    for ent in out.get("entities", []):
        nm = ent.get("name") if isinstance(ent, dict) else None
        if is_dirty_entity_name(nm):
            dirty.add(nm)
            continue
        clean_entities.append(ent)
    out["entities"] = clean_entities

    # connections:任一端是脏实体则整条丢弃
    out["connections"] = [
        c for c in out.get("connections", [])
        if not (isinstance(c, dict) and (is_dirty_entity_name(c.get("src")) or is_dirty_entity_name(c.get("dst"))))
    ]

    # topics.related_entities:清掉指向脏实体的引用
    for tp in out.get("topics", []):
        if isinstance(tp, dict) and isinstance(tp.get("related_entities"), list):
            tp["related_entities"] = [
                r for r in tp["related_entities"] if not is_dirty_entity_name(r)
            ]

    nve = dict(out.get("new_vs_existing") or {})
    for k in ("new_entity_names", "matched_entity_pages", "new_topic_names", "matched_topic_pages"):
        if nve.get(k) is None:
            nve[k] = []
    # new_entity_names 里清掉脏名
    nve["new_entity_names"] = [
        n for n in nve["new_entity_names"] if not is_dirty_entity_name(n)
    ]
    out["new_vs_existing"] = nve
    return out
