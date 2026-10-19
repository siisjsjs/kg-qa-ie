"""admin/schema_editor.py:.wiki-schema.md 的解析与回写。

简单状态机解析:按 H2 切段(主题 / 语言 / 别名词表 / 关系类型词汇表 / 素材类型路由 / 成长配置)

P5+ 增加 auto_fill_from_step1:从 ingest 抽取结果增量回写别名 / 关系类型,
带 `pending: yes` 标记 + 来源页注释,前端 SchemaEditor 只做审核(accept/reject)。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.services.wiki.fs_io import atomic_write

SCHEMA_FILE = ".wiki-schema.md"

_H2_PATTERN = re.compile(r"^##\s+(.+?)\s*$")


def _split_h2_sections(text: str) -> dict[str, str]:
    """切 H2 段:返回 {标题: 段落 body 文本}。frontmatter 之上 / H1 都丢弃。"""
    out: dict[str, str] = {}
    cur_title: str | None = None
    buf: list[str] = []
    in_body = False
    for line in text.splitlines():
        if line.startswith("---"):
            in_body = not in_body
            continue
        if not in_body and not out and cur_title is None:
            # 还没进 body
            pass
        m = _H2_PATTERN.match(line)
        if m:
            if cur_title is not None:
                out[cur_title] = "\n".join(buf).strip()
            cur_title = m.group(1).strip()
            buf = []
        else:
            if cur_title is not None:
                buf.append(line)
    if cur_title is not None:
        out[cur_title] = "\n".join(buf).strip()
    return out


def _parse_aliases(section: str) -> list[dict]:
    """`- A = B = C` → {canonical: A, members: [A,B,C], pending: bool, sources: [slug]}

    支持两种格式:
      - 已采纳:`- 工效学 = 人因工程 = ergonomics`
      - 待审核:`- 工效学 = 人因工程 = ergonomics  <!-- pending source=2026-05-19-工效学 -->`
    """
    out: list[dict] = []
    for raw in section.splitlines():
        s = raw.strip()
        if not s.startswith("- "):
            continue
        # 抽出尾部的 <!-- pending source=... --> 注释
        pending = False
        sources: list[str] = []
        comment_match = re.search(r"<!--\s*(pending(?:\s+source=([^\s>]+))?)\s*-->", s)
        if comment_match:
            pending = True
            if comment_match.group(2):
                sources = [seg for seg in comment_match.group(2).split(",") if seg]
            s = s[: comment_match.start()].rstrip()
        items = [seg.strip() for seg in s[2:].split("=") if seg.strip()]
        if len(items) >= 2:
            out.append(
                {
                    "canonical": items[0],
                    "members": items,
                    "pending": pending,
                    "sources": sources,
                }
            )
    return out


def _parse_relation_types(section: str) -> list[dict]:
    """`- 实现:A 是 B 的方法` → {name, description, pending, sources}"""
    out: list[dict] = []
    for raw in section.splitlines():
        s = raw.strip()
        if not s.startswith("- "):
            continue
        line = s[2:]
        pending = False
        sources: list[str] = []
        comment_match = re.search(r"<!--\s*(pending(?:\s+source=([^\s>]+))?)\s*-->", line)
        if comment_match:
            pending = True
            if comment_match.group(2):
                sources = [seg for seg in comment_match.group(2).split(",") if seg]
            line = line[: comment_match.start()].rstrip()
        for sep in (":", ":"):
            if sep in line:
                name, desc = line.split(sep, 1)
                out.append(
                    {
                        "name": name.strip(),
                        "description": desc.strip(),
                        "pending": pending,
                        "sources": sources,
                    }
                )
                break
        else:
            out.append(
                {"name": line.strip(), "description": "", "pending": pending, "sources": sources}
            )
    return out


def _parse_growth_config(section: str) -> dict:
    """提取 ```yaml ... ``` 块解析,失败时返回空 dict。"""
    m = re.search(r"```(?:yaml)?\s*\n(.+?)\n```", section, flags=re.DOTALL)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1)) or {}
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def _render_aliases(aliases: list[dict]) -> str:
    lines = ["## 别名词表", ""]
    for a in aliases:
        members = a.get("members") or []
        if not members:
            continue
        line = "- " + " = ".join(members)
        if a.get("pending"):
            srcs = ",".join(a.get("sources") or [])
            line += f"  <!-- pending source={srcs} -->" if srcs else "  <!-- pending -->"
        lines.append(line)
    return "\n".join(lines)


def _render_relation_types(rels: list[dict]) -> str:
    lines = ["## 关系类型词汇表", ""]
    for r in rels:
        name = r.get("name", "")
        desc = r.get("description", "")
        if not name:
            continue
        line = f"- {name}:{desc}" if desc else f"- {name}"
        if r.get("pending"):
            srcs = ",".join(r.get("sources") or [])
            line += f"  <!-- pending source={srcs} -->" if srcs else "  <!-- pending -->"
        lines.append(line)
    return "\n".join(lines)


def _render_growth_config(cfg: dict) -> str:
    if not cfg:
        return "## 成长配置"
    yml = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip()
    return f"## 成长配置\n\n```yaml\n{yml}\n```"


async def read_schema(wiki_root: Path) -> dict:
    """解析 .wiki-schema.md → {raw_markdown, aliases, relation_types, growth_config}。"""
    p = Path(wiki_root) / SCHEMA_FILE
    raw = p.read_text(encoding="utf-8") if p.exists() else ""
    sections = _split_h2_sections(raw)
    return {
        "raw_markdown": raw,
        "aliases": _parse_aliases(sections.get("别名词表", "")),
        "relation_types": _parse_relation_types(sections.get("关系类型词汇表", "")),
        "growth_config": _parse_growth_config(sections.get("成长配置", "")),
    }


def render_schema(payload: dict) -> str:
    """如果 payload 含 raw_markdown 直接用;否则按结构化字段重排。"""
    raw = payload.get("raw_markdown")
    if raw and isinstance(raw, str) and raw.strip():
        return raw
    parts = [
        "---",
        "title: 知识库主题",
        "slug: schema",
        "kind: schema",
        "---",
        "",
        "# .wiki-schema.md",
        "",
        _render_aliases(payload.get("aliases") or []),
        "",
        _render_relation_types(payload.get("relation_types") or []),
        "",
        _render_growth_config(payload.get("growth_config") or {}),
        "",
    ]
    return "\n".join(parts)


async def write_schema(*, wiki_root: Path, payload: dict, updated_by: int | None = None) -> None:
    """写回 .wiki-schema.md;同步 wiki_aliases 表(若提供 db)。"""
    text = render_schema(payload)
    p = Path(wiki_root) / SCHEMA_FILE
    await atomic_write(p, text)


def sync_aliases_to_db(db, payload: dict) -> int:
    """把别名词表同步到 wiki_aliases 表(全量覆盖)。返回写入条数。"""
    from app.models.wiki_alias import WikiAlias

    db.query(WikiAlias).delete()
    n = 0
    for group in payload.get("aliases") or []:
        canonical = group.get("canonical")
        for alias in group.get("members") or []:
            if not canonical or not alias:
                continue
            db.add(WikiAlias(canonical=canonical, alias=alias, group_key=canonical))
            n += 1
    db.flush()
    return n


# ---------------------------------------------------------------------------
# P5+ ingest 自动填充:从 Step1 抽取结果增量回写别名 / 关系类型
# ---------------------------------------------------------------------------


async def auto_fill_from_step1(
    *,
    wiki_root: Path,
    step1_result: dict,
    source_slug: str,
) -> dict:
    """ingest 后处理时调用:把 Step1 抽出的别名 / 关系类型增量写入 .wiki-schema.md。

    **直接采纳生效,不再走 pending 审核**。仅保留 sources 字段供追溯,管理员
    可在前端 SchemaEditor 直接删除不想要的条目。

    规则:
      - 别名:对每个 entity 的 (name, aliases) → 一组同义词(name 作为 canonical)
        - 已存在的同义词组(canonical 命中)→ append 新成员(去重),sources 累加
        - 不存在 → 新增一组,sources=[source_slug]
      - 关系类型:从 connections[*].rel_type 中抽出未在词表的 type
        - 已知 5 种(实现/依赖/对比/矛盾/衍生)+ 词表已有的 → 跳过
        - 新类型 → 新增 entry,sources=[source_slug]

    返回 {"new_aliases": int, "merged_aliases": int, "new_relations": int}。
    """
    p = Path(wiki_root) / SCHEMA_FILE
    raw = p.read_text(encoding="utf-8") if p.exists() else ""
    sections = _split_h2_sections(raw)
    aliases = _parse_aliases(sections.get("别名词表", ""))
    relations = _parse_relation_types(sections.get("关系类型词汇表", ""))
    growth = _parse_growth_config(sections.get("成长配置", ""))

    new_aliases = 0
    merged_aliases = 0
    by_canonical = {a["canonical"]: a for a in aliases}
    # 同义词 → canonical 反查表(用于查重)
    member_to_canonical: dict[str, str] = {}
    for a in aliases:
        for m in a.get("members") or []:
            member_to_canonical[m] = a["canonical"]

    for ent in step1_result.get("entities") or []:
        name = (ent.get("name") or "").strip()
        if not name:
            continue
        ent_aliases = [a.strip() for a in (ent.get("aliases") or []) if a and a.strip()]
        if not ent_aliases:
            continue  # 没有别名信息,跳过
        # 找已有 group:name 已是 canonical 或 member
        canonical = member_to_canonical.get(name)
        if canonical:
            group = by_canonical[canonical]
            existing = set(group.get("members") or [])
            added = False
            for a in ent_aliases:
                if a not in existing and a != canonical:
                    group.setdefault("members", []).append(a)
                    member_to_canonical[a] = canonical
                    existing.add(a)
                    added = True
            if added:
                # 别名扩充时累加 source
                srcs = set(group.get("sources") or [])
                if source_slug:
                    srcs.add(source_slug)
                group["sources"] = sorted(srcs)
                merged_aliases += 1
        else:
            # 新建 group:canonical=name,members=[name]+ent_aliases — 直接采纳
            members = [name] + [a for a in ent_aliases if a != name]
            new_group = {
                "canonical": name,
                "members": members,
                "pending": False,
                "sources": [source_slug] if source_slug else [],
            }
            aliases.append(new_group)
            by_canonical[name] = new_group
            for a in members:
                member_to_canonical[a] = name
            new_aliases += 1

    # 关系类型 — 直接采纳
    DEFAULT = {"实现", "依赖", "对比", "矛盾", "衍生"}
    known_names = {r["name"] for r in relations} | DEFAULT
    new_relations = 0
    for conn in step1_result.get("connections") or []:
        rel_type = (conn.get("rel_type") or "").strip()
        if not rel_type or rel_type in known_names:
            continue
        relations.append(
            {
                "name": rel_type,
                "description": "(由 ingest 自动抽取)",
                "pending": False,
                "sources": [source_slug] if source_slug else [],
            }
        )
        known_names.add(rel_type)
        new_relations += 1

    # 仅当有变更时才回写
    if new_aliases + merged_aliases + new_relations > 0:
        text = _rebuild_schema_text(
            sections=sections,
            aliases=aliases,
            relations=relations,
            growth=growth,
        )
        await atomic_write(p, text)

    return {
        "new_aliases": new_aliases,
        "merged_aliases": merged_aliases,
        "new_relations": new_relations,
    }


async def append_pending_aliases(
    *,
    wiki_root: Path,
    proposals: list[dict],
    source_tag: str = "void_link",
    auto_apply: bool = False,
) -> dict:
    """把 alias 提议写入 .wiki-schema.md '## 别名词表'段。

    每条 proposal = {"canonical": "六西格玛管理概述", "alias": "六西格玛管理"}

    - **已存在 canonical 组**(无论原本是否 pending)→ 把 alias 加到 members(去重),
      **不动原有 pending 标记**(避免把已审核通过的 group 误降级)
    - **新建 canonical 组**:
      - auto_apply=True → pending=False(直接采纳)
      - auto_apply=False → pending=True(等审核)

    Returns: {"appended": int, "skipped_dup": int, "auto_applied_groups": int, "new_pending_groups": int}
    """
    p = Path(wiki_root) / SCHEMA_FILE
    raw = p.read_text(encoding="utf-8") if p.exists() else ""
    sections = _split_h2_sections(raw)
    aliases = _parse_aliases(sections.get("别名词表", ""))
    relations = _parse_relation_types(sections.get("关系类型词汇表", ""))
    growth = _parse_growth_config(sections.get("成长配置", ""))

    by_canonical = {a["canonical"]: a for a in aliases}
    member_to_canonical: dict[str, str] = {}
    for a in aliases:
        for m in a.get("members") or []:
            member_to_canonical[m] = a["canonical"]

    appended = 0
    skipped = 0
    auto_applied_groups = 0
    new_pending_groups = 0

    for prop in proposals:
        canonical = (prop.get("canonical") or "").strip()
        alias = (prop.get("alias") or "").strip()
        if not canonical or not alias or canonical == alias:
            skipped += 1
            continue
        # alias 已经在某个组里 → 跳过
        if alias in member_to_canonical or alias == canonical:
            skipped += 1
            continue

        if canonical in by_canonical:
            # 已有 group:仅追加新成员,不改原 pending 标记
            grp = by_canonical[canonical]
            grp.setdefault("members", []).append(alias)
            srcs = set(grp.get("sources") or [])
            srcs.add(source_tag)
            grp["sources"] = sorted(srcs)
            # 注意:不动 grp["pending"] 让它保持原状
            if auto_apply or not grp.get("pending"):
                # 已有正式 group + 高分追加 → 算自动通过
                auto_applied_groups += 1
        else:
            # 新组
            grp = {
                "canonical": canonical,
                "members": [canonical, alias],
                "pending": (not auto_apply),
                "sources": [source_tag],
            }
            aliases.append(grp)
            by_canonical[canonical] = grp
            if auto_apply:
                auto_applied_groups += 1
            else:
                new_pending_groups += 1
        member_to_canonical[alias] = canonical
        appended += 1

    if appended > 0:
        text = _rebuild_schema_text(
            sections=sections,
            aliases=aliases,
            relations=relations,
            growth=growth,
        )
        await atomic_write(p, text)

    return {
        "appended": appended,
        "skipped_dup": skipped,
        "auto_applied_groups": auto_applied_groups,
        "new_pending_groups": new_pending_groups,
    }


def _rebuild_schema_text(
    *,
    sections: dict[str, str],
    aliases: list[dict],
    relations: list[dict],
    growth: dict,
) -> str:
    """重建 .wiki-schema.md 全文,保留未涉及的 H2 段(主题/语言/素材类型路由等)。"""
    parts = [
        "---",
        "title: 知识库主题",
        "slug: schema",
        "kind: schema",
        "---",
        "",
        "# .wiki-schema.md",
        "",
    ]
    # 保留已有 主题/语言 段
    if sections.get("主题"):
        parts.append("## 主题")
        parts.append(sections["主题"])
        parts.append("")
    if sections.get("语言"):
        parts.append("## 语言")
        parts.append(sections["语言"])
        parts.append("")
    parts.append(_render_aliases(aliases))
    parts.append("")
    parts.append(_render_relation_types(relations))
    parts.append("")
    if sections.get("素材类型路由"):
        parts.append("## 素材类型路由")
        parts.append(sections["素材类型路由"])
        parts.append("")
    parts.append(_render_growth_config(growth))
    parts.append("")
    return "\n".join(parts)


async def reject_alias(*, wiki_root: Path, canonical: str) -> bool:
    """删除指定别名组(任何状态都可删)。返回是否命中。"""
    p = Path(wiki_root) / SCHEMA_FILE
    raw = p.read_text(encoding="utf-8") if p.exists() else ""
    sections = _split_h2_sections(raw)
    aliases = _parse_aliases(sections.get("别名词表", ""))
    relations = _parse_relation_types(sections.get("关系类型词汇表", ""))
    growth = _parse_growth_config(sections.get("成长配置", ""))
    before = len(aliases)
    aliases = [a for a in aliases if a["canonical"] != canonical]
    if len(aliases) == before:
        return False
    await atomic_write(
        p,
        _rebuild_schema_text(
            sections=sections, aliases=aliases, relations=relations, growth=growth
        ),
    )
    return True


async def remove_alias_member(
    *, wiki_root: Path, canonical: str, member: str
) -> tuple[bool, str]:
    """从指定别名组中移除单个成员。

    规则:
      - canonical 找不到 → (False, "group_not_found")
      - member 不在 members 中 → (False, "member_not_found")
      - member == canonical → 整组删除(等价于 reject_alias)
      - 删后剩 < 2 个成员 → 整组删除(单条无意义)
      - 否则只从 members 数组移除该成员

    返回 (ok, action),action ∈ {"removed_member", "removed_group", "group_not_found", "member_not_found"}。
    """
    p = Path(wiki_root) / SCHEMA_FILE
    raw = p.read_text(encoding="utf-8") if p.exists() else ""
    sections = _split_h2_sections(raw)
    aliases = _parse_aliases(sections.get("别名词表", ""))
    relations = _parse_relation_types(sections.get("关系类型词汇表", ""))
    growth = _parse_growth_config(sections.get("成长配置", ""))

    target = next((a for a in aliases if a["canonical"] == canonical), None)
    if target is None:
        return False, "group_not_found"

    members = list(target.get("members") or [])
    if member not in members:
        return False, "member_not_found"

    # member == canonical 或剩余成员不足 2 个 → 整组删
    if member == canonical:
        aliases = [a for a in aliases if a["canonical"] != canonical]
        action = "removed_group"
    else:
        members.remove(member)
        if len(members) < 2:
            aliases = [a for a in aliases if a["canonical"] != canonical]
            action = "removed_group"
        else:
            target["members"] = members
            action = "removed_member"

    await atomic_write(
        p,
        _rebuild_schema_text(
            sections=sections, aliases=aliases, relations=relations, growth=growth
        ),
    )
    return True, action


async def reject_relation(*, wiki_root: Path, name: str) -> bool:
    """删除指定关系类型(任何状态都可删,默认 5 种也允许删)。"""
    p = Path(wiki_root) / SCHEMA_FILE
    raw = p.read_text(encoding="utf-8") if p.exists() else ""
    sections = _split_h2_sections(raw)
    aliases = _parse_aliases(sections.get("别名词表", ""))
    relations = _parse_relation_types(sections.get("关系类型词汇表", ""))
    growth = _parse_growth_config(sections.get("成长配置", ""))
    before = len(relations)
    relations = [r for r in relations if r["name"] != name]
    if len(relations) == before:
        return False
    await atomic_write(
        p,
        _rebuild_schema_text(
            sections=sections, aliases=aliases, relations=relations, growth=growth
        ),
    )
    return True


async def get_growth_config(wiki_root: Path) -> dict:
    """读 .wiki-schema.md 中的「成长配置」(高级设置)。"""
    p = Path(wiki_root) / SCHEMA_FILE
    raw = p.read_text(encoding="utf-8") if p.exists() else ""
    sections = _split_h2_sections(raw)
    return _parse_growth_config(sections.get("成长配置", ""))


async def set_growth_config(*, wiki_root: Path, growth: dict) -> None:
    """覆写「成长配置」段。其它段保留。"""
    p = Path(wiki_root) / SCHEMA_FILE
    raw = p.read_text(encoding="utf-8") if p.exists() else ""
    sections = _split_h2_sections(raw)
    aliases = _parse_aliases(sections.get("别名词表", ""))
    relations = _parse_relation_types(sections.get("关系类型词汇表", ""))
    await atomic_write(
        p,
        _rebuild_schema_text(
            sections=sections, aliases=aliases, relations=relations, growth=growth or {}
        ),
    )
