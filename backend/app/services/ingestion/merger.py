"""ingestion/merger.py:跨 source 实体合并 + frontmatter merge + body 段追加。

按规划文档 §3.2 设计:
  1. 字面 slug 命中 → 视为同一节点(merged)
  2. 别名词表命中 → 视为同一节点(merged_via_alias)
  3. 否则:embedding 余弦比对现有实体页 brief
     - ≥ auto_merge_threshold → 自动合并(merged_via_similarity)
     - ≥ suggest_threshold → 候选合并(suggested_merge,P5 入 gap-queue)
     - 否则 → 新建(created)
  4. embedding 不可用(provider 未配置 / 网络失败) → 全部降级新建,记 audit_logs

`apply_observation_block` 把"## 不同素材中的观点 / ### 来源:[[X]]" 段追加到已有实体页正文末尾,
不动 frontmatter 之外的其他段(简介 / 核心要点等)。
"""

from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Literal

from loguru import logger

from app.core.exceptions import ProviderError
from app.services.embedding import get_embedding_provider
from app.services.wiki.frontmatter_io import merge_frontmatter, parse_text, serialize
from app.services.wiki.fs_io import safe_join
from app.services.llm.base import EXTRACTION_MAX_TOKENS

DEFAULT_AUTO_MERGE = 0.92
DEFAULT_SUGGEST_MERGE = 0.75

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
_MERGE_ENTITY_TEMPLATE = _PROMPTS_DIR / "ingest_merge_entity.md"

MergeAction = Literal[
    "merged",                # 字面 slug 命中
    "merged_via_alias",      # 别名词表命中
    "merged_via_similarity", # 余弦 ≥ 0.92
    "suggested_merge",       # 0.75 ≤ 余弦 < 0.92
    "created",               # 新建
    "created_no_embedding",  # embedding 不可用降级新建
]


