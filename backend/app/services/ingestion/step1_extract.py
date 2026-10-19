"""ingestion/step1_extract.py:Step1 结构化分析。

流程:
  1. 渲染 prompt(prompts/ingest_step1.md + purpose / index_summary / wiki_structure_summary / raw_md)
  2. 调 LLM(默认非流式;消耗 1 次调用)
  3. 解析 JSON(允许 ```json 围栏 / 前后多余文字 / 头尾换行)
  4. validate_step1 校验;失败时重试 1 次(prompt 加严格性提醒)
  5. 仍失败 → 抛 ValidationError(上层 pipeline 把 job 置 failed_step1 并 audit_logs)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.exceptions import ValidationError
from app.services.ingestion.validator import normalize_step1, validate_step1
from loguru import logger

from app.services.llm import ChatMessage, get_llm_provider
from app.services.llm.base import EXTRACTION_MAX_TOKENS

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
STEP1_TEMPLATE = (PROMPTS_DIR / "ingest_step1.md").read_text(encoding="utf-8")

# raw_md 截断阈值。默认 1_000_000(1M 字符,匹配 DeepSeek-V4-Pro 的 1M token context),
# 不再让用户配置 — 模型配置一次后自动按其 context 长度发挥最大能力。
DEFAULT_MAX_RAW_MD_CHARS = 1_000_000


def _read_max_raw_md_chars() -> int:
    """允许通过环境变量 / config_store 强制覆盖,但默认就是 1M,普通用户无需关心。"""
    try:
        from app.services.admin import config_store

        v = config_store.get_sync("ingestion.max_raw_md_chars", default="")
        return int(v) if v else DEFAULT_MAX_RAW_MD_CHARS
    except Exception:  # noqa: BLE001
        return DEFAULT_MAX_RAW_MD_CHARS

_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def _render_prompt(
    *,
    raw_md: str,
    purpose_text: str,
    index_summary: str,
    wiki_structure_summary: str,
) -> str:
    return (
        STEP1_TEMPLATE
        .replace("{purpose_text}", purpose_text or "(未设置)")
        .replace("{index_summary}", index_summary or "(空)")
        .replace("{wiki_structure_summary}", wiki_structure_summary or "(空)")
        .replace("{raw_md}", raw_md)
    )


def _extract_json_object(text: str) -> str:
    """从 LLM 响应中抠出第一个完整 JSON 对象。

    支持以下几种返回格式:
      - 纯 JSON
      - ```json ... ``` 围栏
      - 头尾有解释文字 + 中间一段 {...}
    """
    # 1) 首选 ```json ... ``` 围栏
    m = _FENCE_PATTERN.search(text)
    if m:
        return m.group(1).strip()

    # 2) 首尾扫第一个 { 与最后一个 } 之间的子串
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    return text.strip()


def _escape_control_chars_in_strings(s: str) -> str:
    """把 JSON 字符串字面量内部裸露的控制字符转义为合法形式。

    LLM 常把多行文本直接塞进 JSON 字符串值里(真实换行 / 制表符),
    而 JSON 规范要求字符串内 < 0x20 的字符必须转义(\\n / \\t / \\uXXXX)。
    `json.loads` 对此报 "Invalid control character"。

    这里用一个最小状态机:逐字符扫描,只在「字符串内部」把裸控制字符替换为
    转义形式,字符串之外的结构字符(对象/数组的换行缩进)保持原样,不影响解析。
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in s:
        if in_string:
            if escaped:
                out.append(ch)
                escaped = False
                continue
            if ch == "\\":
                out.append(ch)
                escaped = True
                continue
            if ch == '"':
                out.append(ch)
                in_string = False
                continue
            if ch < "\x20":  # 裸控制字符 → 转义
                if ch == "\n":
                    out.append("\\n")
                elif ch == "\t":
                    out.append("\\t")
                elif ch == "\r":
                    out.append("\\r")
                else:
                    out.append(f"\\u{ord(ch):04x}")
                continue
            out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
    return "".join(out)


