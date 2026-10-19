"""wiki/init.py：初始化知识库目录。

对应原 scripts/init-wiki.sh，按规划文档 §2.3.1 创建：
  - 顶层文件：.wiki-schema.md / purpose.md / index.md / log.md / overview.md
  - wiki/ 子目录：entities / topics / sources / queries / synthesis(含 sessions) /
                  comparisons / _growth
  - raw/ 子目录：pdfs / articles / tweets / wechat / xiaohongshu / zhihu /
                 plain_text / assets

幂等：已存在的文件不会被覆盖，目录用 exist_ok=True；返回 created / skipped 列表。
种子模板来源：app/prompts/templates/*.md
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

# 模板根：app/prompts/templates/
TEMPLATES_ROOT = Path(__file__).resolve().parents[2] / "prompts" / "templates"

WIKI_SUBDIRS = (
    "wiki/entities",
    "wiki/topics",
    "wiki/sources",
    "wiki/queries",
    "wiki/synthesis",
    "wiki/synthesis/sessions",
    "wiki/comparisons",
    "wiki/_growth",
)

RAW_SUBDIRS = (
    "raw/pdfs",
    "raw/articles",
    "raw/tweets",
    "raw/wechat",
    "raw/xiaohongshu",
    "raw/zhihu",
    "raw/plain_text",
    "raw/assets",
)

# 种子文件：模板文件名 → wiki_root 内目标相对路径
SEED_FILES: tuple[tuple[str, str], ...] = (
    ("schema.md", ".wiki-schema.md"),
    ("purpose.md", "purpose.md"),
    ("overview.md", "overview.md"),
    ("log.md", "log.md"),
    ("index.md", "index.md"),
    ("gap_queue.md", "wiki/_growth/gap-queue.md"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _render_template(template_text: str) -> str:
    """init 阶段做最小占位替换：{now} + index.md 的计数 / 列表占位。

    其它占位（{title}, {slug} 等）属于 entity/topic/source 模板，由 ingest
    流水线在写页面时替换，不在 init 范围内。
    """
    text = template_text.replace("{now}", _now_iso())
    placeholders = {
        "{count_entity}": "0",
        "{count_topic}": "0",
        "{count_source}": "0",
        "{count_query}": "0",
        "{count_synthesis}": "0",
        "{count_comparison}": "0",
        "{count_total}": "0",
        "{entities_list}": "_暂无_",
        "{topics_list}": "_暂无_",
        "{sources_list}": "_暂无_",
        "{synthesis_list}": "_暂无_",
        "{comparisons_list}": "_暂无_",
        "{queries_list}": "_暂无_",
    }
    for k, v in placeholders.items():
        text = text.replace(k, v)
    return text


def init_wiki(wiki_root: Path | str, *, domain_ie: bool = True) -> dict:
    """创建 wiki 目录树 + 种子文件。

    Args:
        wiki_root: 目标根目录（不存在会被创建）。
        domain_ie: 暂未使用；保留给 P2 注入 IE 领域骨架的扩展点。
                   当前 schema.md / purpose.md 模板已含 IE 默认内容，通用适用。

    Returns:
        {"created": [...], "skipped": [...], "wiki_root": "<abs path>"}
    """
    root = Path(wiki_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    skipped: list[str] = []

    # 1) 创建子目录（每个目录放 .gitkeep 让空目录能被版本工具感知）
    for sub in WIKI_SUBDIRS + RAW_SUBDIRS:
        sub_path = root / sub
        is_new = not sub_path.exists()
        sub_path.mkdir(parents=True, exist_ok=True)
        keep = sub_path / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
        (created if is_new else skipped).append(sub + "/")

    # 2) 写种子文件（幂等：已存在则跳过）
    for tpl_name, dest_rel in SEED_FILES:
        dest = root / dest_rel
        if dest.exists():
            skipped.append(dest_rel)
            continue

        tpl_path = TEMPLATES_ROOT / tpl_name
        if not tpl_path.exists():
            raise FileNotFoundError(f"seed template missing: {tpl_path}")

        text = _render_template(tpl_path.read_text(encoding="utf-8"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        created.append(dest_rel)

    return {
        "created": created,
        "skipped": skipped,
        "wiki_root": str(root),
    }
