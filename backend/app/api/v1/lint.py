"""lint:健康检查(孤立页 / 断链 / 矛盾 / 置信度报告)。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query
from loguru import logger
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.deps import AdminUserId, DbSession
from app.schemas.misc import LintIssue, LintReport
from app.services.admin.schema_editor import append_pending_aliases
from app.services.lint.fix import fix as run_fix
from app.services.lint.runner import run_lint as run_lint_service
from app.services.lint.void_link_cleaner import (
    apply_post_run_sync,
    fill_void_entity,
    scan_void_links,
)

router = APIRouter()


@router.get("/lint", response_model=LintReport)
async def run_lint(user_id: AdminUserId, db: DbSession) -> LintReport:  # noqa: ARG001
    """运行全量健康检查。"""
    out = await run_lint_service(wiki_root=settings.wiki_root_path)
    issues = [LintIssue(**it) for it in out.get("issues", [])]
    return LintReport(issues=issues, summary=out.get("summary", {}))


@router.post("/lint/fix")
async def lint_fix(
    user_id: AdminUserId,  # noqa: ARG001
    kinds: list[str] | None = Query(default=None),
) -> dict:
    """自动修复低风险项(默认重新生成 index.md)。"""
    return await run_fix(wiki_root=settings.wiki_root_path, kinds=kinds)


# ===== 虚空连接清理 =====


@router.post("/lint/void-links/scan")
async def scan_void_links_endpoint(user_id: AdminUserId) -> dict:  # noqa: ARG001
    """扫描所有虚空连接,分类为 aliasable / fillable,不写入任何东西。"""
    return scan_void_links(wiki_root=settings.wiki_root_path)


class AliasProposalIn(BaseModel):
    canonical: str
    alias: str


class VoidLinkRunRequest(BaseModel):
    # 高分(≥0.95)直接采纳,不进 pending
    auto_apply_aliases: list[AliasProposalIn] = Field(default_factory=list)
    # 中等分(0.7~0.95)进 pending 区,等审核
    accept_aliases: list[AliasProposalIn] = Field(default_factory=list)
    generate_slugs: list[str] = Field(default_factory=list)


@router.post("/lint/void-links/run")
async def run_void_links(
    payload: VoidLinkRunRequest, user_id: AdminUserId
) -> dict:  # noqa: ARG001
    """执行选定的修复操作:
    - auto_apply_aliases:写入 .wiki-schema.md,**直接生效**
    - accept_aliases:写入 .wiki-schema.md 的 pending 区,**等待 admin 二次审核**
    - generate_slugs:对每个 slug 调 LLM 生成 entity 页(直写 wiki/entities/)

    LLM 调用一对一,不并发(避免限流)。
    """
    wiki_root = settings.wiki_root_path
    result: dict = {
        "auto_applied": 0,
        "pending_added": 0,
        "aliases_skipped": 0,
        "pages_generated": [],
        "pages_failed": [],
    }

    if payload.auto_apply_aliases:
        proposals = [{"canonical": p.canonical, "alias": p.alias} for p in payload.auto_apply_aliases]
        ar = await append_pending_aliases(
            wiki_root=wiki_root, proposals=proposals,
            source_tag="void_link", auto_apply=True,
        )
        result["auto_applied"] = ar["appended"]
        result["aliases_skipped"] += ar["skipped_dup"]
        logger.info(
            f"[void_links/run] auto_apply appended={ar['appended']} "
            f"new_groups={ar.get('new_pending_groups',0)} "
            f"auto_groups={ar.get('auto_applied_groups',0)} skipped={ar['skipped_dup']}"
        )

    if payload.accept_aliases:
        proposals = [{"canonical": p.canonical, "alias": p.alias} for p in payload.accept_aliases]
        ar = await append_pending_aliases(
            wiki_root=wiki_root, proposals=proposals,
            source_tag="void_link", auto_apply=False,
        )
        result["pending_added"] = ar["appended"]
        result["aliases_skipped"] += ar["skipped_dup"]
        logger.info(
            f"[void_links/run] pending appended={ar['appended']} skipped={ar['skipped_dup']}"
        )

    for slug in payload.generate_slugs:
        try:
            r = await fill_void_entity(wiki_root=wiki_root, slug=slug)
            if r["ok"]:
                result["pages_generated"].append({"slug": slug, "written": r["written"]})
                logger.info(f"[void_links/run] generated {slug} -> {r['written']}")
            else:
                result["pages_failed"].append({"slug": slug, "error": r["error"]})
                logger.warning(f"[void_links/run] failed {slug}: {r['error']}")
        except Exception as e:  # noqa: BLE001
            result["pages_failed"].append({"slug": slug, "error": str(e)})
            logger.exception(f"[void_links/run] exception on {slug}")
        # 间隔 0.5s 避免 LLM provider 限流
        await asyncio.sleep(0.5)

    # 收尾:把本次改动同步到 wiki_pages / wiki_links / graph-data.json
    # - alias 改了 → 全 wiki 跑 normalize 把 [[别名]] 改写成 [[canonical]]
    # - 新页面写盘了 → DB 索引同步
    # - 重建 graph-data.json 让前端图谱看到新节点
    has_changes = (
        result["auto_applied"] > 0
        or result["pending_added"] > 0
        or len(result["pages_generated"]) > 0
    )
    if has_changes:
        new_pages = [p["written"] for p in result["pages_generated"] if p.get("written")]
        try:
            sync = await apply_post_run_sync(wiki_root=wiki_root, written_pages=new_pages)
            result["post_sync"] = sync
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[void_links/run] post_sync failed: {e}")
            result["post_sync"] = {"errors": [str(e)]}

    return result