def _loads_lenient(raw_json: str) -> dict:
    """`json.loads` 的容错版,三级兜底:
      1. 直接 json.loads
      2. 失败若因裸控制字符 → 转义后重试
      3. 仍失败 → json_repair 修复(截断/缺逗号/未闭合括号,LLM 高频错误)

    DeepSeek 等模型一次性吐整章 JSON 时,常见两类坏法:输出撞 max_tokens 被截断、
    或字符串拼接漏逗号。前两级修不了这两类,第三级用 json_repair 尽力补全。
    """
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError as e:
        if "control character" in str(e).lower():
            try:
                return json.loads(_escape_control_chars_in_strings(raw_json))
            except json.JSONDecodeError:
                pass
        # 最后兜底:json_repair 容忍截断 / 缺逗号 / 未闭合括号
        from json_repair import repair_json

        repaired = repair_json(raw_json, return_objects=True)
        if isinstance(repaired, dict):
            return repaired
        raise


# 已知 LLM 误用映射:把"非法 kind"→"最近的合法 kind"。
# 维护原则:遇到新模式才加一行,不要为了"兜底"无限扩张(会让真错误被静默掩盖)。
_KIND_ALIAS = {
    "case": "concept",       # 案例不是实体类型,降级为 concept
    "example": "concept",
    "framework": "concept",  # 框架/模型 → concept
    "model": "concept",
    "principle": "concept",
    "company": "org",
    "enterprise": "org",
    "indicator": "metric",
    "kpi": "metric",
}


def _coerce_invalid_entity_kinds(payload: dict) -> None:
    """LLM 偶发把 entity.kind 写成不在 schema 枚举里的值(如 'case')。

    在 validate_step1 之前做一次轻量映射,避免 attempt=2 重跑浪费 150-200 秒。
    映射不到的非法值保持原样,会被 validator 抓到并触发重试。
    """
    from app.services.ingestion.validator import ENTITY_KINDS

    for ent in payload.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        kind = ent.get("kind")
        if isinstance(kind, str) and kind not in ENTITY_KINDS:
            mapped = _KIND_ALIAS.get(kind.lower().strip())
            if mapped:
                ent["kind"] = mapped


def _sanitize_payload(payload: dict) -> None:
    """validate 之前的自愈清洗:丢弃 LLM 高频产出的「半成品」碎片,而不是让整篇失败。

    背景:一次 228s 的完整抽取(56k 字符、含全部实体与 new_vs_existing)曾因
    单个 detailed_facets[*].content 为空被整篇判废,attempt=2 重跑反而被截断丢了
    new_vs_existing。这些碎片下游只是拼进 prompt,空条目无害 —— 该丢弃而非致命。

    清洗项:
      1. detailed_facets 中 facet 或 content 为空的条目直接剔除(空 facet 列表保留为 [])。
      2. 顶层缺 new_vs_existing → 补一个空骨架(下游语义 = 全部视为新增,安全)。
    """
    for ent in payload.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        facets = ent.get("detailed_facets")
        if isinstance(facets, list):
            ent["detailed_facets"] = [
                f
                for f in facets
                if isinstance(f, dict)
                and isinstance(f.get("facet"), str)
                and f.get("facet").strip()
                and isinstance(f.get("content"), str)
                and f.get("content").strip()
            ]

    if "new_vs_existing" not in payload or not isinstance(payload.get("new_vs_existing"), dict):
        payload["new_vs_existing"] = {
            "new_entity_names": [],
            "matched_entity_pages": [],
            "new_topic_names": [],
            "matched_topic_pages": [],
        }


def _warn_suspected_truncation(payload: dict) -> None:
    """疑似截断告警:抽到实体/主题但 new_vs_existing 四表全空 → 输出大概率被 token 上限截断。

    截断会丢尾部字段(connections / contradictions / new_vs_existing)。只告警不阻断:
    step2 已保证即使 new_vs_existing 为空也会深度生成全部实体/主题。
    """
    ents = payload.get("entities") or []
    topics = payload.get("topics") or []
    if not ents and not topics:
        return
    nve = payload.get("new_vs_existing") or {}
    lists = [
        nve.get("new_entity_names") or [],
        nve.get("matched_entity_pages") or [],
        nve.get("new_topic_names") or [],
        nve.get("matched_topic_pages") or [],
    ]
    if all(not x for x in lists):
        conns = len(payload.get("connections") or [])
        logger.warning(
            f"[ingest-trace] step1 疑似被截断:抽到 {len(ents)} 实体 / {len(topics)} 主题,"
            f"但 new_vs_existing 四表全空,connections={conns} — 请检查输出上限与原文长度"
        )


