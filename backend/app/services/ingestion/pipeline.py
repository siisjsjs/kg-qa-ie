"""ingestion/pipeline.py:5 阶段流水线 orchestrator。

阶段:upload → cache_check → step1_extract → step2_compose → post_process。

设计:
  - 每阶段写 ingest_jobs 行(stage / status / started_at / finished_at / error / payload_json)
  - 每阶段附带写 log.md 行(append)
  - upload 阶段由上层 API 完成(privacy_check / raw 落盘 / documents 表写入),
    pipeline 接收 document_id 后从 cache_check 开始
  - post_process:把 step2 产出的页面字典落盘 → 应用合并 → 同步 wiki_links/wiki_pages 到 DB
  - 失败处理:任一阶段抛异常 → 记录 error / 标 failed → audit_logs(P5 起);Neo4j/Chroma 同步
    失败不阻塞主流程,只写 audit_logs(用 logger.warning 替代,真实 audit_logs 在 P5 引入)

返回值:
  {
    "document_id": int,
    "final_status": "succeeded" | "failed" | "skipped",
    "source_slug": str | None,
    "jobs": [{stage, status, error, started_at, finished_at}, ...],
    "files_written": [str, ...],          # wiki/ 下被新写或更新的文件相对路径
    "merge_actions": {entity_name: action, ...},
  }
"""

from __future__ import annotations

import asyncio
import json
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import session_scope
from app.models.document import Document
from app.models.ingest_job import IngestJob
from app.models.wiki_link import WikiLink
from app.models.wiki_page import WikiPage
from app.services.ingestion.merger import (
    apply_observation_block,
    distill_observation,
    load_alias_groups,
    merge_or_create_entity,
)
from app.services.ingestion.step1_extract import step1_extract
from app.services.ingestion.step2_compose import step2_compose
from app.services.wiki.cache import compute_hash, is_hit, record
from app.services.wiki.frontmatter_io import merge_frontmatter, parse_text, serialize
from app.services.wiki.fs_io import atomic_write, safe_join
from app.services.wiki.link_resolver import (
    aggregate_edges,
    extract_links_with_meta,
    extract_section_links,
    rebuild_links_index,
)

PIPELINE_STAGES = ("cache_check", "step1_extract", "step2_compose", "post_process")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _append_log(wiki_root: Path, action: str, detail: str) -> None:
    """append 一行到 wiki_root/log.md;最新在底部第二段(_now_section)。"""
    log_path = Path(wiki_root) / "log.md"
    ts = _now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"- **{ts}** [{action}] {detail}\n"
    if log_path.exists():
        log_path.write_text(log_path.read_text(encoding="utf-8") + line, encoding="utf-8")
    else:
        log_path.write_text(line, encoding="utf-8")


def _new_job(db: Session, *, document_id: int, stage: str, correlation_id: str | None) -> IngestJob:
    job = IngestJob(
        document_id=document_id,
        stage=stage,
        status="running",
        started_at=_now(),
        correlation_id=correlation_id,
    )
    db.add(job)
    db.flush()
    return job


def _finish_job(
    job: IngestJob,
    *,
    status: str,
    error: str | None = None,
    payload: dict | None = None,
) -> None:
    job.status = status
    job.finished_at = _now()
    if error:
        job.error = error[:4000]
    if payload is not None:
        job.payload_json = json.dumps(payload, ensure_ascii=False)


