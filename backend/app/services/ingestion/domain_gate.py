"""领域相关性闸门:拦截 step1 抽取出的「明确跨领域」实体,防止脏数据入库。

背景:step1 prompt 要求"哪怕一句话提到也建节点",LLM 会把正文案例里顺带提到的
跨领域名词(如某汽车公司案例里的"六位一体体验式营销"营销概念)也抽成实体。这些
domain_tags 全部落在 IE 学科领域之外,却照样建页、进图谱,成为脏数据/灰色节点。

设计原则(保守,宁可放过不可错杀):
  - 只拦截 domain_tags **全部** off-topic 的实体;任一 tag 命中白名单即保留。
  - 命中白名单用「子串包含」双向匹配(白名单词出现在 tag 里,或 tag 出现在白名单词里),
    容忍"生产管理"vs"生产率管理"这类表述差异。
  - 拦截的实体不静默丢弃 → 记 audit_logs(可人工召回),并打日志。
  - 白名单可经 config_store 键 `ingestion.domain_whitelist`(逗号分隔)覆盖,默认内置 IE 领域集。
"""

from __future__ import annotations

from loguru import logger

# IE 学科领域白名单(默认)。来自 purpose.md「在范围内」+ 常见 IE 分支领域。
# 任一 entity.domain_tags 命中其一即视为在范围内。
DEFAULT_DOMAIN_WHITELIST: list[str] = [
    "工业工程", "IE", "生产", "制造", "运营", "运作", "质量", "精益",
    "人因", "工效", "设施规划", "物流", "供应链", "运筹", "系统工程",
    "工程经济", "管理科学", "工业管理", "标准化", "作业", "工作研究",
    "生产率", "效率", "成本", "科学管理", "六西格玛", "约束理论",
    "智能制造", "工业4.0", "数字化", "信息系统", "工程", "技术",
    "统计", "优化", "决策", "项目管理", "安全", "职业卫生",
]


def _load_whitelist() -> list[str]:
    """白名单:config_store 覆盖优先,否则用内置默认。"""
    try:
        from app.services.admin import config_store

        raw = config_store.get_sync("ingestion.domain_whitelist", default="")
        if raw:
            items = [x.strip() for x in raw.split(",") if x.strip()]
            if items:
                return items
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_DOMAIN_WHITELIST


def _tag_in_scope(tag: str, whitelist: list[str]) -> bool:
    """单个 domain_tag 是否在范围内(双向子串包含,容忍表述差异)。"""
    t = (tag or "").strip().lower()
    if not t:
        return False
    for w in whitelist:
        wl = w.lower()
        if wl in t or t in wl:
            return True
    return False


def is_entity_off_topic(entity: dict, whitelist: list[str] | None = None) -> bool:
    """实体是否「明确跨领域」:domain_tags 非空且全部不在白名单内。

    domain_tags 为空 → 无法判定,保守保留(返回 False)。
    """
    if whitelist is None:
        whitelist = _load_whitelist()
    tags = entity.get("domain_tags") or []
    tags = [t for t in tags if isinstance(t, str) and t.strip()]
    if not tags:
        return False  # 无标签不拦
    return all(not _tag_in_scope(t, whitelist) for t in tags)


def filter_off_topic_entities(payload: dict) -> dict:
    """从 step1 payload 中剔除明确跨领域的实体,返回统计。

    就地修改 payload['entities'],被剔除的实体名记入返回的 'dropped' 列表
    (调用方负责写 audit_logs)。
    """
    whitelist = _load_whitelist()
    entities = payload.get("entities") or []
    kept: list[dict] = []
    dropped: list[dict] = []
    for ent in entities:
        if isinstance(ent, dict) and is_entity_off_topic(ent, whitelist):
            dropped.append({
                "name": ent.get("name"),
                "domain_tags": ent.get("domain_tags"),
            })
        else:
            kept.append(ent)

    if dropped:
        payload["entities"] = kept
        names = [d["name"] for d in dropped]
        logger.info(
            f"[domain-gate] 拦截 {len(dropped)} 个跨领域实体(domain_tags 全部 off-topic): {names}"
        )

    return {"kept": len(kept), "dropped": dropped}