async def step1_extract(
    *,
    raw_md: str,
    purpose_text: str,
    index_summary: str,
    wiki_structure_summary: str,
) -> dict:
    """单次 LLM 调用 + JSON schema 校验 + 失败重试 1 次。

    Returns:
        通过校验的 Step1 dict(已 normalize_step1 补齐空数组)。

    Raises:
        ValidationError:LLM 输出两次都校验失败。
    """
    max_chars = _read_max_raw_md_chars()
    prompt = _render_prompt(
        raw_md=raw_md[:max_chars],
        purpose_text=purpose_text,
        index_summary=index_summary,
        wiki_structure_summary=wiki_structure_summary,
    )
    provider = get_llm_provider()

    last_errors: list[str] = []
    last_raw: str = ""
    for attempt in (1, 2):
        suffix = ""
        if attempt == 2:
            suffix = (
                "\n\n## 校验失败提醒\n\n"
                "上一次输出未通过 JSON schema 校验,失败原因:\n"
                + "\n".join(f"- {e}" for e in last_errors[:8])
                + "\n\n请严格按上面 schema 重新输出合法 JSON,不要任何前后文字、不要围栏。"
            )

        messages = [
            ChatMessage(
                role="system",
                content="你是严格输出合法 JSON 的助手,不要解释、不要 markdown 围栏。",
            ),
            ChatMessage(role="user", content=prompt + suffix),
        ]
        # Step1 输出 JSON 可能很长(实体/主题/连接列表),不设 max_tokens 上限:
        # 由 provider 走 DB 默认(=不发送 max_tokens,服务端用模型默认最大输出),
        # 避免深度抽取的长 JSON 被人为截断丢失尾部字段。
        import time as _t
        from loguru import logger as _logger
        _t0 = _t.perf_counter()
        # 结构化抽取依赖模型推理,显式恢复(reasoning_effort=None 移除默认的 none)
        raw = await provider.chat(messages, stream=False, reasoning_effort=None, max_tokens=EXTRACTION_MAX_TOKENS)
        _dt = _t.perf_counter() - _t0
        last_raw = raw if isinstance(raw, str) else ""
        _logger.info(f"[ingest-trace] step1_extract attempt={attempt} done: {_dt:.2f}s, out_chars={len(last_raw)}")
        try:
            payload = _loads_lenient(_extract_json_object(last_raw))
        except json.JSONDecodeError as e:
            last_errors = [f"<root>: JSON 解析失败 ({e})"]
            _logger.warning(
                f"[ingest-trace] step1_extract attempt={attempt} JSON 解析失败: {e}; "
                f"输出头 200: {last_raw[:200]!r}"
            )
            continue

        # 轻量预清洗:LLM 反复犯的小错(把"案例"实体 kind 写成 'case'),静默修成 concept,
        # 避免触发 schema 校验失败 → attempt=2 浪费 150-200 秒。
        _coerce_invalid_entity_kinds(payload)
        # 自愈清洗:丢弃空 facet 碎片、补齐缺失的 new_vs_existing,
        # 避免一篇完整抽取因单个空字段被整篇判废。
        _sanitize_payload(payload)
        # 疑似截断告警(不阻断,见 helper docstring)
        _warn_suspected_truncation(payload)

        ok, errors = validate_step1(payload)
        if ok:
            normalized = normalize_step1(payload)
            # 领域闸门:剔除明确跨领域(domain_tags 全部 off-topic)的实体,防脏数据入库。
            # 保守策略,只拦全部 off-topic 的,任一 tag 在 IE 范围内则保留。
            try:
                from app.services.ingestion.domain_gate import filter_off_topic_entities

                gate = filter_off_topic_entities(normalized)
                if gate["dropped"]:
                    _logger.info(
                        f"[ingest-trace] domain-gate dropped {len(gate['dropped'])} off-topic entities, "
                        f"kept {gate['kept']}"
                    )
            except Exception as e:  # noqa: BLE001
                _logger.warning(f"[ingest-trace] domain-gate skipped (non-blocking): {e}")
            return normalized
        last_errors = errors
        _logger.warning(
            f"[ingest-trace] step1_extract attempt={attempt} schema 校验失败 "
            f"({len(errors)} errors): "
            + "; ".join(errors[:5])
            + (f"; …还有 {len(errors)-5} 条" if len(errors) > 5 else "")
        )

    raise ValidationError(
        "Step1 LLM 输出两次都未通过 schema 校验:\n"
        + "\n".join(f"- {e}" for e in last_errors[:8])
        + f"\n\n原始输出片段:{last_raw[:300]}"
    )