def _summarize_index(wiki_root: Path) -> str:
    """给 step1 prompt 用的 wiki 概要:每个 entity / topic 一行 `slug | aliases | brief`。

    目的:让 LLM 能基于别名命中现有页(否则它只看 slug 列表,容易把"IE"判成 new)。
    限制:每个目录最多 200 项;brief 截 80 字;aliases 截前 5 个;总输出避免过长 prompt。
    """
    parts: list[str] = []
    for kind_dir in ("entities", "topics"):
        d = Path(wiki_root) / "wiki" / kind_dir
        if not d.exists():
            continue
        rows: list[str] = []
        files = sorted(d.glob("*.md"))[:200]
        for p in files:
            slug = p.stem
            try:
                fm, _body = parse_text(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                rows.append(f"- {slug}")
                continue
            aliases = fm.get("aliases") or []
            if isinstance(aliases, list):
                aliases_str = ", ".join(str(a) for a in aliases[:5])
            else:
                aliases_str = str(aliases)
            brief = (fm.get("brief") or "").strip().replace("\n", " ")
            if len(brief) > 80:
                brief = brief[:80] + "…"
            cells = [slug]
            if aliases_str:
                cells.append(f"别名: {aliases_str}")
            if brief:
                cells.append(brief)
            rows.append("- " + " | ".join(cells))
        parts.append(f"### {kind_dir}\n" + ("\n".join(rows) or "(空)"))
    return "\n\n".join(parts) if parts else "(空)"


def _read_purpose(wiki_root: Path) -> str:
    p = Path(wiki_root) / "purpose.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _read_index_summary(wiki_root: Path, *, max_chars: int = 1500) -> str:
    p = Path(wiki_root) / "index.md"
    if not p.exists():
        return ""
    txt = p.read_text(encoding="utf-8")
    return txt[:max_chars] + ("…" if len(txt) > max_chars else "")


def _enforce_topic_naming(step1_result: dict, *, wiki_root: Path) -> dict[str, str]:
    """命名约定:主题名与实体同名时自动加「概述」后缀,根除 topic/entity 同 slug 碰撞。

    背景:wiki_pages.slug 唯一约束下,topics/作业测定.md 与 entities/作业测定.md
    同 slug 只能存活一行 → DB 查询静默丢主题;目录树/关系框也会把同名主题与
    实体互相串线。约定(用户定):素材=「第X章」、主题=名+「概述」、实体=裸名。

    与本次抽取的实体名、磁盘已有实体页两路查重;改名贯穿 step1_result 全部引用点
    (topics[].name / new_topic_names / connections.src/dst / matched_topic_pages)。
    返回 {旧名: 新名}。
    """
    entity_names: set[str] = {
        str(e.get("name")) for e in (step1_result.get("entities") or []) if e.get("name")
    }
    nve = step1_result.get("new_vs_existing") or {}
    for m in nve.get("matched_entity_pages") or []:
        for key in ("name", "slug"):
            if m.get(key):
                entity_names.add(str(m[key]))
    ents_dir = Path(wiki_root) / "wiki" / "entities"
    disk_entities: set[str] = (
        {f.stem for f in ents_dir.glob("*.md")} if ents_dir.is_dir() else set()
    )

    def _conflicts(name: str) -> bool:
        return name in entity_names or name in disk_entities

    rename: dict[str, str] = {}
    for t in step1_result.get("topics") or []:
        name = str(t.get("name") or "")
        if name and _conflicts(name) and not name.endswith("概述"):
            rename[name] = f"{name}概述"

    if not rename:
        return rename

    for t in step1_result.get("topics") or []:
        name = str(t.get("name") or "")
        if name in rename:
            t["name"] = rename[name]
    nve["new_topic_names"] = [rename.get(n, n) for n in (nve.get("new_topic_names") or [])]
    topics_dir = Path(wiki_root) / "wiki" / "topics"
    for m in nve.get("matched_topic_pages") or []:
        if m.get("name") in rename:
            new = rename[m["name"]]
            # 命中的是磁盘已有 topic:只有带后缀的新页已存在(或旧页不存在)才改指向,
            # 否则保持合并到旧页 —— 改了会让 merge 找不到现有页、凭空开新页。
            old_slug = str(m.get("slug") or m["name"])
            if (topics_dir / f"{new}.md").exists() or not (topics_dir / f"{old_slug}.md").exists():
                m["name"] = new
                m["slug"] = new
    # connections 的端点若是被改名的主题,同步改(实体端点不动 —— 同名实体仍是裸名)
    topic_old_names = set(rename)
    for c in step1_result.get("connections") or []:
        for key in ("src", "dst"):
            v = str(c.get(key) or "")
            # 端点同时是实体名时不改:裸名给实体;只有当端点仅作为主题存在时改
            if v in topic_old_names and v not in entity_names and v not in disk_entities:
                c[key] = rename[v]
    logger.info(f"step1 topic naming enforced: {rename}")
    return rename


# ---------------------------------------------------------------------------
# A 改造:落盘前把 [[别名]] normalize 为 [[规范名]]
# ---------------------------------------------------------------------------


def _load_alias_member_map(wiki_root: Path) -> dict[str, str]:
    """加载 .wiki-schema.md 别名表,返回 {member: canonical} 映射。

    member==canonical 的项也包含进去(便于查重),但 normalize 时跳过。
    pending 状态的别名也参与 normalize(用户审过 schema 才会进 wiki-schema.md)。
    """
    try:
        from app.services.admin.schema_editor import (
            SCHEMA_FILE,
            _parse_aliases,
            _split_h2_sections,
        )
    except Exception:  # noqa: BLE001
        return {}
    p = Path(wiki_root) / SCHEMA_FILE
    if not p.exists():
        return {}
    raw = p.read_text(encoding="utf-8")
    sections = _split_h2_sections(raw)
    out: dict[str, str] = {}
    for group in _parse_aliases(sections.get("别名词表", "")):
        canonical = group.get("canonical")
        if not canonical:
            continue
        for m in group.get("members") or []:
            if m and m != canonical:
                out[m] = canonical
    return out


_LINK_RE = re.compile(r"\[\[([^\]\|]+?)\]\]")


def _fs_safe_remap_files(
    files: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """落盘前把"文件名含文件系统禁用字符"的页面 stem 清成安全 slug。

    背景:实体/主题名可能含 Windows 禁用字符(如 O*NET、IE:工业工程),
    step2 直接用 `wiki/entities/{name}.md` 做路径,atomic_write 在 Windows 上
    open('O*NET系统.md.tmp') 会抛 Errno 22 → 整个 post_process 阶段崩溃,
    连带后面的零虚空清理(fire-and-forget)永远不跑 → 残留虚空链接。

    本函数对每个页面 stem 算 safe_slug;若变了:
      1. 改 files 的 path key(下游 _path_segments / 文件名都用安全名)
      2. 改该页 frontmatter.slug 为安全 slug
      3. 把所有页面正文里的 [[unsafe]] / [[unsafe|x]] / [[unsafe#x]] 改成 [[safe...]]
         —— 因为正文双链用的是实体名(=原 stem),不一起改就会变虚空链接
      4. 改 frontmatter.sources / related_entities 里命中的旧名

    Returns: (新的 files dict, remap{unsafe_stem: safe_stem})
    """
    from app.services.wiki.fs_io import safe_slug

    remap: dict[str, str] = {}
    for path in files:
        stem = Path(path).stem
        safe = safe_slug(stem)
        if safe != stem:
            remap[stem] = safe

    if not remap:
        return files, {}

    logger.info(f"[fs-safe] 文件名含禁用字符,remap: {remap}")

    def _rewrite_links(body: str) -> str:
        for old, new in remap.items():
            body = (
                body.replace(f"[[{old}]]", f"[[{new}]]")
                .replace(f"[[{old}|", f"[[{new}|")
                .replace(f"[[{old}#", f"[[{new}#")
            )
        return body

    new_files: dict[str, str] = {}
    for path, text in files.items():
        stem = Path(path).stem
        new_stem = remap.get(stem, stem)
        try:
            fm, body = parse_text(text)
        except Exception:  # noqa: BLE001
            # 解析失败的也至少把 path key 换掉,正文 [[X]] 尽量替换
            new_path = path.replace(f"/{stem}.md", f"/{new_stem}.md") if stem != new_stem else path
            new_files[new_path] = _rewrite_links(text)
            continue

        body = _rewrite_links(body)
        if stem != new_stem:
            fm["slug"] = new_stem
        # sources / related_entities 里命中旧名的也改
        for key in ("sources", "related_entities"):
            vals = fm.get(key)
            if isinstance(vals, list):
                fm[key] = [remap.get(v, v) if isinstance(v, str) else v for v in vals]
            elif isinstance(vals, str) and vals in remap:
                fm[key] = remap[vals]

        new_path = f"{Path(path).parent.as_posix()}/{new_stem}.md" if stem != new_stem else path
        new_files[new_path] = serialize(fm, body)

    return new_files, remap


def _normalize_alias_links(text: str, member_to_canonical: dict[str, str]) -> str:
    """把正文 [[member]] 替换为 [[canonical]]。

    - 仅匹配标准 wiki link;不动 markdown 普通链接 / 代码块
    - 保留 [[X|display]] 形态(目前 codebase 没用,但留个兜底)
    - tgt 含 #anchor 时只对 anchor 之前部分做 normalize
    """
    if not member_to_canonical:
        return text

    def repl(m: re.Match) -> str:
        tgt = m.group(1).strip()
        # 处理 #anchor
        anchor = ""
        if "#" in tgt:
            tgt, anchor = tgt.split("#", 1)
            tgt = tgt.strip()
            anchor = "#" + anchor
        canon = member_to_canonical.get(tgt)
        if canon and canon != tgt:
            return f"[[{canon}{anchor}]]"
        return m.group(0)

    return _LINK_RE.sub(repl, text)


# ---------------------------------------------------------------------------
# step1 connections → 正文 [[X]] 类型注释:用 step1 抽到的富类型关系
# (实现/依赖/对比/矛盾/衍生)给正文双链补 `<!-- type: X -->`。落盘前对 files
# 字典做文本变换,这样后续 rebuild_links_index 会同时把类型同步到 wiki_links
# 和 graph-data.json,边和关系图同源,不会出现 DB/文件分叉。
# ---------------------------------------------------------------------------

# 无信息量的关系类型,不用来覆盖
_VOID_REL_TYPES = frozenset({"", "相关", "related", "relate"})

# 正文里已有的 type 注释探测(用于跳过已标注的链接)
REL_TYPE_INLINE_RE = re.compile(r"<!--\s*type\s*:")


def _build_connection_type_map(
    connections: list[dict],
    *,
    alias_to_canonical: dict[str, str],
) -> dict[tuple[str, str], str]:
    """把 step1.connections 归一成 {有向实体对 (src, dst) -> rel_type}。

    - 名字先折叠破折号、再过别名表收敛到 canonical
    - 跳过空/无信息量类型、自环、缺端
    - **保留方向**:src→dst 与 dst→src 是两条不同的键。方向决定关系语义
      (「机器干扰宽放 属于 操作宽放」不能反),注入时只给方向一致的链接标类型。
    - 同一有向对出现多次时,首个有信息量的类型胜出
    """
    from app.services.wiki.fs_io import fold_dashes

    def _norm(name: str) -> str:
        raw = fold_dashes((name or "").strip())
        return alias_to_canonical.get(raw, raw)

    out: dict[tuple[str, str], str] = {}
    for conn in connections or []:
        rel_type = (conn.get("rel_type") or "").strip()
        if rel_type.lower() in _VOID_REL_TYPES:
            continue
        src = _norm(conn.get("src") or "")
        dst = _norm(conn.get("dst") or "")
        if not src or not dst or src == dst:
            continue
        out.setdefault((src, dst), rel_type)
    return out


def _annotate_connection_types(
    files: dict[str, str],
    *,
    connections: list[dict],
    alias_to_canonical: dict[str, str],
) -> tuple[dict[str, str], int]:
    """给 files 里实体/主题正文的 [[X]] 双链补关系类型注释。

    仅当:
      - 该页 → 链接目标 这一**有向**对在 step1.connections 里有富类型关系
        (即 connections 里存在 src=该页、dst=链接目标 的条目)
      - 该 [[X]] 后面还没有 `<!-- type: ... -->`(已标注的不动)
    时,在 `]]` 后追加 ` <!-- type: 实现 --> <!-- confidence: INFERRED -->`。

    **方向铁律**:只给「页面=连接 src、链接目标=连接 dst」的方向标类型。
    反方向(目标→页面)的关系不会标到本页的链接上,而会标到目标页里指向本页的
    链接上。这样 graph 边 P→T 的方向永远与 rel_type 语义一致,杜绝「操作宽放
    属于 机器干扰宽放」这类反向错标。
    """
    from app.services.wiki.fs_io import fold_dashes

    type_map = _build_connection_type_map(connections, alias_to_canonical=alias_to_canonical)
    if not type_map:
        return files, 0

    applied = 0
    out_files: dict[str, str] = {}
    for path, text in files.items():
        # 只处理实体/主题页(source/overview 不参与实体语义关系)
        if not (path.startswith("wiki/entities/") or path.startswith("wiki/topics/")):
            out_files[path] = text
            continue
        page_name = fold_dashes(Path(path).stem)
        page_canon = alias_to_canonical.get(page_name, page_name)

        def repl(m: "re.Match[str]") -> str:
            nonlocal applied
            whole = m.group(0)
            target_raw = m.group(1).strip()
            # 已经带 type 注释的不动(看 ]] 之后 60 字符内)
            tail = text[m.end():m.end() + 60]
            if REL_TYPE_INLINE_RE.search(tail) or REL_TYPE_INLINE_RE.search(target_raw):
                return whole
            target = alias_to_canonical.get(fold_dashes(target_raw), fold_dashes(target_raw))
            rel_type = type_map.get((page_canon, target))
            if not rel_type or target == page_canon:
                return whole
            applied += 1
            return f"{whole} <!-- type: {rel_type} --> <!-- confidence: INFERRED -->"

        out_files[path] = _LINK_RE.sub(repl, text)
    return out_files, applied


# ---------------------------------------------------------------------------
# post_process:落盘 + 合并 + DB 同步
# ---------------------------------------------------------------------------


def _path_segments(file_path: str) -> tuple[str, str]:
    """从 'wiki/entities/工效学.md' 提取 (kind_dir, slug)。"""
    parts = file_path.split("/")
    if len(parts) < 3 or parts[0] != "wiki":
        raise ValueError(f"unexpected file_path: {file_path}")
    kind_dir = parts[1]
    slug = Path(parts[-1]).stem
    return kind_dir, slug


_KIND_BY_DIR = {
    "entities": "entity",
    "topics": "topic",
    "sources": "source",
    "queries": "query",
    "synthesis": "synthesis",
    "comparisons": "comparison",
}


_PAGE_WRITE_LOCKS: dict[str, asyncio.Lock] = {}


def _page_write_lock(target_path: Path) -> asyncio.Lock:
    """按目标落盘路径取一把进程内锁,序列化对同一页的并发 read→merge→write。

    并行落盘时不同实体页天然走不同锁(全并行);只有当两个候选合并到同一
    canonical 页时才会争用同锁 → 串行执行,第二个在锁内重读最新文本,避免
    「各读旧文本→各 unify→后写覆盖先写」丢数据。
    """
    key = str(target_path.resolve())
    lock = _PAGE_WRITE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PAGE_WRITE_LOCKS[key] = lock
    return lock


async def _materialize_page(
    *,
    wiki_root: Path,
    file_path: str,
    new_text: str,
    source_slug: str,
    alias_groups: list[list[str]],
    merge_actions: dict[str, str],
    staged: dict[Path, str],
) -> tuple[str, str, str, dict | None]:
    """**计算**单个页面的最终落盘文本,写入 `staged`(内存),**不直接写磁盘**。

    两阶段提交(2026-06-11 质量优先改造):本函数只做「读基线→合并/融合→产出最终
    文本」,结果存入 `staged[target_path]`。实际落盘由 `_materialize_all` 在所有页面
    全部计算成功后统一执行。任一页融合失败(unify 抛 IngestQualityError)→ 异常上抛
    → 整篇 job 失败,且此刻磁盘一个字都没写,杜绝半成品污染。

    基线文本来源:优先 `staged`(本次已算过的同 canonical 页,支持同文档多候选链式
    融合),否则磁盘已有页,否则视为新建。

    Returns:
        (relative_path, slug, action, version_record)
    """
    from app.services.wiki.fs_io import safe_slug
    from app.services.wiki.version_history import compute_diff

    def _base_text(target: Path) -> str | None:
        """取目标页的合并基线:已暂存优先,其次磁盘,均无 → None(新建)。"""
        if target in staged:
            return staged[target]
        if target.exists():
            return target.read_text(encoding="utf-8")
        return None

    kind_dir, slug = _path_segments(file_path)
    slug = safe_slug(slug)  # 兜底:topic/source/... 的 stem 也清禁用字符

    new_fm, new_body = parse_text(new_text)

    if kind_dir == "entities":
        canonical_slug, action, _meta = await merge_or_create_entity(
            candidate_name=new_fm.get("title") or slug,
            candidate_brief=_first_brief(new_body),
            wiki_root=wiki_root,
            alias_groups=alias_groups,
        )
        # 兜底:merge_or_create_entity 新建分支直接回传 candidate_name(=title)做 slug,
        # 可能含文件系统禁用字符(O*NET 等)绕过上游 remap → 这里强制清安全。
        canonical_slug = safe_slug(canonical_slug)
        merge_actions[new_fm.get("title") or slug] = action
        target_path = safe_join(wiki_root, "wiki", "entities", f"{canonical_slug}.md")
        version_record: dict | None = None
        # 按目标页加锁:并发计算时不同实体走不同锁(全并行);两个候选合并到同一
        # canonical 页时同锁串行,锁内重读最新基线(staged 优先),杜绝「各读旧文本→
        # 各 unify→后写覆盖先写」丢数据。compute(read base → unify)必须在锁内完成。
        async with _page_write_lock(target_path):
            base_text = _base_text(target_path)
            page_exists = base_text is not None
            # 目标页是否已有基线,是「融合累加 vs 新建」的唯一判据 —— 不能只看 action。
            # merge_or_create_entity 的 action 有 6 种,只有 3 种以 "merged" 开头;
            # 当同名实体被二次提及但字面 slug 不完全相等(大小写/全半角/标点差异)、
            # 别名表未收录、且 embedding 相似度未达 auto 阈值时,action 会是
            # "created" / "suggested_merge",但 canonical_slug 仍指向那个已存在的同名
            # 页。只要基线存在就走融合分支(LLM unify,失败直接 raise → 整篇失败),
            # 只有基线真不存在才新建。
            if page_exists:
                existing_text = base_text
                existing_fm, existing_body = parse_text(existing_text)
                # 先把 incoming(本次新素材页)备好:补上 canonical slug + 本次 source。
                incoming_fm = {
                    **new_fm,
                    "slug": canonical_slug,
                    "sources": list({*(existing_fm.get("sources") or []), source_slug}),
                }
                incoming_text = serialize(incoming_fm, new_body)

                # Req(2026-06-06)跨素材统一融合:把已有页 + 本次新页交给 LLM 综合成
                # **一份单一定义**的页面,而不是把第二份素材堆成 "### 来源:[[X]]" 并列子段。
                # 白名单 = 现有实体 slug 全集(含本实体自己),保证融合后正文不产生虚空双链。
                # 质量优先:unify 失败会抛 IngestQualityError,直接上抛 → 整篇 job 失败,
                # 不再回退追加策略,也不写盘(staged 未被赋值)。
                from app.services.ingestion.merger import (
                    existing_entity_slugs,
                    unify_entity_pages,
                )
                _wl = existing_entity_slugs(wiki_root)
                entity_names_block = "\n".join(f"- {s}" for s in _wl[:300]) or "(无)"
                import time as _time
                _t_unify = _time.perf_counter()
                unified = await unify_entity_pages(
                    existing_text=existing_text,
                    incoming_text=incoming_text,
                    entity_title=str(new_fm.get("title") or canonical_slug),
                    entity_names_block=entity_names_block,
                )
                logger.info(
                    f"[ingest-trace] unify_entity name={canonical_slug!r} "
                    f"took={_time.perf_counter() - _t_unify:.2f}s ok=True"
                )

                # LLM 融合成功:强制 slug/sources/kind 与落盘一致后存入 staged。
                u_fm, u_body = parse_text(unified)
                u_fm = merge_frontmatter(existing_fm, u_fm)
                u_fm["slug"] = canonical_slug
                u_fm["kind"] = "entity"
                u_fm["sources"] = list({*(existing_fm.get("sources") or []), source_slug})
                merged_text = serialize(u_fm, u_body)
                staged[target_path] = merged_text
                # 写版本 diff(merge 触发 bump)
                final_fm, final_body = parse_text(merged_text)
                version_record = {
                    "slug": canonical_slug,
                    "version": int(final_fm.get("version") or 1),
                    "diff": compute_diff(
                        old_fm=existing_fm,
                        old_body=existing_body,
                        new_fm=final_fm,
                        new_body=final_body,
                    ),
                    "title": str(final_fm.get("title") or canonical_slug),
                    "triggered_by": "merge" if action != "merged" else "ingest",
                }
            else:
                # 新建:用 new_fm + new_body,但补上 slug + sources
                new_fm = dict(new_fm)
                # slug 必须 = 实际落盘文件名(canonical_slug),否则 DB 按 fm.slug 入库
                # 会与文件名错位 → 正文 [[文件名]] 在 DB join 不到页 → 前端虚空链接。
                new_fm["slug"] = canonical_slug
                new_fm.setdefault("sources", [source_slug])
                new_fm.setdefault("kind", "entity")
                staged[target_path] = serialize(new_fm, new_body)
                version_record = {
                    "slug": canonical_slug,
                    "version": int(new_fm.get("version") or 1),
                    "diff": compute_diff(
                        old_fm={}, old_body="", new_fm=new_fm, new_body=new_body
                    ),
                    "title": str(new_fm.get("title") or canonical_slug),
                    "triggered_by": "ingest",
                }
        return f"wiki/entities/{canonical_slug}.md", canonical_slug, action, version_record

    # topic / source / query / synthesis / comparison:计算最终文本存入 staged
    target_path = safe_join(wiki_root, "wiki", kind_dir, f"{slug}.md")
    new_fm = dict(new_fm)
    # slug 必须 = 实际落盘文件名(slug),否则 DB 按 fm.slug 入库会与文件名错位
    # → 正文 [[文件名]] 在 DB join 不到页 → 前端虚空链接。
    # sources 例外:其 slug 稍后由 _materialize_all 统一 remap 成带日期的规范形态。
    if kind_dir != "sources":
        new_fm["slug"] = slug
    else:
        new_fm.setdefault("slug", slug)
    new_fm.setdefault("kind", _KIND_BY_DIR.get(kind_dir, kind_dir))
    if kind_dir != "sources":
        new_fm.setdefault("sources", [source_slug])

    base_text = _base_text(target_path)
    if base_text is not None:
        existing_text = base_text
        existing_fm, existing_body = parse_text(existing_text)
        merged_fm = merge_frontmatter(existing_fm, new_fm)
        # body 简化策略:topic 也走 apply_observation_block(累积视角)
        if kind_dir == "topics":
            # related_entities 跟随正文「## 关键实体」段派生,让目录树/关系与
            # 正文/图谱归一。merged 分支正文保留 existing_body(只追加来源子段),
            # 故按 existing_body 派生。
            derived = _derive_related_entities_from_body(existing_body)
            if derived is not None:
                merged_fm["related_entities"] = derived
            merged_text = serialize(merged_fm, existing_body)
            merged_text = apply_observation_block(
                merged_text,
                source_slug=source_slug,
                new_observation=distill_observation(new_body) or new_body[:300],
            )
        else:
            # source 不应重复(slug 含日期+title 已避让);其他类型直接 new_body 替换
            merged_text = serialize(merged_fm, new_body)
        staged[target_path] = merged_text
        final_fm, final_body = parse_text(merged_text)
        version_record = {
            "slug": slug,
            "version": int(final_fm.get("version") or 1),
            "diff": compute_diff(
                old_fm=existing_fm,
                old_body=existing_body,
                new_fm=final_fm,
                new_body=final_body,
            ),
            "title": str(final_fm.get("title") or slug),
            "triggered_by": "ingest",
        }
        return f"wiki/{kind_dir}/{slug}.md", slug, "merged", version_record
    else:
        # 新建主题:related_entities 按正文「## 关键实体」段派生,与正文/图谱同源。
        if kind_dir == "topics":
            derived = _derive_related_entities_from_body(new_body)
            if derived is not None:
                new_fm["related_entities"] = derived
        staged[target_path] = serialize(new_fm, new_body)
        version_record = {
            "slug": slug,
            "version": int(new_fm.get("version") or 1),
            "diff": compute_diff(
                old_fm={}, old_body="", new_fm=new_fm, new_body=new_body
            ),
            "title": str(new_fm.get("title") or slug),
            "triggered_by": "ingest",
        }
        return f"wiki/{kind_dir}/{slug}.md", slug, "created", version_record


def _derive_related_entities_from_body(body: str) -> list[str] | None:
    """从主题正文**全文**的 `[[X]]` 双链派生 related_entities 列表。

    分叉根因:LLM 在 step2c 一次性写出 frontmatter.related_entities 与正文双链,
    两处本应同源,但 LLM 前后不一致 + post_process(fill_void/分类/别名归一)只改
    正文与 wiki_links、不回写 frontmatter → 目录树/关系(读 frontmatter)比
    正文/图谱(读正文双链 / wiki_links)少一大截实体。

    历史实现只扫「## 关键实体」单段,但 topic 正文的概要、特征、应用等段落里也
    有大量 `[[]]` 链接(它们都进了 wiki_links / 图谱)。只扫一段导致 frontmatter
    系统性少计。故改为**扫全文所有段落**的 `[[]]`,与 wiki_links 出边对齐。

    - 扫全文 `[[X]]`(含 `[[X|alias]]`),取管道符前的 slug;
    - 去重保序;
    - 跳过 source 双链(形如 2026-xx-...);
    - 跳过页内锚点(`[[X#anchor]]` 取 X 部分);
    - 无双链 → 返回 None(调用方保留 LLM 原值,不强行清空)。
    """
    collected: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", body):
        slug = m.group(1).strip()
        # 去页内锚点:[[X#anchor]] → X
        if "#" in slug:
            slug = slug.split("#", 1)[0].strip()
        if not slug or re.match(r"^\d{4}-", slug):
            continue
        if slug not in seen:
            seen.add(slug)
            collected.append(slug)
    return collected or None


def _first_brief(body: str) -> str:
    """从 body 提取"## 简介"段第一段;没有则取首段非空非标题文本。"""
    in_brief = False
    buf: list[str] = []
    for line in body.splitlines():
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
    if buf:
        return " ".join(buf)
    # fallback:取第一段非标题文本
    for para in body.split("\n\n"):
        para = para.strip()
        if para and not para.startswith("#") and not para.startswith("<!--"):
            return para[:200]
    return ""


def _sync_db_indices(
    db: Session,
    *,
    wiki_root: Path,
    written_paths: list[str],
) -> dict[str, int]:
    """把刚写过的页面同步到 wiki_pages / wiki_links 表。

    - wiki_pages:upsert(按 slug);frontmatter / body_excerpt / file_path 全更新
    - wiki_links:对每个写过的页面重抽 [[X]] → 删旧 src 边 + 插新边

    Returns:
        统计 {pages_upserted, links_deleted, links_inserted}
    """
    pages_upserted = 0
    links_deleted = 0
    links_inserted = 0

    # 同一次 ingest 里同一个 slug 可能被多次 written(merge 路径会让多个 LLM
    # 输出指向同一个 canonical entity 页);DB 同步只需做一次,否则:
    #   第 1 次:DELETE src=slug -> add WikiLink(...)
    #   第 2 次:DELETE src=slug -> add WikiLink(...) — 与上一次 add 撞 UNIQUE
    seen_slugs: set[str] = set()
    for rel in written_paths:
        abs_path = safe_join(wiki_root, *rel.split("/"))
        if not abs_path.exists():
            continue
        text = abs_path.read_text(encoding="utf-8")
        fm, body = parse_text(text)
        slug = fm.get("slug") or Path(rel).stem
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        kind_dir = rel.split("/")[1]
        kind = _KIND_BY_DIR.get(kind_dir, kind_dir)

        page = db.query(WikiPage).filter(WikiPage.slug == slug).one_or_none()
        excerpt = body.strip()[:1000]
        fm_json = json.dumps(fm, ensure_ascii=False, default=str)
        if page is None:
            db.add(
                WikiPage(
                    slug=slug,
                    title=fm.get("title") or slug,
                    kind=kind,
                    file_path=rel,
                    frontmatter_json=fm_json,
                    body_excerpt=excerpt,
                    version=int(fm.get("version") or 1),
                )
            )
        else:
            page.title = fm.get("title") or slug
            page.kind = kind
            page.file_path = rel
            page.frontmatter_json = fm_json
            page.body_excerpt = excerpt
            page.version = int(fm.get("version") or page.version or 1)
        pages_upserted += 1

        # links:删旧 src 边 + 插新边
        deleted = (
            db.query(WikiLink).filter(WikiLink.src_slug == slug).delete(synchronize_session=False)
        )
        links_deleted += int(deleted or 0)
        # 必须先 flush 让 DELETE 真正落地,否则同事务里下面的 INSERT 会跟即将被删除
        # 的旧行撞 UNIQUE(InnoDB 在 flush 前看到的是旧行)
        db.flush()
        edges_meta = extract_links_with_meta(body)
        # 同 src 重复 (dst, type) 聚合
        agg = aggregate_edges(
            [
                {
                    "src_slug": slug,
                    "dst_slug": e["target"],
                    "link_type": e["rel_type"],
                    "occurrences": e["occurrences"],
                    "confidence": e["confidence"],
                }
                for e in edges_meta
            ]
        )
        # 二次去重:MySQL 默认 utf8mb4_unicode_ci 不区分大小写,
        # aggregate_edges 在 Python 里以精确字符串去重时,'PDCA循环' 和 'pdca循环'
        # 被视为不同 key 但 DB UNIQUE 会判同 → 触发 1062。
        # 这里用 lower 再做一遍合并,occurrences 求和。
        case_dedup: dict[tuple[str, str, str], dict] = {}
        for edge in agg:
            ci_key = (
                (edge["src_slug"] or "").lower(),
                (edge["dst_slug"] or "").lower(),
                (edge["link_type"] or "related").lower(),
            )
            if ci_key in case_dedup:
                case_dedup[ci_key]["occurrences"] += int(edge["occurrences"] or 1)
            else:
                case_dedup[ci_key] = dict(edge)
        # 兜底:即便上面 dedup 了,跨页 batch 共享 session 时仍可能撞 UNIQUE
        # (例:别的页 normalize 后产生同 src+dst,在 flush 前一起入队)
        # 用 SAVEPOINT 包每条 INSERT,撞 UNIQUE 就跳过,避免整 batch 回滚
        from sqlalchemy.exc import IntegrityError as _IntegrityError
        for edge in case_dedup.values():
            try:
                with db.begin_nested():
                    db.add(
                        WikiLink(
                            src_slug=edge["src_slug"],
                            dst_slug=edge["dst_slug"],
                            link_type=edge["link_type"] or "related",
                            occurrences=edge["occurrences"] or 1,
                            confidence=edge["confidence"] or "EXTRACTED",
                        )
                    )
                    db.flush()
                links_inserted += 1
            except _IntegrityError:
                # UNIQUE 撞了说明这条边已经在表里(可能由别的 src 触发的 normalize 刚刚插入)
                # 跳过即可,不影响其它边
                pass

    db.flush()
    return {
        "pages_upserted": pages_upserted,
        "links_deleted": links_deleted,
        "links_inserted": links_inserted,
    }


def _persist_version_records(
    db: Session,
    records: list[dict],
    *,
    document_id: int,
    triggered_default: str = "ingest",
) -> int:
    """把 _materialize_page 收集到的 version_records 写入 wiki_node_versions。

    - 同 (slug, version) 已存在 → 跳过(idempotent;cache miss 重跑场景)
    - body diff 全空 + frontmatter 无变更 → 跳过(避免无意义版本号膨胀)
    """
    from app.models.wiki_node_version import WikiNodeVersion
    from app.services.wiki.version_history import write_version

    n = 0
    for r in records:
        slug = r.get("slug")
        version = int(r.get("version") or 0)
        diff = r.get("diff") or {}
        if not slug or version <= 0:
            continue
        # 跳过已存在
        existing = (
            db.query(WikiNodeVersion)
            .filter(
                WikiNodeVersion.slug == slug,
                WikiNodeVersion.version == version,
            )
            .one_or_none()
        )
        if existing is not None:
            continue
        # 跳过空 diff(避免噪声)
        body_stat = (diff.get("body") or {}).get("stat") or {}
        fm = diff.get("frontmatter") or {}
        if (
            body_stat.get("added_lines") == 0
            and body_stat.get("removed_lines") == 0
            and not fm.get("added")
            and not fm.get("removed")
            and not fm.get("changed")
        ):
            continue
        write_version(
            db,
            slug=slug,
            version=version,
            diff=diff,
            triggered_by=str(r.get("triggered_by") or triggered_default),
            title_snapshot=r.get("title"),
            document_id=document_id,
        )
        n += 1
    return n


# ---------------------------------------------------------------------------
# 顶层入口
# ---------------------------------------------------------------------------


async def run_pipeline(*, document_id: int, force: bool = False) -> dict:
    """串行跑完 5 阶段,返回最终结果。

    每阶段单独写 ingest_jobs 行,失败不影响后续 audit;不抛异常给上层(把
    错误信息包进返回值的 jobs[*].error)。
    """
    wiki_root = settings.wiki_root_path
    correlation_id = f"doc{document_id}-{_now().strftime('%Y%m%d%H%M%S')}"
    jobs_summary: list[dict[str, Any]] = []
    files_written: list[str] = []
    merge_actions: dict[str, str] = {}
    version_records: list[dict] = []
    source_slug: str | None = None
    final_status = "succeeded"

    # post_process 主体跑完后,把"置 succeeded"推迟到 _post_post_process(分类/孤儿消解/
    # 图谱回写)之后执行,让前端 /progress 的 succeeded 只在数据真正一致后出现。
    # 这些变量在 post_process 阶段 session 块内填充,在 session 块关闭后(避免行锁死锁)
    # await _post_post_process,再用新 session 置终态。
    post_process_pending = False
    post_process_job_id: int | None = None
    post_process_payload: dict | None = None

    # 各阶段需要的"跨阶段状态"
    raw_text: str | None = None
    cache_record: dict | None = None
    step1_payload: dict | None = None
    step2_files: dict[str, str] = {}

    for stage in PIPELINE_STAGES:
        import time as _t
        _stage_t0 = _t.perf_counter()
        with session_scope() as db:
            doc = db.query(Document).filter(Document.id == document_id).one_or_none()
            if doc is None:
                final_status = "failed"
                jobs_summary.append({
                    "stage": stage,
                    "status": "failed",
                    "error": f"document_id={document_id} not found",
                })
                break

            job = _new_job(db, document_id=document_id, stage=stage, correlation_id=correlation_id)
            # 立即 commit running 行,让前端轮询 /progress 能看到当前正在跑的阶段。
            # 否则 session_scope 的 commit-on-exit 会把 running 行困在内存,
            # 等阶段跑完才一起 commit,导致用户看到"卡在上一阶段 N 分钟"的假死锁。
            db.commit()
            try:
                if stage == "cache_check":
                    raw_text = Path(doc.file_path).read_text(encoding="utf-8")
                    h = compute_hash(raw_text)
                    if doc.content_hash != h:
                        # 自愈:hash 与 documents 行不一致(可能历史数据),刷新
                        doc.content_hash = h
                    rec = is_hit(wiki_root, h)
                    if rec and not force:
                        cache_record = rec
                        source_slug = rec.get("source_slug")
                        _finish_job(job, status="skipped", payload={"hit": True, "source_slug": source_slug})
                        jobs_summary.append({"stage": stage, "status": "skipped", "source_slug": source_slug})
                        # 命中后跳过余下三阶段:为每个阶段也建 DB job 行(状态 skipped),
                        # 这样前端"流水线状态"表能看到完整 5 行而不是只 1 行。
                        for sk in PIPELINE_STAGES[PIPELINE_STAGES.index(stage) + 1:]:
                            sk_job = _new_job(
                                db, document_id=document_id, stage=sk, correlation_id=correlation_id
                            )
                            _finish_job(sk_job, status="skipped", payload={"reason": "cache_hit"})
                            jobs_summary.append({"stage": sk, "status": "skipped"})
                        # 关键:cache 命中也要把 documents 行推到终态,否则前端轮询永远 running
                        doc.parse_status = "succeeded"
                        doc.parse_error = None
                        _append_log(wiki_root, "ingest", f"cache hit doc#{document_id} → {source_slug}")
                        return _result(document_id, "skipped", source_slug, jobs_summary, files_written, merge_actions)
                    _finish_job(job, status="succeeded", payload={"hit": False})
                    jobs_summary.append({"stage": stage, "status": "succeeded"})

                elif stage == "step1_extract":
                    assert raw_text is not None
                    purpose = _read_purpose(wiki_root)
                    idx_summary = _read_index_summary(wiki_root)
                    wiki_summary = _summarize_index(wiki_root)
                    step1_payload = await step1_extract(
                        raw_md=raw_text,
                        purpose_text=purpose,
                        index_summary=idx_summary,
                        wiki_structure_summary=wiki_summary,
                    )
                    # 命名约定:主题与实体同名 → 主题加「概述」后缀,防 slug 碰撞
                    _enforce_topic_naming(step1_payload, wiki_root=wiki_root)
                    _finish_job(
                        job,
                        status="succeeded",
                        payload={
                            "entities_count": len(step1_payload.get("entities", [])),
                            "topics_count": len(step1_payload.get("topics", [])),
                            "connections_count": len(step1_payload.get("connections", [])),
                        },
                    )
                    jobs_summary.append({"stage": stage, "status": "succeeded"})

                elif stage == "step2_compose":
                    assert raw_text is not None
                    assert step1_payload is not None
                    related_pages = _collect_related_pages(wiki_root, step1_payload)
                    alias_groups = load_alias_groups(wiki_root)
                    step2_files = await step2_compose(
                        raw_md=raw_text,
                        step1_result=step1_payload,
                        related_pages=related_pages,
                        alias_groups=alias_groups,
                    )
                    _finish_job(
                        job,
                        status="succeeded",
                        payload={"files_count": len(step2_files), "files": list(step2_files.keys())},
                    )
                    jobs_summary.append({"stage": stage, "status": "succeeded", "files_count": len(step2_files)})

                elif stage == "post_process":
                    assert step1_payload is not None
                    alias_groups = load_alias_groups(wiki_root)
                    # 先落 source 摘要页(它的 slug 是其他页面的 sources 字段值)
                    # 传入上传时固定的 raw 文件名 stem 作为确定性 source slug,
                    # 避免依赖 step2 LLM 生成的 title(不稳定)→ force 重跑产生幽灵章节。
                    _doc_source_slug = Path(doc.file_path).stem if doc.file_path else None
                    source_slug = await _materialize_all(
                        db=db,
                        wiki_root=wiki_root,
                        files=step2_files,
                        alias_groups=alias_groups,
                        files_written=files_written,
                        merge_actions=merge_actions,
                        version_records=version_records,
                        book=doc.book,
                        step1_result=step1_payload,
                        doc_source_slug=_doc_source_slug,
                    )
                    # 同步索引镜像(此时所有页面都已落盘)
                    sync_stat = _sync_db_indices(db, wiki_root=wiki_root, written_paths=files_written)
                    # 写节点版本历史
                    versions_written = _persist_version_records(
                        db, version_records, document_id=document_id, triggered_default="ingest"
                    )
                    # P5+ 自动填充 .wiki-schema.md(别名 / 关系类型)
                    schema_stat: dict = {}
                    try:
                        from app.services.admin.schema_editor import auto_fill_from_step1

                        schema_stat = await auto_fill_from_step1(
                            wiki_root=wiki_root,
                            step1_result=step1_payload,
                            source_slug=source_slug or "",
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"schema auto_fill failed (non-blocking): {e}")
                    # 缓存登记
                    if raw_text is not None and source_slug:
                        record(
                            wiki_root,
                            content_hash=doc.content_hash,
                            source_slug=source_slug,
                            entities=[
                                e.get("name") for e in step1_payload.get("entities", []) if e.get("name")
                            ],
                        )
                    # documents 行 + post_process job 的"succeeded"推迟到 _post_post_process
                    # (孤儿消解/分类/图谱回写)之后再置,确保前端看到 succeeded 时数据已一致。
                    # 这里只记录待终结的 payload / job id,实际终结在 session 块关闭后执行
                    # (在仍打开的 session 里 await _post_post_process 会与其内部 session_scope
                    # 争 wiki_pages 行锁而死锁)。
                    post_process_payload = {
                        **sync_stat,
                        "files_written": files_written,
                        "source_slug": source_slug,
                        "versions_written": versions_written,
                        "schema_auto_fill": schema_stat,
                    }
                    post_process_job_id = job.id
                    post_process_pending = True
                    jobs_summary.append({
                        "stage": stage,
                        "status": "succeeded",
                        **sync_stat,
                        "files_written_count": len(files_written),
                        "source_slug": source_slug,
                        "versions_written": versions_written,
                        "schema_auto_fill": schema_stat,
                    })
                    _append_log(
                        wiki_root,
                        "ingest",
                        f"doc#{document_id} → {source_slug} (pages={len(files_written)})",
                    )

            except Exception as e:  # noqa: BLE001
                tb = traceback.format_exc()
                logger.exception(f"pipeline stage={stage} failed: {e}")
                _finish_job(
                    job,
                    status="failed",
                    error=str(e)[:1000],
                    payload={
                        "exception_type": type(e).__name__,
                        "traceback": tb[-4000:],
                    },
                )
                jobs_summary.append({"stage": stage, "status": "failed", "error": str(e)})
                final_status = "failed"
                # 标 documents 失败 + log
                if doc is not None:
                    doc.parse_status = "failed"
                    doc.parse_error = f"[{stage}] {e}"[:1000]
                _append_log(wiki_root, "ingest_failed", f"doc#{document_id} stage={stage}: {e}")
                break

        _stage_dt = _t.perf_counter() - _stage_t0
        logger.info(f"[ingest-trace] stage={stage} elapsed={_stage_dt:.2f}s doc#{document_id}")

    # post_process 主体跑完(session 块已关闭、行锁已释放)后,串行 await 后处理:
    # 孤儿消解 / 全量分类 / 图谱与 frontmatter 回写。跑完它们之后才把 documents.parse_status
    # 与 post_process job 置 succeeded —— 这样前端 /progress 的"完成"语义 = 数据已一致,
    # 不再出现"完成后又冒出(无主题分组)"的时序窗口。
    if post_process_pending and post_process_job_id is not None:
        pp_error: str | None = None
        try:
            await _post_post_process(wiki_root=wiki_root, written_paths=list(files_written))
        except Exception as e:  # noqa: BLE001
            # 后处理整体失败不阻塞终结:主体页面已落盘,记 warning,仍置 succeeded。
            pp_error = str(e)
            logger.warning(f"pipeline.post_post overall failed (non-blocking): {e}")
        with session_scope() as db:
            doc = db.query(Document).filter(Document.id == document_id).one_or_none()
            if doc is not None:
                doc.parse_status = "succeeded"
                doc.parse_error = None
            job = db.query(IngestJob).filter(IngestJob.id == post_process_job_id).one_or_none()
            if job is not None:
                _finish_job(
                    job,
                    status="succeeded",
                    payload={**(post_process_payload or {}), "post_post_error": pp_error},
                )

    return _result(document_id, final_status, source_slug, jobs_summary, files_written, merge_actions)


def _result(
    document_id: int,
    final_status: str,
    source_slug: str | None,
    jobs: list,
    files_written: list[str],
    merge_actions: dict[str, str],
) -> dict:
    return {
        "document_id": document_id,
        "final_status": final_status,
        "source_slug": source_slug,
        "jobs": jobs,
        "files_written": files_written,
        "merge_actions": merge_actions,
    }


def _collect_related_pages(wiki_root: Path, step1: dict) -> dict[str, str]:
    """根据 Step1 的 matched_entity_pages / matched_topic_pages 加载现有 wiki 页正文。"""
    out: dict[str, str] = {}
    nve = step1.get("new_vs_existing") or {}
    for item in nve.get("matched_entity_pages", []):
        slug = item.get("slug")
        if not slug:
            continue
        p = safe_join(wiki_root, "wiki", "entities", f"{slug}.md")
        if p.exists():
            out[slug] = p.read_text(encoding="utf-8")
    for item in nve.get("matched_topic_pages", []):
        slug = item.get("slug")
        if not slug:
            continue
        p = safe_join(wiki_root, "wiki", "topics", f"{slug}.md")
        if p.exists():
            out[slug] = p.read_text(encoding="utf-8")
    return out


async def _materialize_all(
    *,
    db: Session,
    wiki_root: Path,
    files: dict[str, str],
    alias_groups: list[list[str]],
    files_written: list[str],
    merge_actions: dict[str, str],
    version_records: list[dict],
    book: str | None = None,
    step1_result: dict | None = None,
    doc_source_slug: str | None = None,
) -> str | None:
    """落盘所有页面,返回 source 页 slug(如果有的话)。

    顺序:先 source 摘要页,再 entities / topics(让 entities 引用 source 时不会断链)。
    收集每页的 version_record 到 version_records,在 post_process 末段统一写入 DB。

    Source slug 强制从 fm.title 重算中文 slug,绕过 LLM 自创的拼音命名;
    若发生改写,同步替换其他文件正文里的 [[old_slug]] 与 fm.sources。

    `book`:上传时选定的目标知识库。非空时强制写入 source frontmatter.book
    (覆盖 LLM 自填),topic/entity 运行时从 sources[0] 继承,无需逐页改。

    A 改造:写盘前按 .wiki-schema.md 别名表把 [[member]] 改成 [[canonical]],
    避免 LLM 在正文随手写缩写/别名造成 broken link。
    """
    from datetime import datetime as _dt

    from app.services.wiki.fs_io import slugify as _slugify

    book = (book or "").strip() or None

    # —— fs-safe:落盘前先把含文件系统禁用字符(* ? : 等)的页面 stem 清成安全 slug,
    # 并同步重写正文 [[链接]] / fm.slug / sources / related_entities。必须最先做,
    # 否则后面 atomic_write('O*NET.md.tmp') 在 Windows 抛 Errno 22 → 整阶段崩溃。
    files, _fs_remap = _fs_safe_remap_files(files)

    # —— A 改造:加载别名映射(member → canonical),用于落盘前 normalize 正文链接 —— #
    alias_member_to_canonical = _load_alias_member_map(wiki_root)
    if alias_member_to_canonical:
        files = {p: _normalize_alias_links(t, alias_member_to_canonical) for p, t in files.items()}

    # —— 关系类型注释:用 step1.connections 给实体/主题正文双链补 `<!-- type: X -->`。
    # 在别名 normalize 之后做(此时正文 [[X]] 已收敛到 canonical),让后续
    # rebuild_links_index 把类型同时同步到 wiki_links 与 graph-data.json。
    if step1_result:
        files, _ann = _annotate_connection_types(
            files,
            connections=step1_result.get("connections") or [],
            alias_to_canonical=alias_member_to_canonical,
        )
        if _ann:
            logger.info(f"_materialize_all: annotated {_ann} link(s) with step1 rel_type")

    source_slug: str | None = None
    source_files = {p: t for p, t in files.items() if p.startswith("wiki/sources/")}
    other_files = {p: t for p, t in files.items() if not p.startswith("wiki/sources/")}

    # 第一步:对每个 source 文件,先按 fm.title 重算 slug,再落盘
    slug_remap: dict[str, str] = {}  # old_slug -> new_slug
    rewritten_source_files: dict[str, str] = {}
    for path, text in source_files.items():
        old_slug = Path(path).stem
        try:
            fm, body = parse_text(text)
        except Exception:  # noqa: BLE001
            fm, body = {}, text
        # 强制写入上传时选定的 book(覆盖 LLM 自填),让章节正确归库
        if book:
            fm["book"] = book
        # source slug 优先用上传时固定的 raw 文件名 stem(doc_source_slug):
        # 它在 stage_upload_md 阶段由 `{date}-{slugify(doc.title or 文件名)}` 确定,
        # 对同一文档恒定不变。这样 force 重跑会命中同名 source 页并覆盖,
        # 而非因 LLM title 写法漂移(如"生产与生产率管理" vs "第一章 生产与生产率管理")
        # 生成不同 slug → 残留多个 source 文件 → 前端幽灵章节。
        # 仅当未传入 doc_source_slug 时(老调用方/异常)才回退到 fm.title 重算。
        if doc_source_slug:
            desired_slug = doc_source_slug
        else:
            title = (fm.get("title") or "").strip()
            if title:
                # 回退:复用 source_writer 的命名规则 `{date}-{slugify(title)}`
                date_str = _dt.utcnow().strftime("%Y-%m-%d")
                created = fm.get("created")
                if isinstance(created, str) and len(created) >= 10:
                    date_str = created[:10]
                try:
                    desired_slug = f"{date_str}-{_slugify(title)}"
                except Exception:  # noqa: BLE001
                    desired_slug = old_slug
            else:
                desired_slug = old_slug

        if desired_slug != old_slug:
            slug_remap[old_slug] = desired_slug
            fm["slug"] = desired_slug
            # source 自己 body 里也可能有 [[old_slug]](LLM 在 ## 不同素材中的观点 / 引文里写过)
            body = body.replace(f"[[{old_slug}]]", f"[[{desired_slug}]]")
            text = serialize(fm, body)
            new_path = f"wiki/sources/{desired_slug}.md"
        else:
            # slug 不变,但 book 可能已写入 fm → 重新序列化保证 book 落盘
            if book:
                text = serialize(fm, body)
            new_path = path
        rewritten_source_files[new_path] = text

    if slug_remap:
        logger.info(f"[A2] source slug 重命名: {slug_remap}")

    # —— 两阶段提交(2026-06-11 质量优先):先把所有页面「算」进内存 staged,全部
    # 成功后再统一落盘。任一页融合失败(unify 抛 IngestQualityError)→ 异常上抛,
    # 此刻磁盘一字未写 → 整篇 job 失败且不留半成品。 —— #
    staged: dict[Path, str] = {}
    src_results: list[tuple[str, dict | None]] = []
    for path, text in rewritten_source_files.items():
        rel, slug, _action, vr = await _materialize_page(
            wiki_root=wiki_root,
            file_path=path,
            new_text=text,
            source_slug="",  # source 页本身不需要 source_slug
            alias_groups=alias_groups,
            merge_actions=merge_actions,
            staged=staged,
        )
        src_results.append((rel, vr))
        source_slug = source_slug or slug

    # 第二步:把其他页面正文里所有 [[old_slug]] 与 fm.sources 替换为新 slug
    # 改用 frontmatter 结构化改写,不再依赖 string replace(yaml 缩进 / 块/流式都能命中)
    if slug_remap:
        rewrites = 0
        for path in list(other_files.keys()):
            txt = other_files[path]
            try:
                fm, body = parse_text(txt)
            except Exception:  # noqa: BLE001
                continue
            mutated = False
            # body: [[old]] → [[new]]
            for old, new in slug_remap.items():
                if f"[[{old}]]" in body:
                    body = body.replace(f"[[{old}]]", f"[[{new}]]")
                    mutated = True
            # fm.sources 是 list 形态
            srcs = fm.get("sources") or []
            if isinstance(srcs, list):
                new_srcs = [slug_remap.get(s, s) if isinstance(s, str) else s for s in srcs]
                if new_srcs != srcs:
                    fm["sources"] = new_srcs
                    mutated = True
            elif isinstance(srcs, str) and srcs in slug_remap:
                fm["sources"] = slug_remap[srcs]
                mutated = True
            if mutated:
                other_files[path] = serialize(fm, body)
                rewrites += 1
        logger.info(f"[A2] other_files 重写 {rewrites} 个 (slug_remap 命中)")

    # 并行计算:实体合并要跑 unify LLM(单页 40-100s),串行时 N 个跨素材实体
    # 墙钟 = N×单页。改成信号量限并发的 gather:不同实体页天然走不同 _page_write_lock
    # → 全并行;只有两候选合并到同一 canonical 页才同锁串行(锁内重读 staged 防丢数据)。
    # 注意:此处只「算」(写入 staged),不落盘;任一 _materialize_one 抛异常会让 gather
    # 整体抛出 → _materialize_all 抛出 → 整篇 job 失败,磁盘零写入。
    from app.services.ingestion.step2_compose import _read_llm_concurrency

    sem = asyncio.Semaphore(_read_llm_concurrency())

    async def _materialize_one(path: str, text: str) -> tuple[str, str, str, dict | None]:
        async with sem:
            return await _materialize_page(
                wiki_root=wiki_root,
                file_path=path,
                new_text=text,
                source_slug=source_slug or "",
                alias_groups=alias_groups,
                merge_actions=merge_actions,
                staged=staged,
            )

    results = await asyncio.gather(
        *(_materialize_one(path, text) for path, text in other_files.items())
    )

    # —— 提交阶段:所有页面均已成功算入 staged,现在统一落盘 —— #
    for target_path, final_text in staged.items():
        await atomic_write(target_path, final_text)

    # 收集 files_written / version_records(顺序:source 先,其他后,与旧逻辑一致)
    for rel, vr in src_results:
        files_written.append(rel)
        if vr:
            version_records.append(vr)
    for rel, _slug, _action, vr in results:
        files_written.append(rel)
        if vr:
            version_records.append(vr)

    return source_slug


async def _post_post_process(
    *, wiki_root: Path, written_paths: list[str], fill_void: bool = True
) -> None:
    """fire-and-forget:Neo4j 节点/边同步、Chroma 实体向量同步、graph-data.json 重建。

    本函数失败不影响主返回。

    Args:
        fill_void: True(ingest 场景)允许 ensure_zero_void 用 LLM 凭上下文回填缺页;
            False(删除后对齐场景)把 fill_limit 压到 0 —— 否则刚删掉的实体会被
            void-fill 当成"缺页"重新建出来(复活),违背删除意图。fill_void=False
            时仍会 strip 残留断链为纯文本,保证删除后 0 虚空、不复活。
    """
    try:
        # 重建全量 wiki_links 索引(graph-data.json / Neo4j 同步在末段统一做)
        edges = aggregate_edges(rebuild_links_index(wiki_root))
        logger.info(
            f"pipeline.post_post: pages={len(written_paths)} edges={len(edges)} "
            f"(links index rebuilt; graph/Neo4j sync 在末段统一执行)"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"pipeline.post_post failed (non-blocking): {e}")

    # 重建 graph-data.json,让前端图谱页能立刻看到新节点
    try:
        from app.services.graph.builder import (
            build_graph_data,
            write_graph_data_json,
        )

        payload = await build_graph_data(wiki_root=wiki_root)
        await write_graph_data_json(wiki_root=wiki_root, payload=payload)
        logger.info(
            f"pipeline.post_post: graph-data.json rebuilt "
            f"(nodes={len(payload.get('nodes') or [])} edges={len(payload.get('edges') or [])})"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"pipeline.post_post graph rebuild failed (non-blocking): {e}")

    # 全 wiki 兜底 alias-normalize:把历史页面里残留的 [[别名]] 也按 schema 改成 [[规范名]]
    # ingest 阶段的 _normalize_alias_links 只动本次 step2 产出,这里补扫历史包袱。
    try:
        await _full_wiki_alias_normalize(wiki_root=wiki_root)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"pipeline.post_post full alias-normalize failed (non-blocking): {e}")

    # Req2 零虚空链接(自动化):一站式清理,扫描结束 broken link = 0。
    #   0. 修复 [[X<!--...-->]] 内联注释污染 → 假性虚空立刻解析为现有页
    #   1. aliasable 近似命中现有页 → 正文 [[别名]] 改写为 [[规范名]]
    #   2. fillable 无匹配 → fill_void_entity(先过 merger 判定能否归并到已有实体,
    #      否则 LLM 凭上下文建页并强制写 sources)
    #   3. 仍 broken 的 → strip 成纯文本,确保 0 虚空、0 "待 llm 生成"占位
    # 必须先跑零虚空清理,让实体全集(含 void_fill 新建的)定型,随后才做全量分类。
    try:
        from app.services.lint.void_link_cleaner import ensure_zero_void_links

        # fill_void=False(删除对齐):fill_limit=0 → 跳过 LLM 回填,只 alias 归一 + strip,
        # 防止刚删掉的实体被"缺页回填"复活。
        _fill_limit = _read_void_backfill_limit() if fill_void else 0
        zr = await ensure_zero_void_links(wiki_root=wiki_root, fill_limit=_fill_limit)
        logger.info(
            f"pipeline.post_post: ensure_zero_void before={zr.get('before')} "
            f"after={zr.get('after')} repaired={zr.get('repaired')} "
            f"alias={zr.get('alias_applied')} filled={len(zr.get('filled') or [])} "
            f"stripped={zr.get('stripped')}"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"pipeline.post_post ensure_zero_void failed (non-blocking): {e}")

    # 全量实体分类(按 topic 覆盖,Task 4):零虚空清理后实体全集已定型 ——
    # 直扫 wiki/entities/ 全集,凡未被任何 topic 的 related_entities 覆盖的实体
    # (含 void_fill 新建、无 source 漏网者)都抓出来归入主题:
    #   1) LLM 归入(有 source 锚定的章节主题池 / 无 source 的全局主题池);
    #   2) 有 source 锚定且归不进的 → 合成新主题;
    #   3) LLM/合成失败 → 字面相似度挂到最相近已有主题。
    # 判定不依赖 sources,无论实体从哪冒出来都能抓到;分类时给无 source 实体补 sources。
    try:
        from app.services.wiki.orphan_topic import (
            classify_all_unclassified_entities,
            classify_orphan_entities,
        )

        def _sync_classify_written(rel_paths: list[str], _label: str) -> None:
            if not rel_paths:
                return
            try:
                from app.core.db import session_scope
                with session_scope() as db:
                    _sync_db_indices(db, wiki_root=wiki_root, written_paths=rel_paths)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"pipeline.post_post {_label} DB sync failed: {e}")

        # (a) 全局判据:抓「未被任何 topic 覆盖」的实体(含 void_fill 漏网、无 source、归
        #     未分类书者)。这一层与 book_tree 的逐章判据不同 —— 它漏掉「已在某章 topic 里、
        #     但在引用它的另一章里仍是孤儿」的跨章实体。
        cr = await classify_all_unclassified_entities(wiki_root=wiki_root)
        _sync_classify_written(cr.get("written") or [], "classify-all")
        logger.info(
            f"pipeline.post_post: classify-all "
            f"found={cr.get('unclassified_found')} assigned={cr.get('assigned_to_existing')} "
            f"new={cr.get('synthesized_new')} fallback={cr.get('fallback_matched')} "
            f"failures={len(cr.get('failures') or [])}"
        )

        # (b) 逐章判据(与前端 book_tree.orphan_entities 完全一致):把「被某章引用、却没出
        #     现在该章任一 topic.related_entities」的实体补进该章的某个主题。跨章实体(如
        #     工业工程被第一章+第二章共同引用)在每个引用章里都会被纳入对应主题,根除
        #     前端「(无主题分组)」桶。两层互补:全局兜底无 source 漏网,逐章对齐前端展示。
        co = await classify_orphan_entities(wiki_root=wiki_root)
        _sync_classify_written(co.get("written") or [], "classify-orphan")
        logger.info(
            f"pipeline.post_post: classify-orphan(per-chapter) "
            f"sources={co.get('sources_processed')} assigned={co.get('assigned_to_existing')} "
            f"new={co.get('synthesized_new')} fallback={co.get('fallback_matched')} "
            f"failures={len(co.get('failures') or [])}"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"pipeline.post_post classify failed (non-blocking): {e}")

    # 清理 + 分组都结束后统一重建 graph-data.json(反映新 topic 分组 + 已清理的链接)
    try:
        from app.services.graph.builder import (
            build_graph_data,
            write_graph_data_json,
        )
        payload = await build_graph_data(wiki_root=wiki_root)
        await write_graph_data_json(wiki_root=wiki_root, payload=payload)
        logger.info(
            f"pipeline.post_post: graph-data.json final rebuild "
            f"(nodes={len(payload.get('nodes') or [])} edges={len(payload.get('edges') or [])})"
        )
        # Neo4j 同步:把最终 payload(已含 orphan 分类 + 清理后链接)写入图数据库。
        # 与 /graph/rebuild 接口同源;fire-and-forget,失败不阻塞主流程。
        try:
            from app.services.graph.neo4j_client import sync_graph_to_neo4j

            await sync_graph_to_neo4j(payload)
            logger.info(
                f"pipeline.post_post: Neo4j synced "
                f"(nodes={len(payload.get('nodes') or [])} edges={len(payload.get('edges') or [])})"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"pipeline.post_post Neo4j sync failed (non-blocking): {e}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"pipeline.post_post final graph rebuild failed (non-blocking): {e}")

    # 重建知识库导航 map(per-book + global),供智能问答检索路由
    try:
        from app.services.wiki.nav_map import write_nav_maps

        stat = await write_nav_maps(wiki_root)
        logger.info(f"pipeline.post_post: nav-map rebuilt (books={len(stat.get('books_written') or [])})")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"pipeline.post_post nav-map rebuild failed (non-blocking): {e}")

    # 收尾:把 topic.related_entities / source 抽取统计回写为与 wiki_links(权威)一致。
    # 必须放在 wiki_links 重建 + 零虚空清理 + 实体分类全部完成之后,
    # 才能用最终权威数据回写,让「节点页字段 = 图谱 = 正文」三方对齐。
    try:
        _resync_frontmatter_from_links(wiki_root=wiki_root)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"pipeline.post_post frontmatter resync failed (non-blocking): {e}")

    # Keep the QA vector index aligned with the final Wiki state. The sync is
    # content-hash based, so only new or changed pages call the embedding API.
    try:
        from app.services.query.vector_index import sync_query_vector_index

        vector_stat = await sync_query_vector_index(wiki_root=wiki_root)
        logger.info(f"pipeline.post_post: query vector index synced {vector_stat}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"pipeline.post_post query vector index sync failed (non-blocking): {e}")


def _page_summary_for_bullet(wiki_root: Path, kind_dir: str, slug: str) -> str:
    """取页面一句话摘要(frontmatter.brief 优先,否则正文首行内容文本)。

    摘要内的 [[双链]] 必须还原为纯文本:这些行会被拼进 source 的链接段,
    残留双链会被 extract_section_links 误识别为该段成员。
    """
    p = wiki_root / "wiki" / kind_dir / f"{slug}.md"
    if not p.exists():
        return ""
    try:
        pfm, pbody = parse_text(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""

    def _plain(s: str) -> str:
        return re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), s)

    brief = pfm.get("brief")
    if brief:
        return _plain(str(brief).strip())[:200]
    for line in pbody.split("\n"):
        q = line.strip()
        if q and not q.startswith(("#", ">", "-", "|")):
            return _plain(q)[:200]
    return ""


