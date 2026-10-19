"""wiki/link_resolver.py：[[X]] 双链解析、反链表构建、自动补链。

双链格式:
- `[[slug]]` 直接引用 slug
- `[[slug|显示文本]]` 引用 slug,展示为"显示文本"

设计要点:
- `extract_links` 单文件抽取,不跨文件;ingest 与 query 都要用
- `rebuild_links_index` 全量扫 wiki/**/*.md,产出 wiki_links 表数据(供 MySQL 同步)
- `find_backlinks` 反查"引用了 slug 的页面"
- `autolink_entities` 把首次出现的实体名包成 [[]],别名也走同一规范化(canonical)
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from app.services.wiki.frontmatter_io import parse_file

# 匹配 [[slug]] 或 [[slug|alias]];slug 内不允许 [、]、|
WIKILINK_PATTERN = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]]+?))?\]\]")

# 关系类型注释格式:[[slug]] <!-- type: 实现 -->
REL_COMMENT_PATTERN = re.compile(r"<!--\s*type\s*:\s*([^\s-][^-]*?)\s*-->")
CONFIDENCE_COMMENT_PATTERN = re.compile(r"<!--\s*confidence\s*:\s*(EXTRACTED|INFERRED|AMBIGUOUS)\s*-->")

# LLM 偶发把 type/confidence 注释写进 [[...]] 内部:
#   [[工业工程<!-- type: 相关 --> <!-- confidence: EXTRACTED -->]]
# 导致 target 含注释残渣,永远匹配不到现有页 → 变虚空链接。
# 这里在抽取阶段把链接内部任意 HTML 注释剥掉,还原出干净 slug。
_INNER_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _clean_target(raw: str) -> str:
    """剥掉 [[...]] 捕获内容里混入的 HTML 注释 + 首尾空白,还原干净 target。

    同时把 Unicode 破折号/连字符变体折叠成 ASCII '-',与文件名/slug 归一一致,
    避免 [[柯布‑道格拉斯…]](U+2011)与文件名里的 ASCII '-' 比对不相等而误判虚空。
    """
    from app.services.wiki.fs_io import fold_dashes

    return fold_dashes(_INNER_COMMENT_RE.sub("", raw).strip())


def extract_links(body: str) -> list[tuple[str, str | None]]:
    """从 md 正文抽 [[X]] / [[X|Y]] → [(target, alias_or_None), ...]。"""
    return [
        (_clean_target(m.group(1)), m.group(2).strip() if m.group(2) else None)
        for m in WIKILINK_PATTERN.finditer(body)
    ]


def extract_links_with_meta(body: str) -> list[dict]:
    """抽 [[X]] 同时识别紧随其后的 type / confidence 注释。

    返回每条:`{"target", "alias", "rel_type", "confidence", "occurrences": 1}`。
    同 src 内多次引用同一 target 会合并 occurrences,rel_type / confidence 取首次出现。
    """
    by_target: dict[tuple[str, str | None], dict] = {}
    for m in WIKILINK_PATTERN.finditer(body):
        raw_inner = m.group(1)
        target = _clean_target(raw_inner)
        alias = m.group(2).strip() if m.group(2) else None
        # 注释可能在 [[X]] 之后,也可能被 LLM 错塞进 [[...]] 内部 —— 两处都查。
        tail = raw_inner + body[m.end():m.end() + 80]
        rel_type = None
        confidence = None
        rel_m = REL_COMMENT_PATTERN.search(tail)
        if rel_m:
            rel_type = rel_m.group(1).strip()
        conf_m = CONFIDENCE_COMMENT_PATTERN.search(tail)
        if conf_m:
            confidence = conf_m.group(1)

        key = (target, alias)
        if key in by_target:
            by_target[key]["occurrences"] += 1
        else:
            by_target[key] = {
                "target": target,
                "alias": alias,
                "rel_type": rel_type,
                "confidence": confidence,
                "occurrences": 1,
            }
    return list(by_target.values())


# 段落标题:`## 关键实体`(允许 1~6 级 # 与首尾空白)
_SECTION_HEAD_TMPL = r"(?m)^#{{1,6}}[ \t]*{name}[ \t]*$"
# 任意 markdown 标题行(用于界定段落终点)
_ANY_HEAD_RE = re.compile(r"(?m)^#{1,6}[ \t]")


def extract_section_links(body: str, section_name: str) -> list[tuple[str, str | None]]:
    """抽取某个标题段(如「关键实体」)内的 [[X]] 双链 → [(target, alias), ...]。

    段落范围 = 该标题行之后,到下一个 markdown 标题行(任意级别)之前。
    去重保序(同 target 只保留首次)。找不到该段返回 []。
    """
    head_re = re.compile(_SECTION_HEAD_TMPL.format(name=re.escape(section_name)))
    m = head_re.search(body)
    if not m:
        return []
    start = m.end()
    nxt = _ANY_HEAD_RE.search(body, start)
    segment = body[start: nxt.start()] if nxt else body[start:]
    # 排除引用块行(`> 引自来源:[[source]]:...`):这些是出处脚注,不是该段成员。
    # 否则主题「## 关键实体」段内每条实体后跟的来源引用会把 source slug 当成成员,
    # 在目录树里冒出 exists=False 的幽灵节点。
    segment = "\n".join(
        ln for ln in segment.splitlines() if not ln.lstrip().startswith(">")
    )
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for target, alias in extract_links(segment):
        if target in seen:
            continue
        seen.add(target)
        out.append((target, alias))
    return out


def topic_member_slugs(body: str) -> list[str]:
    """主题的成员实体 slug = 正文「## 关键实体」段内的双链 target(去重保序)。

    这是「一个主题包含哪些实体」的唯一权威定义,目录树 / 关系侧栏 / 图谱 topic→entity
    边都应以此为准,避免 frontmatter.related_entities 与正文各说各话(LLM 单次生成易发散)。
    """
    return [t for t, _ in extract_section_links(body, "关键实体")]


def _slug_from_path(path: Path, wiki_root: Path) -> str:
    """从绝对路径推 slug:`wiki/entities/工效学.md` → `工效学`。

    folddash:文件名 stem 里若残留 Unicode 破折号变体,折叠成 ASCII '-',与
    _clean_target 折叠后的 link target 收敛,避免新旧两侧错位。
    """
    from app.services.wiki.fs_io import fold_dashes

    rel = path.relative_to(wiki_root)
    return fold_dashes(rel.stem)


def _kind_from_path(path: Path, wiki_root: Path) -> str | None:
    """从路径推 page kind:`wiki/entities/x.md` → `entity`,等等。"""
    rel = path.relative_to(wiki_root)
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "wiki":
        kind_dir = parts[1]
        return {
            "entities": "entity",
            "topics": "topic",
            "sources": "source",
            "queries": "query",
            "synthesis": "synthesis",
            "comparisons": "comparison",
        }.get(kind_dir)
    return None


def iter_wiki_pages(wiki_root: Path) -> list[Path]:
    """枚举所有 wiki 页面 md(不含 raw/、不含元文件)。"""
    root = Path(wiki_root)
    if not (root / "wiki").exists():
        return []
    return sorted(p for p in (root / "wiki").rglob("*.md") if p.is_file())


def rebuild_links_index(wiki_root: Path) -> list[dict]:
    """全量扫描 wiki/**/*.md,返回所有边。

    每条:`{src_slug, src_kind, dst_slug, link_type, occurrences, confidence}`。
    `src_kind` 来自源页所在目录(entity/topic/source/...),供图谱按层级过滤边
    (素材→主题、主题→实体、实体↔实体)。`link_type` / `confidence` 来源于
    [[X]] 后的 HTML 注释,可为 None。
    """
    edges: list[dict] = []
    root = Path(wiki_root).resolve()
    for page in iter_wiki_pages(root):
        src = _slug_from_path(page, root)
        src_kind = _kind_from_path(page, root)
        try:
            _, body = parse_file(page)
        except Exception:
            continue
        for link in extract_links_with_meta(body):
            edges.append({
                "src_slug": src,
                "src_kind": src_kind,
                "dst_slug": link["target"],
                "link_type": link["rel_type"],
                "occurrences": link["occurrences"],
                "confidence": link["confidence"],
            })
    return edges


def find_backlinks(slug: str, wiki_root: Path) -> list[str]:
    """重扫所有页面找反向引用本 slug 的源页 slug 列表(去重保序)。"""
    src_pages: list[str] = []
    seen: set[str] = set()
    root = Path(wiki_root).resolve()
    for page in iter_wiki_pages(root):
        src = _slug_from_path(page, root)
        if src == slug or src in seen:
            continue
        try:
            _, body = parse_file(page)
        except Exception:
            continue
        for target, _alias in extract_links(body):
            if target == slug:
                seen.add(src)
                src_pages.append(src)
                break
    return src_pages


def find_broken_links(wiki_root: Path) -> list[dict]:
    """找出所有指向不存在页面的边。返回 `[{src_slug, dst_slug}, ...]`。"""
    root = Path(wiki_root).resolve()
    existing = {_slug_from_path(p, root) for p in iter_wiki_pages(root)}
    broken: list[dict] = []
    for edge in rebuild_links_index(root):
        if edge["dst_slug"] not in existing:
            broken.append({"src_slug": edge["src_slug"], "dst_slug": edge["dst_slug"]})
    return broken


def autolink_entities(body: str, *, alias_groups: list[list[str]]) -> str:
    """把正文中"实体名首次出现"自动包成 [[canonical]]。

    `alias_groups`: `[[canonical, alias1, alias2, ...], ...]`(canonical 是 group[0])

    规则:
    - 已经在 `[[...]]` 内的不重复包(用 placeholder 占位法)
    - 每个 group 在每篇正文中只自动补一次(首次)
    - 长串优先匹配(避免"价值流图" 比 "价值流" 先匹配的反例)
    """
    if not body or not alias_groups:
        return body

    placeholders: dict[str, str] = {}

    def _stash(m: re.Match) -> str:
        token = f"\x00WL{len(placeholders)}\x00"
        placeholders[token] = m.group(0)
        return token

    stashed = WIKILINK_PATTERN.sub(_stash, body)

    # 长串优先
    pairs = []
    for group in alias_groups:
        if not group:
            continue
        canonical = group[0]
        for term in group:
            pairs.append((term, canonical))
    pairs.sort(key=lambda x: -len(x[0]))

    autolinked_canonicals: set[str] = set()
    for term, canonical in pairs:
        if canonical in autolinked_canonicals:
            continue
        if not term or term not in stashed:
            continue
        new = stashed.replace(term, f"[[{canonical}]]", 1)
        if new != stashed:
            stashed = new
            autolinked_canonicals.add(canonical)

    for token, original in placeholders.items():
        stashed = stashed.replace(token, original)
    return stashed


def slugs_referenced_by_source(source_slug: str, wiki_root: Path) -> list[str]:
    """找出 frontmatter.sources 包含 source_slug 的所有 wiki 页面 slug。

    用于 delete 流程时定位需要清理"## 来源:{slug}"段的页面。
    """
    affected: list[str] = []
    root = Path(wiki_root).resolve()
    for page in iter_wiki_pages(root):
        try:
            fm, _ = parse_file(page)
        except Exception:
            continue
        sources = fm.get("sources") or []
        if source_slug in sources:
            affected.append(_slug_from_path(page, root))
    return affected


def aggregate_edges(edges: list[dict]) -> list[dict]:
    """把 rebuild_links_index 的边按 (src, src_kind, dst, link_type) 聚合,occurrences 求和。

    src_kind 参与聚合键:同名 entity / topic 页各自的出边要分开统计,
    否则图谱无法判断这条边该归到哪一层。
    """
    bucket: dict[tuple[str, str | None, str, str | None], dict] = defaultdict(
        lambda: {"occurrences": 0, "confidence": None}
    )
    for e in edges:
        key = (e["src_slug"], e.get("src_kind"), e["dst_slug"], e.get("link_type"))
        bucket[key]["occurrences"] += int(e.get("occurrences") or 1)
        if not bucket[key]["confidence"]:
            bucket[key]["confidence"] = e.get("confidence")
    return [
        {
            "src_slug": k[0],
            "src_kind": k[1],
            "dst_slug": k[2],
            "link_type": k[3],
            "occurrences": v["occurrences"],
            "confidence": v["confidence"],
        }
        for k, v in bucket.items()
    ]