def cosine(a: list[float], b: list[float]) -> float:
    """计算两向量余弦相似度;长度不一致或全零返回 0.0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def existing_entity_slugs(wiki_root: Path) -> list[str]:
    """枚举 wiki/entities/ 下的所有实体页 slug(不含扩展名)。"""
    p = Path(wiki_root) / "wiki" / "entities"
    if not p.exists():
        return []
    return sorted([f.stem for f in p.glob("*.md") if f.is_file()])


def find_alias_match(name: str, *, alias_groups: list[list[str]]) -> str | None:
    """在别名词表里查 name → 返回该 group 的 canonical(group[0]),没命中返 None。"""
    for group in alias_groups:
        if not group:
            continue
        if name in group:
            return group[0]
    return None


def _load_entity_brief(wiki_root: Path, slug: str) -> str | None:
    """读 wiki/entities/{slug}.md 的 brief(简介段)— 用于相似度比对。"""
    p = safe_join(Path(wiki_root), "wiki", "entities", f"{slug}.md")
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    fm, body = parse_text(text)
    # 优先 frontmatter 自带 brief 字段
    if fm.get("brief"):
        return str(fm["brief"])
    # 否则取 "## 简介" 下的第一段
    lines = body.splitlines()
    in_brief = False
    buf: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("## 简介"):
            in_brief = True
            continue
        if in_brief:
            if s.startswith("## "):
                break
            if s and not s.startswith("<!--"):
                buf.append(s)
            elif buf:
                break
    return " ".join(buf) if buf else fm.get("title")


async def merge_or_create_entity(
    *,
    candidate_name: str,
    candidate_brief: str,
    wiki_root: Path,
    alias_groups: list[list[str]] | None = None,
    auto_merge_threshold: float = DEFAULT_AUTO_MERGE,
    suggest_threshold: float = DEFAULT_SUGGEST_MERGE,
) -> tuple[str, MergeAction, dict]:
    """判断候选实体应该合并到哪个 slug 还是新建。

    Returns:
        (slug, action, meta)
        - slug: 最终落到的 wiki/entities/{slug}.md 中的 slug
        - action: MergeAction 之一
        - meta: 附加信息,如 cosine_score / matched_against / alias_group_canonical
    """
    alias_groups = alias_groups or []
    existing = existing_entity_slugs(wiki_root)
    existing_set = set(existing)

    # 1) 字面命中
    if candidate_name in existing_set:
        return candidate_name, "merged", {"matched_via": "exact_slug"}

    # 2) 别名词表命中
    alias_canonical = find_alias_match(candidate_name, alias_groups=alias_groups)
    if alias_canonical and alias_canonical in existing_set:
        return alias_canonical, "merged_via_alias", {
            "matched_via": "alias",
            "alias_group_canonical": alias_canonical,
        }
    if alias_canonical:
        # 别名指向 canonical,但 canonical 还没建页 → 用 canonical 新建
        return alias_canonical, "created", {
            "matched_via": "alias_new",
            "alias_group_canonical": alias_canonical,
        }

    # 3) embedding 余弦比对
    if not existing or not candidate_brief.strip():
        return candidate_name, "created", {"matched_via": "no_existing_or_brief"}

    try:
        provider = get_embedding_provider()
        # 候选 + 所有现有实体的 brief 一次性 embed
        briefs: list[str] = []
        slugs_for_brief: list[str] = []
        for slug in existing:
            brief = _load_entity_brief(wiki_root, slug)
            if brief:
                briefs.append(brief)
                slugs_for_brief.append(slug)
        if not briefs:
            return candidate_name, "created", {"matched_via": "existing_have_no_brief"}

        _t0 = time.perf_counter()
        vectors = await provider.embed([candidate_brief, *briefs])
        logger.info(
            f"[ingest-trace] merge_embed name={candidate_name!r} "
            f"n_briefs={len(briefs)} took={time.perf_counter() - _t0:.2f}s"
        )
        cand_vec = vectors[0]
        scores = [(slugs_for_brief[i], cosine(cand_vec, vectors[i + 1])) for i in range(len(briefs))]
    except (ProviderError, ValueError, RuntimeError) as e:
        # 质量优先(2026-06-11):embedding 不可用会让相似度判重失效 → 同义实体被当新
        # 实体重复建页,污染图谱。不再静默降级新建,直接硬失败让整篇 job 失败。
        from app.core.exceptions import IngestQualityError

        logger.warning(f"merger: embedding 不可用,判重无法进行,整篇失败。reason={e}")
        raise IngestQualityError(
            f"embedding 服务不可用,无法对实体 {candidate_name!r} 做相似度判重: {e}"
        ) from e

    best_slug, best_score = max(scores, key=lambda x: x[1]) if scores else (None, 0.0)
    meta = {"cosine": round(best_score, 4), "matched_against": best_slug}

    if best_slug and best_score >= auto_merge_threshold:
        return best_slug, "merged_via_similarity", meta
    if best_slug and best_score >= suggest_threshold:
        # 仍新建,但记 suggested(后续 P5 把这条写入 gap-queue.merge_suggestion)
        meta["note"] = "suggested_merge_recorded"
        return candidate_name, "suggested_merge", meta
    return candidate_name, "created", meta


def distill_observation(new_body: str, *, max_chars: int = 9000) -> str:
    """把新生成的实体/主题正文压成「叠加」用的观察块(保留全文要点,而非 300 字摘要)。

    Req3 叠加增强:跨素材合并时,旧实现只取新页 ## 简介 首段(≤300 字),
    导致后续素材的深度内容丢失。这里改为保留新页的全部实质章节正文:

      - 丢弃顶部 H1 与 frontmatter(parse_text 已剥离 fm)
      - 丢弃 boilerplate 段:参考来源 / 不同素材中的观点(避免观点段套娃)
      - H2 (## x) → H4 (#### x),H3 (### x) → H5,使其安全嵌套在
        "### 来源:[[slug]]" 这一 H3 子段之下,不破坏页面大纲层级
      - 超长时按段截断到 max_chars(在段边界切,尾部加省略标记)

    返回去掉首尾空白的 markdown 片段。空输入回退到原文首段。
    """
    if not new_body or not new_body.strip():
        return ""

    drop_h2 = ("参考来源", "不同素材中的观点", "参考资料", "参考文献")
    out_lines: list[str] = []
    skipping = False
    for raw in new_body.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        # H1:整页标题,丢
        if stripped.startswith("# ") and not stripped.startswith("## "):
            continue
        # H2:判断是否 boilerplate 段;命中则进入 skip 直到下一个 H2
        if stripped.startswith("## ") and not stripped.startswith("### "):
            title = stripped[3:].strip()
            if any(title.startswith(d) for d in drop_h2):
                skipping = True
                continue
            skipping = False
            out_lines.append("#### " + title)  # H2 → H4
            continue
        if skipping:
            continue
        # H3 → H5(在 boilerplate 之外才会到这)
        if stripped.startswith("### "):
            out_lines.append("##### " + stripped[4:].strip())
            continue
        out_lines.append(line)

    text = "\n".join(out_lines).strip()
    # 折叠 3+ 连续空行为 1 个空行
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")

    if not text:
        # 全是 boilerplate/标题 → 退回首段非标题文本
        for para in new_body.split("\n\n"):
            p = para.strip()
            if p and not p.startswith("#"):
                text = p
                break

    if len(text) > max_chars:
        cut = text[:max_chars]
        # 回退到最后一个段落边界,避免句子被截一半
        nl = cut.rfind("\n\n")
        if nl > max_chars * 0.5:
            cut = cut[:nl]
        text = cut.rstrip() + "\n\n_(内容较长,已截断;完整内容见该素材原页)_"
    return text


def apply_observation_block(
    existing_text: str,
    *,
    source_slug: str,
    new_observation: str,
    contradicts_with: list[str] | None = None,
) -> str:
    """把"## 不同素材中的观点 / ### 来源:[[X]]" 段追加到已有页正文末尾。

    - 若已存在同名 ### 来源:[[source_slug]] 子段,则覆盖该子段;否则新增
    - 若该页已存在 "## 不同素材中的观点" 段,只追加新子段
    - 否则在正文末尾新增整段
    - contradicts_with 不为空时,在新观察上方加 `> ⚠ 与来源 ...` blockquote
    """
    fm, body = parse_text(existing_text)

    contra_block = ""
    if contradicts_with:
        notes = "、".join(f"[[{s}]]" for s in contradicts_with)
        contra_block = f"\n> ⚠ 与来源 {notes} 的主张可能冲突\n\n"

    new_subsection = (
        f"### 来源:[[{source_slug}]]\n\n"
        f"{contra_block}{new_observation.strip()}\n"
    )

    section_marker = "## 不同素材中的观点"
    sub_marker = f"### 来源:[[{source_slug}]]"

    body_stripped = body.rstrip()
    if section_marker in body_stripped:
        if sub_marker in body_stripped:
            # 覆盖现有子段:替换从 sub_marker 到下一个 ### / ## 之间的内容
            new_body = _replace_subsection(body_stripped, sub_marker, new_subsection)
        else:
            # 在 "## 不同素材中的观点" 段末尾追加子段
            new_body = _append_subsection(body_stripped, section_marker, new_subsection)
    else:
        new_body = body_stripped.rstrip() + "\n\n## 不同素材中的观点\n\n" + new_subsection

    return serialize(fm, new_body)


def _strip_md_fence(md: str) -> str:
    """剥掉 LLM 偶发包裹的 ```markdown 围栏 + 首尾空白。"""
    s = md.strip()
    s = re.sub(r"^```(?:markdown|md)?\s*\n", "", s)
    s = re.sub(r"\n```\s*$", "", s)
    return s.strip()


def _unify_llm_timeout() -> float:
    """unify 融合 LLM 调用单次超时(秒)。可经 config_store 键 ingestion.unify_llm_timeout 覆盖,默认 600s。

    600s 与 openai_compat 的 read timeout 对齐:deepseek 融合两份长素材常需 3-5 分钟,
    旧默认 120s 会让大量调用压线超时 → 工作研究等多素材实体无法融合。
    """
    try:
        from app.services.admin import config_store

        raw = config_store.get_sync("ingestion.unify_llm_timeout", default="")
        if raw:
            v = float(raw)
            if v > 0:
                return v
    except Exception:  # noqa: BLE001
        pass
    return 600.0


def _unify_max_retries() -> int:
    """unify 融合失败重试次数(不含首次)。可经 config_store 键 ingestion.unify_max_retries 覆盖,默认 2。"""
    try:
        from app.services.admin import config_store

        raw = config_store.get_sync("ingestion.unify_max_retries", default="")
        if raw:
            v = int(float(raw))
            if v >= 0:
                return v
    except Exception:  # noqa: BLE001
        pass
    return 2


async def unify_entity_pages(
    *,
    existing_text: str,
    incoming_text: str,
    entity_title: str,
    entity_names_block: str = "",
) -> str:
    """把"已有实体页"与"本次新素材实体页"用 LLM 融合成一份统一页(单一定义)。

    Req(2026-06-06):跨素材合并把两份内容综合成**一个定义、一套章节**,而不是把
    第二份素材堆成"## 不同素材中的观点 / ### 来源:[[X]]"并列子段。

    质量优先(2026-06-11):本函数要么返回合法的融合整页 markdown(含 frontmatter),
    要么在重试耗尽后抛 IngestQualityError —— **不再返回 None / 回退追加策略**。
    调用方据此让整篇文档 job 失败,杜绝半成品落盘。
    """
    import asyncio

    from app.core.exceptions import IngestQualityError
    from app.services.llm import ChatMessage, get_llm_provider

    if not _MERGE_ENTITY_TEMPLATE.exists():
        raise IngestQualityError(
            f"实体融合模板缺失({_MERGE_ENTITY_TEMPLATE.name}),无法融合 title={entity_title!r}"
        )
    template = _MERGE_ENTITY_TEMPLATE.read_text(encoding="utf-8")
    prompt = (
        template
        .replace("{entity_title}", entity_title)
        .replace("{entity_names_block}", entity_names_block or "(无白名单,正文不要新增任何双链)")
        .replace("{existing_page}", existing_text)
        .replace("{incoming_page}", incoming_text)
    )
    messages = [
        ChatMessage(
            role="system",
            content="你是工业工程领域的知识工程师,擅长把同一概念的多份素材融合成一份连贯、无重复、单一定义的 wiki 页。严格按要求输出,不编造,不丢信息。",
        ),
        ChatMessage(role="user", content=prompt),
    ]

    timeout = float(_unify_llm_timeout())
    max_retries = _unify_max_retries()
    last_reason = ""
    for attempt in range(max_retries + 1):
        try:
            provider = get_llm_provider()
            raw = await asyncio.wait_for(
                # 实体合并判断依赖模型推理,显式恢复(reasoning_effort=None 移除默认的 none)
                provider.chat(messages, stream=False, reasoning_effort=None, max_tokens=EXTRACTION_MAX_TOKENS),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            last_reason = f"LLM 调用超时(>{timeout}s)"
            logger.warning(
                f"merger: 统一融合超时(第{attempt + 1}次) title={entity_title!r} ({last_reason})"
            )
            continue
        except (ProviderError, ValueError, RuntimeError) as e:
            last_reason = f"LLM 调用失败: {type(e).__name__}: {e}"
            logger.warning(
                f"merger: 统一融合失败(第{attempt + 1}次) title={entity_title!r} ({last_reason})"
            )
            continue

        md = _strip_md_fence(raw if isinstance(raw, str) else "")
        if not md or "---" not in md:
            last_reason = "输出无 frontmatter"
            logger.warning(f"merger: 统一融合输出非法(第{attempt + 1}次): {last_reason}")
            continue
        try:
            fm, _body = parse_text(md)
        except Exception as e:  # noqa: BLE001
            last_reason = f"frontmatter 解析失败: {e}"
            logger.warning(f"merger: 统一融合输出非法(第{attempt + 1}次): {last_reason}")
            continue
        if not fm.get("title"):
            last_reason = "输出缺 title"
            logger.warning(f"merger: 统一融合输出非法(第{attempt + 1}次): {last_reason}")
            continue
        # 质量硬卡点(2026-06-11):prompt 明令「融合成单一定义、删掉『## 不同素材中的观点』段」,
        # 但 LLM 在长输入/多素材时偶尔仍吐出该堆叠段(把某份素材并列另起一段而非真融合)。
        # 仅靠 prompt 软约束兜不住,这里把「禁止堆叠」编码为硬校验:命中即判不合格 → 重试 →
        # 耗尽则抛 IngestQualityError 让整篇失败,杜绝堆叠页落盘。
        if "## 不同素材中的观点" in _body:
            last_reason = "输出含被禁的『## 不同素材中的观点』堆叠段(未真正融合成单一定义)"
            logger.warning(f"merger: 统一融合输出非法(第{attempt + 1}次): {last_reason}")
            continue
        return md


    raise IngestQualityError(
        f"实体融合在 {max_retries + 1} 次尝试后仍失败 title={entity_title!r}: {last_reason}"
    )


def _split_sections(body: str) -> list[tuple[str, int, int]]:
    """把 body 按 H2/H3 标题切片,返回 [(line, start_pos, end_pos), ...]。

    用于在 H2 内插入或替换 H3 子段。
    """
    lines = body.splitlines(keepends=True)
    positions: list[int] = []
    pos = 0
    for line in lines:
        positions.append(pos)
        pos += len(line)
    positions.append(pos)
    return [(lines[i], positions[i], positions[i + 1]) for i in range(len(lines))]


def _append_subsection(body: str, h2_marker: str, new_subsection: str) -> str:
    """在 H2 段的末尾追加一个 H3 子段。"""
    h2_pos = body.find(h2_marker)
    next_h2_pos = body.find("\n## ", h2_pos + len(h2_marker))
    insert_pos = next_h2_pos if next_h2_pos != -1 else len(body)
    head = body[:insert_pos].rstrip()
    tail = body[insert_pos:]
    return head + "\n\n" + new_subsection.rstrip() + "\n\n" + tail.lstrip()


def _replace_subsection(body: str, h3_marker: str, new_subsection: str) -> str:
    """替换以 h3_marker 开始的 H3 子段,直到下一个 H2/H3 或 EOF。"""
    start = body.find(h3_marker)
    if start == -1:
        return body
    # 找下一个 H2/H3
    after = body[start + len(h3_marker):]
    next_h3 = after.find("\n### ")
    next_h2 = after.find("\n## ")
    candidates = [n for n in (next_h3, next_h2) if n != -1]
    next_pos = min(candidates) if candidates else -1
    end = start + len(h3_marker) + next_pos if next_pos != -1 else len(body)
    head = body[:start].rstrip()
    tail = body[end:]
    return head + "\n\n" + new_subsection.rstrip() + "\n\n" + tail.lstrip()


# ---------------------------------------------------------------------------
# 别名词表读取(从 .wiki-schema.md 解析)
# ---------------------------------------------------------------------------

def parse_alias_groups_from_schema(schema_text: str) -> list[list[str]]:
    """从 .wiki-schema.md 的 "## 别名词表" 段解析出别名组。

    规则:每行 `- 工效学 = 人因工程 = ergonomics` → ["工效学", "人因工程", "ergonomics"]
    """
    groups: list[list[str]] = []
    in_section = False
    for raw_line in schema_text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            in_section = line == "## 别名词表"
            continue
        if not in_section:
            continue
        if not line.startswith("- "):
            continue
        items = [seg.strip() for seg in line[2:].split("=")]
        items = [it for it in items if it]
        if len(items) >= 2:
            groups.append(items)
    return groups


def load_alias_groups(wiki_root: Path) -> list[list[str]]:
    """从 wiki_root/.wiki-schema.md 加载别名组;文件不存在时返回空。"""
    p = Path(wiki_root) / ".wiki-schema.md"
    if not p.exists():
        return []
    return parse_alias_groups_from_schema(p.read_text(encoding="utf-8"))