def _append_missing_to_source_section(
    wiki_root: Path, body: str, section: str, desired: list[str], *, kind_dir: str
) -> str:
    """把 desired 里缺失的页面以 `- [[slug]]: 摘要` 行补进 source 正文指定段落。

    背景:step1 抽取写出的「抽取的实体/主题」段是生成时快照,后处理
    (void_fill / 分类 / 别名)新建的页面只回写了 frontmatter 统计,正文段
    不补 → 节点页正文数字与 frontmatter / 图谱对不上。只追加不删除
    (删除由 void_link_cleaner 等专门流程负责)。
    """
    if not desired:
        return body
    existing = {t for t, _ in extract_section_links(body, section)}
    missing = [s for s in desired if s not in existing]
    if not missing:
        return body
    bullets = []
    for s in missing:
        summary = _page_summary_for_bullet(wiki_root, kind_dir, s)
        bullets.append(f"- [[{s}]]: {summary}" if summary else f"- [[{s}]]")
    block = "\n".join(bullets)

    pat = re.compile(rf"(^##\s*{re.escape(section)}\s*$)(.*?)(?=^##\s|\Z)", re.M | re.S)
    m = pat.search(body)
    if not m:
        return body.rstrip() + f"\n\n## {section}\n\n{block}\n"
    seg = m.group(2)
    # 段内只有 _暂无_ 占位时整段替换,否则在段尾追加
    if re.fullmatch(r"\s*_暂无_\s*", seg):
        new_seg = f"\n\n{block}\n\n"
    else:
        new_seg = seg.rstrip() + f"\n{block}\n\n"
    return body[: m.start(2)] + new_seg + body[m.end(2):]


def _resync_frontmatter_from_links(*, wiki_root: Path) -> dict[str, int]:
    """收尾:把 topic.related_entities 与 source 抽取统计回写为与 wiki_links 一致。

    根因:topic.related_entities / source「抽取的实体/主题」清单是生成时快照,
    post_process(fill_void / 分类 / 别名归一)只改正文与 wiki_links,不回写
    frontmatter → 目录树/节点页显示的数字比图谱(wiki_links)少。

    本函数以 **wiki_links 出边为权威基准**(rebuild_links_index 刚重建过),
    重算每个 topic/source 的实际关联,回写 frontmatter,使「节点页字段 = 图谱 = 正文」三方对齐。

    - topic: related_entities = wiki_links 里 src=该topic 的所有 dst(排除 source 类)
    - source: 回写 contributed_entities / contributed_topics 计数 + 列表
      (按各页 frontmatter.sources 命中该 source 反向统计,这是 source→页 的真实贡献)

    fire-and-forget 末段调用;返回统计。
    """
    from app.core.db import session_scope
    from app.models.wiki_page import WikiPage as _WP
    from app.models.wiki_link import WikiLink as _WL

    wiki_dir = wiki_root / "wiki"
    topics_resynced = 0
    sources_resynced = 0

    # —— 1. 以 wiki_links 为准回写 topic.related_entities ——
    with session_scope() as db:
        # 各 topic 的出边(dst 列表),排除 source(日期开头)
        page_kind = {
            p.slug: p.kind
            for p in db.query(_WP.slug, _WP.kind).all()
        }
        topic_out: dict[str, list[str]] = {}
        for src, dst in db.query(_WL.src_slug, _WL.dst_slug).all():
            if page_kind.get(src) != "topic":
                continue
            if re.match(r"^\d{4}-", dst or ""):
                continue
            topic_out.setdefault(src, [])
            if dst not in topic_out[src]:
                topic_out[src].append(dst)

    topics_dir = wiki_dir / "topics"
    if topics_dir.exists():
        for p in sorted(topics_dir.glob("*.md")):
            slug = p.stem
            desired = topic_out.get(slug)
            if desired is None:
                continue
            try:
                fm, body = parse_text(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            current = fm.get("related_entities") or []
            if not isinstance(current, list):
                current = []
            # 比较成员集合(忽略顺序),不同才回写
            if set(current) != set(desired):
                fm["related_entities"] = desired
                p.write_text(serialize(fm, body), encoding="utf-8")
                topics_resynced += 1

    # —— 2. 回写 source 的实际贡献统计 ——
    # 反向索引:遍历所有 entity/topic 页 frontmatter.sources,统计每个 source 贡献了哪些页
    src_entities: dict[str, list[str]] = {}
    src_topics: dict[str, list[str]] = {}
    for sub, bucket in (("entities", src_entities), ("topics", src_topics)):
        d = wiki_dir / sub
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md")):
            try:
                fm, _ = parse_text(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            for s in fm.get("sources") or []:
                if isinstance(s, str):
                    bucket.setdefault(s.strip(), []).append(p.stem)

    sources_dir = wiki_dir / "sources"
    if sources_dir.exists():
        for p in sorted(sources_dir.glob("*.md")):
            slug = p.stem
            ents = sorted(src_entities.get(slug, []))
            tops = sorted(src_topics.get(slug, []))
            try:
                fm, body = parse_text(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            new_ce = len(ents)
            new_ct = len(tops)
            fm_changed = (
                fm.get("contributed_entity_count") != new_ce
                or fm.get("contributed_topic_count") != new_ct
            )
            if fm_changed:
                fm["contributed_entity_count"] = new_ce
                fm["contributed_topic_count"] = new_ct
                fm["contributed_entities"] = ents
                fm["contributed_topics"] = tops
            new_body = _append_missing_to_source_section(
                wiki_root, body, "抽取的实体", ents, kind_dir="entities"
            )
            new_body = _append_missing_to_source_section(
                wiki_root, new_body, "抽取的主题", tops, kind_dir="topics"
            )
            body_changed = new_body != body
            if fm_changed or body_changed:
                p.write_text(serialize(fm, new_body), encoding="utf-8")
                sources_resynced += 1

    logger.info(
        f"pipeline.post_post: frontmatter resync from links "
        f"topics={topics_resynced} sources={sources_resynced}"
    )
    return {"topics_resynced": topics_resynced, "sources_resynced": sources_resynced}


async def _full_wiki_alias_normalize(*, wiki_root: Path) -> None:
    """扫全 wiki/* 把 [[member]] 改成 [[canonical]];改完同步 wiki_pages/wiki_links。

    fire-and-forget:失败不阻塞;每次 ingest 结束后跑一次,清理历史 broken 别名。
    """
    member_to_canonical = _load_alias_member_map(wiki_root)
    if not member_to_canonical:
        return

    from app.services.wiki.fs_io import atomic_write

    wiki_dir = wiki_root / "wiki"
    pages_rewritten = 0
    links_replaced = 0
    written_paths: list[str] = []

    for sub in ("entities", "topics", "sources", "queries", "synthesis", "comparisons"):
        d = wiki_dir / sub
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md")):
            try:
                text = p.read_text(encoding="utf-8")
                fm, body = parse_text(text)
            except Exception:  # noqa: BLE001
                continue
            counter = {"n": 0}

            def repl(m, _ctx=counter):
                tgt = m.group(1).strip()
                anchor = ""
                if "#" in tgt:
                    tgt, anchor = tgt.split("#", 1)
                    tgt = tgt.strip()
                    anchor = "#" + anchor
                canon = member_to_canonical.get(tgt)
                if canon and canon != tgt:
                    _ctx["n"] += 1
                    return f"[[{canon}{anchor}]]"
                return m.group(0)

            new_body = _LINK_RE.sub(repl, body)
            if counter["n"] > 0:
                pages_rewritten += 1
                links_replaced += counter["n"]
                await atomic_write(p, serialize(fm, new_body))
            written_paths.append(f"wiki/{sub}/{p.name}")

    if pages_rewritten > 0:
        # 同步 DB(只在真有改动时跑,避免 200+ 页 upsert 浪费)
        from app.core.db import session_scope
        with session_scope() as db:
            stat = _sync_db_indices(db, wiki_root=wiki_root, written_paths=written_paths)
        logger.info(
            f"pipeline.post_post: full alias-normalize rewrote {pages_rewritten} pages, "
            f"{links_replaced} link refs; db_resync={stat}"
        )
    else:
        logger.info("pipeline.post_post: full alias-normalize 无可改写,跳过 DB resync")


def _read_void_backfill_limit() -> int:
    """单次 ingest 允许自动回填的最大虚空实体数。0 = 关闭。默认 12。"""
    try:
        from app.services.admin import config_store

        v = config_store.get_sync("ingestion.void_backfill_limit", default="")
        if v != "":
            return max(0, min(int(v), 100))
    except Exception:  # noqa: BLE001
        pass
    return 12


async def run_batch(*, document_ids: list[int], pause_every: int = 5) -> str:
    """批量入口骨架 — P2 范围只串行跑完;P3 加分批暂停。"""
    batch_id = f"batch-{_now().strftime('%Y%m%d%H%M%S')}-{len(document_ids)}"
    for did in document_ids:
        await run_pipeline(document_id=did)
    return batch_id


# 给上层 API 用的便捷 hash + raw 落盘工具,放在这里以便 upload 阶段统一调用
async def stage_upload_md(
    *,
    db: Session,
    owner_id: int,
    raw_content: str,
    source_type: str,
    original_filename: str,
    title: str | None,
    tags: list[str] | None = None,
    book: str | None = None,
) -> tuple[Document, dict]:
    """upload 阶段:隐私自查 + raw 落盘 + documents 表写入。

    返回 (document, privacy_result);privacy 含 block 时不落盘也不建 document(返回值含 ok=False)。

    `book`:上传时选定的目标知识库。非空时同步登记到 books.json(幂等),
    并落到 documents.book — post_process 阶段会把它写进 source frontmatter.book。
    """
    from app.services.ingestion.privacy_check import scan as privacy_scan
    from app.services.wiki.fs_io import slugify

    privacy = privacy_scan(raw_content)
    if not privacy["ok"]:
        return None, privacy  # type: ignore[return-value]

    wiki_root = settings.wiki_root_path
    book = (book or "").strip() or None
    if book and book != "未分类":
        # 登记到 books.json,确保即便本篇 ingest 失败,书也已可见
        try:
            from app.services.wiki.book_registry import add_book

            add_book(wiki_root, book)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"stage_upload_md: add_book({book!r}) failed (non-blocking): {e}")
    h = compute_hash(raw_content)
    date_str = _now().strftime("%Y-%m-%d")
    title_for_slug = title or original_filename.rsplit(".", 1)[0]
    fname = f"{date_str}-{slugify(title_for_slug)}.md"
    raw_dir_name = _routed_raw_subdir(source_type)
    raw_path = safe_join(wiki_root, "raw", raw_dir_name, fname)
    await atomic_write(raw_path, raw_content)

    rel_path = str(raw_path.relative_to(wiki_root)).replace("\\", "/")
    doc = Document(
        source_type=source_type,
        file_path=str(raw_path),
        original_filename=original_filename,
        content_hash=h,
        size_bytes=len(raw_content.encode("utf-8")),
        parse_status="pending",
        owner_id=owner_id,
        title=title or title_for_slug,
        book=book,
        tags_json=json.dumps(list(tags or []), ensure_ascii=False),
    )
    db.add(doc)
    db.flush()
    _append_log(
        wiki_root,
        "upload",
        f"doc#{doc.id} {original_filename} → {rel_path} ({h[:12]}…) book={book or '未分类'}",
    )
    return doc, privacy


# source_type(Document 枚举值)→ raw/ 子目录(由 wiki/init.py 创建)。
# 注意:Document.source_type 枚举与 raw 子目录命名不一一对应(单复数 / 通用化),
# 所以这里需要显式映射。未知类型一律降级到 plain_text/。
_SOURCE_TYPE_TO_RAW_DIR = {
    "md": "articles",
    "web": "articles",
    "pdf": "pdfs",
    "ppt": "articles",          # 没有单独的 raw/ppts/,放 articles
    "tweet": "tweets",
    "wechat": "wechat",
    "xiaohongshu": "xiaohongshu",
    "zhihu": "zhihu",
    "plain_text": "plain_text",
}


def _routed_raw_subdir(source_type: str) -> str:
    """source_type → raw/ 下的子目录;未知类型降级到 plain_text/。"""
    return _SOURCE_TYPE_TO_RAW_DIR.get(source_type, "plain_text")
