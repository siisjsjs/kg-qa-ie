"""admin：LLM/Embedding 配置 / schema 编辑 / purpose 编辑 / gap 队列。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.core.deps import AdminUserId, DbSession
from app.schemas.admin import (
    ConfigSummary,
    ConnectionTestRequest,
    ConnectionTestResponse,
    EmbeddingConfigRequest,
    EmbeddingConfigView,
    GapQueueItem,
    GapQueueUpdate,
    LLMConfigRequest,
    LLMConfigView,
    PurposePayload,
    SchemaEditorPayload,
)
from app.schemas.common import OkResponse
from app.services.admin import config_store
from app.services.embedding import (
    get_embedding_config,
    get_embedding_provider,
    invalidate_embedding_cache,
    list_providers as list_embedding_providers,
)
from app.services.llm import (
    get_llm_config,
    get_llm_provider,
    invalidate_llm_cache,
    list_providers as list_llm_providers,
)

router = APIRouter(prefix="/admin")


# ===== 模型配置 =====
@router.get("/config", response_model=ConfigSummary)
async def get_config(user_id: AdminUserId) -> ConfigSummary:
    """读当前 LLM / Embedding provider 配置（API key 屏蔽）。"""
    llm_cfg = get_llm_config()
    emb_cfg = get_embedding_config()
    return ConfigSummary(
        llm=LLMConfigView(
            provider=llm_cfg["provider"],
            api_key=config_store.mask_secret(llm_cfg["api_key"]) if llm_cfg["api_key"] else "",
            base_url=llm_cfg["base_url"] or None,
            model=llm_cfg["model"],
            temperature=llm_cfg["temperature"],
            max_tokens=llm_cfg["max_tokens"],
        ),
        embedding=EmbeddingConfigView(
            provider=emb_cfg["provider"],
            api_key=config_store.mask_secret(emb_cfg["api_key"]) if emb_cfg["api_key"] else "",
            base_url=emb_cfg["base_url"] or None,
            model=emb_cfg["model"],
            dim=emb_cfg["dim"],
        ),
        available_llm_providers=list_llm_providers(),
        available_embedding_providers=list_embedding_providers(),
    )


def _api_key_for_write(submitted: str | None) -> str | None:
    """空串 / None → 保留已有值（返回 None 表示"不写"）。

    防止前端因为读视图返回的是屏蔽串，回写时把屏蔽串当真值覆盖掉真 key。
    """
    if submitted is None:
        return None
    s = submitted.strip()
    if not s:
        return None
    if "*" in s:  # 看起来是屏蔽串，跳过
        return None
    return s


@router.put("/config/llm", response_model=OkResponse)
async def put_llm_config(
    payload: LLMConfigRequest,
    user_id: AdminUserId,
) -> OkResponse:
    """更新 LLM 配置（API key 加密存储 + 立即生效）。"""
    if payload.provider not in list_llm_providers():
        raise HTTPException(status_code=422, detail=f"unknown llm provider: {payload.provider}")

    items = [
        {"key": "llm.provider", "value": payload.provider, "encrypted": False},
        {"key": "llm.base_url", "value": payload.base_url or "", "encrypted": False},
        {"key": "llm.model", "value": payload.model, "encrypted": False},
        {"key": "llm.temperature", "value": str(payload.temperature), "encrypted": False},
        {"key": "llm.max_tokens", "value": str(payload.max_tokens), "encrypted": False},
    ]
    new_key = _api_key_for_write(payload.api_key)
    if new_key is not None:
        items.append({"key": "llm.api_key", "value": new_key, "encrypted": True})

    await config_store.set_many(items, updated_by=user_id)
    invalidate_llm_cache()
    return OkResponse(ok=True, message="llm config updated")


@router.put("/config/embedding", response_model=OkResponse)
async def put_embedding_config(
    payload: EmbeddingConfigRequest,
    user_id: AdminUserId,
) -> OkResponse:
    """更新 Embedding 配置（API key 加密存储 + 立即生效）。"""
    if payload.provider not in list_embedding_providers():
        raise HTTPException(status_code=422, detail=f"unknown embedding provider: {payload.provider}")

    items = [
        {"key": "embedding.provider", "value": payload.provider, "encrypted": False},
        {"key": "embedding.base_url", "value": payload.base_url or "", "encrypted": False},
        {"key": "embedding.model", "value": payload.model, "encrypted": False},
        {"key": "embedding.dim", "value": str(payload.dim), "encrypted": False},
    ]
    new_key = _api_key_for_write(payload.api_key)
    if new_key is not None:
        items.append({"key": "embedding.api_key", "value": new_key, "encrypted": True})

    await config_store.set_many(items, updated_by=user_id)
    invalidate_embedding_cache()
    return OkResponse(ok=True, message="embedding config updated")


# ===== firecrawl 配置 =====
@router.get("/config/firecrawl")
async def get_firecrawl_config(user_id: AdminUserId):  # noqa: ARG001
    """读 firecrawl 配置(api_key 屏蔽)。"""
    from app.schemas.scraper import FirecrawlConfigView

    api_key = config_store.get_sync("firecrawl.api_key", default="") or ""
    base_url = config_store.get_sync("firecrawl.base_url", default="") or ""
    return FirecrawlConfigView(
        api_key=config_store.mask_secret(api_key) if api_key else "",
        base_url=base_url,
    )


@router.put("/config/firecrawl", response_model=OkResponse)
async def put_firecrawl_config(user_id: AdminUserId, payload: dict) -> OkResponse:
    """写 firecrawl 配置(api_key Fernet 加密;空串/星号串视为不修改)。"""
    items: list[dict] = []
    submitted_key = payload.get("api_key")
    new_key = _api_key_for_write(submitted_key)
    if new_key is not None:
        items.append({"key": "firecrawl.api_key", "value": new_key, "encrypted": True})
    base_url = payload.get("base_url")
    if base_url is not None:
        items.append({"key": "firecrawl.base_url", "value": str(base_url).strip(), "encrypted": False})
    if items:
        await config_store.set_many(items, updated_by=user_id)
    return OkResponse(ok=True, message="firecrawl config updated")


# ===== 工具配置(Agentic 改造 §7:每次调用现读 config_store,保存即生效) =====
@router.get("/config/tavily")
async def get_tavily_config(user_id: AdminUserId):  # noqa: ARG001
    """读 Tavily 联网搜索配置(api_key 屏蔽)。"""
    api_key = config_store.get_sync("tavily.api_key", default="") or ""
    return {"api_key": config_store.mask_secret(api_key) if api_key else ""}


@router.put("/config/tavily", response_model=OkResponse)
async def put_tavily_config(user_id: AdminUserId, payload: dict) -> OkResponse:
    """写 Tavily 配置(api_key Fernet 加密;空串/星号串视为不修改)。

    保存新 key 顺带清掉 web_search 的熔断记录(用户可能刚充值/换 key)。
    """
    from app.services.tools.base import breaker

    new_key = _api_key_for_write(payload.get("api_key"))
    if new_key is not None:
        await config_store.set_many(
            [{"key": "tavily.api_key", "value": new_key, "encrypted": True}],
            updated_by=user_id,
        )
        breaker.clear("web_search")
    return OkResponse(ok=True, message="tavily config updated")


@router.get("/config/imagegen")
async def get_imagegen_config(user_id: AdminUserId):  # noqa: ARG001
    """读画图接口配置(api_key 屏蔽)。"""
    api_key = config_store.get_sync("imagegen.api_key", default="") or ""
    return {
        "base_url": config_store.get_sync("imagegen.base_url", default="") or "",
        "api_key": config_store.mask_secret(api_key) if api_key else "",
        "model": config_store.get_sync("imagegen.model", default="") or "",
        "size": config_store.get_sync("imagegen.size", default="") or "",
    }


@router.put("/config/imagegen", response_model=OkResponse)
async def put_imagegen_config(user_id: AdminUserId, payload: dict) -> OkResponse:
    """写画图接口配置(base_url/model/size 明文,api_key 加密)。"""
    from app.services.tools.base import breaker

    items: list[dict] = []
    for field in ("base_url", "model", "size"):
        if payload.get(field) is not None:
            items.append(
                {"key": f"imagegen.{field}", "value": str(payload[field]).strip(), "encrypted": False}
            )
    new_key = _api_key_for_write(payload.get("api_key"))
    if new_key is not None:
        items.append({"key": "imagegen.api_key", "value": new_key, "encrypted": True})
    if items:
        await config_store.set_many(items, updated_by=user_id)
        breaker.clear("generate_image")
    return OkResponse(ok=True, message="imagegen config updated")


@router.get("/config/zhihuitu")
async def get_zhihuitu_config(user_id: AdminUserId):  # noqa: ARG001
    """读绘图平台(智绘图)地址配置。

    账号无需配置:问答触发绘图工具时,以提问用户自己的登录态走 SSO 账号同步。
    """
    return {"base_url": config_store.get_sync("zhihuitu.base_url", default="") or ""}


@router.put("/config/zhihuitu", response_model=OkResponse)
async def put_zhihuitu_config(user_id: AdminUserId, payload: dict) -> OkResponse:
    """写绘图平台地址(明文,无密钥);保存后清熔断与缓存。"""
    from app.services.tools.base import breaker
    from app.services.tools.chart_generate import invalidate_token_cache
    from app.services.tools.chart_templates import invalidate_template_cache

    base_url = payload.get("base_url")
    if base_url is not None:
        await config_store.set_many(
            [{"key": "zhihuitu.base_url", "value": str(base_url).strip(), "encrypted": False}],
            updated_by=user_id,
        )
        breaker.clear("recommend_chart_templates")
        breaker.clear("generate_chart")
        invalidate_token_cache()
        invalidate_template_cache()
    return OkResponse(ok=True, message="zhihuitu config updated")


async def _test_zhihuitu(kgqa_token: str) -> tuple[bool, str]:
    """验证绘图平台连通性:模板列表可达 + 用当前管理员的登录态验证 SSO 账号同步。"""
    import httpx

    from app.services.tools.chart_generate import exchange_user_token
    from app.services.tools.chart_templates import zhihuitu_base_url

    base_url = zhihuitu_base_url()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(f"{base_url}/api/v1/templates")
            if resp.status_code >= 400:
                return False, f"模板列表接口 HTTP {resp.status_code}"
            data = resp.json()
            if isinstance(data, list):
                items = data
            else:
                items = next(
                    (data[k] for k in ("data", "items", "templates") if isinstance(data.get(k), list)),
                    [],
                )
            if not kgqa_token:
                return True, f"模板列表连通({len(items)} 个模板);无登录态,未验证账号同步"
            try:
                await exchange_user_token(client, base_url, kgqa_token, force=True)
            except RuntimeError as e:
                return False, f"模板列表连通({len(items)} 个模板),但账号同步失败:{e}"
            return True, f"模板列表连通({len(items)} 个模板),账号同步正常,问答内可服务端出图"
    except Exception as e:  # noqa: BLE001
        return False, f"连接失败: {e}"


async def _test_tavily() -> tuple[bool, str]:
    """发一个最小测试查询验证 Tavily key 可用。"""
    import httpx

    api_key = (config_store.get_sync("tavily.api_key", default="") or "").strip()
    if not api_key:
        return False, "tavily.api_key 未配置"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": "test", "max_results": 1},
            )
    except Exception as e:  # noqa: BLE001
        return False, f"网络错误: {e}"
    if resp.status_code == 200:
        return True, "Tavily 测试查询成功"
    if resp.status_code in (401, 403):
        return False, f"API key 无效(HTTP {resp.status_code})"
    return False, f"HTTP {resp.status_code}: {resp.text[:200]}"


def _imagegen_api_root(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url


async def _test_imagegen() -> tuple[bool, str]:
    """校验 /v1/models 可达且当前 model 在列表里,不真生成图。"""
    import httpx

    raw_base = (config_store.get_sync("imagegen.base_url", default="") or "").strip()
    api_key = (config_store.get_sync("imagegen.api_key", default="") or "").strip()
    model = (config_store.get_sync("imagegen.model", default="") or "").strip()
    missing = [
        name
        for name, val in (("base_url", raw_base), ("api_key", api_key), ("model", model))
        if not val
    ]
    if missing:
        return False, f"imagegen 配置缺失: {', '.join(missing)}"
    api_root = _imagegen_api_root(raw_base)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{api_root}/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except Exception as e:  # noqa: BLE001
        return False, f"网络错误: {e}"
    if resp.status_code in (401, 403):
        return False, f"API key 无效(HTTP {resp.status_code})"
    if resp.status_code >= 500:
        return False, f"服务端错误(HTTP {resp.status_code})"
    ctype = (resp.headers.get("content-type") or "").lower()
    if "json" not in ctype:
        return False, f"打到了网站页而不是 API(HTTP {resp.status_code}),请确认 Base URL"
    ids: list[str] = []
    try:
        payload = resp.json()
        ids = [str(m.get("id") or "") for m in (payload.get("data") or [])]
    except Exception:  # noqa: BLE001
        return False, f"模型列表不是 JSON: {resp.text[:120]}"
    if ids and model not in ids:
        return False, f"连通正常但模型 {model} 不在列表中"
    return True, f"连通正常(HTTP {resp.status_code}),model={model}"


@router.post("/config/test", response_model=ConnectionTestResponse)
async def test_provider(
    payload: ConnectionTestRequest,
    user_id: AdminUserId,  # noqa: ARG001
    authorization: str | None = Header(default=None),
) -> ConnectionTestResponse:
    started = time.perf_counter()

    if payload.target == "firecrawl":
        from app.services.scraper import test_connection as firecrawl_test
        try:
            ok, info = await firecrawl_test()
        except Exception as e:  # noqa: BLE001
            return ConnectionTestResponse(ok=False, error=f"unexpected error: {e}")
        latency_ms = int((time.perf_counter() - started) * 1000)
        if ok:
            return ConnectionTestResponse(ok=True, latency_ms=latency_ms, sample_output=info)
        return ConnectionTestResponse(ok=False, latency_ms=latency_ms, error=info)

    if payload.target == "tavily":
        ok, info = await _test_tavily()
        latency_ms = int((time.perf_counter() - started) * 1000)
        if ok:
            return ConnectionTestResponse(ok=True, latency_ms=latency_ms, sample_output=info)
        return ConnectionTestResponse(ok=False, latency_ms=latency_ms, error=info)

    if payload.target == "imagegen":
        ok, info = await _test_imagegen()
        latency_ms = int((time.perf_counter() - started) * 1000)
        if ok:
            return ConnectionTestResponse(ok=True, latency_ms=latency_ms, sample_output=info)
        return ConnectionTestResponse(ok=False, latency_ms=latency_ms, error=info)

    if payload.target == "zhihuitu":
        kgqa_token = ""
        if authorization and authorization.lower().startswith("bearer "):
            kgqa_token = authorization.split(" ", 1)[1].strip()
        ok, info = await _test_zhihuitu(kgqa_token)
        latency_ms = int((time.perf_counter() - started) * 1000)
        if ok:
            return ConnectionTestResponse(ok=True, latency_ms=latency_ms, sample_output=info)
        return ConnectionTestResponse(ok=False, latency_ms=latency_ms, error=info)

    try:
        if payload.target == "llm":
            provider = get_llm_provider()
        else:
            provider = get_embedding_provider()
    except Exception as e:  # noqa: BLE001
        return ConnectionTestResponse(ok=False, error=f"failed to init provider: {e}")

    try:
        ok, info = await provider.test_connection()
    except NotImplementedError:
        return ConnectionTestResponse(
            ok=False,
            error=f"{payload.target} provider '{provider.name}' has no test_connection implementation yet",
        )
    except Exception as e:  # noqa: BLE001
        return ConnectionTestResponse(ok=False, error=f"unexpected error: {e}")

    latency_ms = int((time.perf_counter() - started) * 1000)
    if ok:
        return ConnectionTestResponse(ok=True, latency_ms=latency_ms, sample_output=info)
    return ConnectionTestResponse(ok=False, latency_ms=latency_ms, error=info)


# ===== schema / purpose 编辑 =====
@router.get("/schema", response_model=SchemaEditorPayload)
async def get_schema(user_id: AdminUserId, db: DbSession) -> SchemaEditorPayload:
    """读 .wiki-schema.md(解析为别名词表 / 关系类型词汇表 / 成长配置)。

    别名 / 关系条目带 `pending` 标记 + `sources` 字段;前端 SchemaEditor 据此分待审核 / 已采纳两栏。
    """
    from app.core.config import settings as _settings
    from app.services.admin.schema_editor import read_schema

    data = await read_schema(_settings.wiki_root_path)
    return SchemaEditorPayload(**data)


@router.put("/schema", response_model=OkResponse)
async def put_schema(
    payload: SchemaEditorPayload, user_id: AdminUserId, db: DbSession
) -> OkResponse:
    """覆盖 .wiki-schema.md,并同步 wiki_aliases 表。"""
    from app.core.config import settings as _settings
    from app.services.admin.schema_editor import sync_aliases_to_db, write_schema

    await write_schema(
        wiki_root=_settings.wiki_root_path, payload=payload.model_dump(), updated_by=user_id
    )
    try:
        n = sync_aliases_to_db(db, payload.model_dump())
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return OkResponse(ok=True, message=f"schema written; alias DB sync failed: {e}")
    return OkResponse(ok=True, message=f"schema updated; aliases synced ({n} rows)")


# ----- P5+ schema 删除工作流(自动写入,管理员只能删除不想要的条目) -----


class _SchemaItemKey(BaseModel):
    key: str  # 别名 canonical 或 关系类型 name


@router.post("/schema/aliases/delete", response_model=OkResponse)
async def delete_schema_alias(
    payload: _SchemaItemKey, user_id: AdminUserId, db: DbSession
) -> OkResponse:
    """删除一个别名组(由 ingest 自动写入,管理员可删)。"""
    from app.core.config import settings as _settings
    from app.services.admin.schema_editor import read_schema, reject_alias, sync_aliases_to_db

    hit = await reject_alias(wiki_root=_settings.wiki_root_path, canonical=payload.key)
    if not hit:
        raise HTTPException(status_code=404, detail=f"alias {payload.key!r} not found")
    # 同步 DB
    data = await read_schema(_settings.wiki_root_path)
    try:
        sync_aliases_to_db(db, data)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return OkResponse(ok=True, message=f"alias {payload.key!r} deleted")


class _AliasMemberPayload(BaseModel):
    canonical: str
    member: str


@router.post("/schema/aliases/member/delete", response_model=OkResponse)
async def delete_schema_alias_member(
    payload: _AliasMemberPayload, user_id: AdminUserId, db: DbSession
) -> OkResponse:
    """从指定别名组移除单个成员;成员==canonical 或剩余成员不足 2 时整组删。"""
    from app.core.config import settings as _settings
    from app.services.admin.schema_editor import (
        read_schema,
        remove_alias_member,
        sync_aliases_to_db,
    )

    ok, action = await remove_alias_member(
        wiki_root=_settings.wiki_root_path,
        canonical=payload.canonical,
        member=payload.member,
    )
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"alias member not found: canonical={payload.canonical!r} member={payload.member!r} ({action})",
        )
    # 同步 DB
    data = await read_schema(_settings.wiki_root_path)
    try:
        sync_aliases_to_db(db, data)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return OkResponse(ok=True, message=f"{action}: {payload.member!r} from {payload.canonical!r}")


@router.post("/schema/relations/delete", response_model=OkResponse)
async def delete_schema_relation(
    payload: _SchemaItemKey, user_id: AdminUserId, db: DbSession
) -> OkResponse:
    from app.core.config import settings as _settings
    from app.services.admin.schema_editor import reject_relation

    hit = await reject_relation(wiki_root=_settings.wiki_root_path, name=payload.key)
    if not hit:
        raise HTTPException(status_code=404, detail=f"relation {payload.key!r} not found")
    return OkResponse(ok=True, message=f"relation {payload.key!r} deleted")


# 成长配置:从 SchemaEditor 解耦,挪到 ModelConfig 高级设置展示
class _GrowthConfigPayload(BaseModel):
    config: dict


@router.get("/schema/growth", response_model=_GrowthConfigPayload)
async def get_growth_config_endpoint(user_id: AdminUserId) -> _GrowthConfigPayload:  # noqa: ARG001
    from app.core.config import settings as _settings
    from app.services.admin.schema_editor import get_growth_config

    cfg = await get_growth_config(_settings.wiki_root_path)
    return _GrowthConfigPayload(config=cfg)


@router.put("/schema/growth", response_model=OkResponse)
async def put_growth_config_endpoint(
    payload: _GrowthConfigPayload, user_id: AdminUserId
) -> OkResponse:  # noqa: ARG001
    from app.core.config import settings as _settings
    from app.services.admin.schema_editor import set_growth_config

    await set_growth_config(wiki_root=_settings.wiki_root_path, growth=payload.config or {})
    return OkResponse(ok=True, message="growth config updated")


@router.get("/purpose", response_model=PurposePayload)
async def get_purpose(user_id: AdminUserId, db: DbSession) -> PurposePayload:
    """读 purpose.md。"""
    from app.core.config import settings as _settings
    from app.services.admin.purpose_editor import read_purpose

    data = await read_purpose(_settings.wiki_root_path)
    return PurposePayload(**data)


@router.put("/purpose", response_model=OkResponse)
async def put_purpose(
    payload: PurposePayload, user_id: AdminUserId, db: DbSession
) -> OkResponse:
    """覆盖 purpose.md。"""
    from app.core.config import settings as _settings
    from app.services.admin.purpose_editor import write_purpose

    await write_purpose(wiki_root=_settings.wiki_root_path, payload=payload.model_dump())
    return OkResponse(ok=True, message="purpose updated")


# ===== gap 队列 =====
@router.get("/gap-queue", response_model=list[GapQueueItem])
async def list_gap_queue(
    user_id: AdminUserId,  # noqa: ARG001
    db: DbSession,
    status: str | None = "open",
    origin: str | None = None,
    limit: int = 100,
) -> list[GapQueueItem]:
    """gap 队列查询。status='all' 或空字符串 → 全部状态。"""
    from app.services.growth.gap_queue import list_gaps

    effective_status: str | None = status
    if status in (None, "", "all"):
        effective_status = None
    rows = await list_gaps(db=db, status=effective_status, origin=origin, limit=limit)
    return [GapQueueItem(**r) for r in rows]


@router.put("/gap-queue/{item_id}", response_model=GapQueueItem)
async def update_gap_status(
    item_id: int,
    payload: GapQueueUpdate,
    user_id: AdminUserId,
    db: DbSession,
) -> GapQueueItem:
    """变更 gap 项状态(open/in_progress/resolved/dismissed/reopened)。"""
    from app.services.growth.gap_queue import update_status

    try:
        row = await update_status(
            db=db,
            gap_id=item_id,
            status=payload.status,
            resolved_by=user_id,
            resolved_via_ingest_job_id=payload.resolved_via_ingest_job_id,
        )
    except LookupError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    db.commit()
    return GapQueueItem(**row)


# ===== 用户管理 =====
from typing import Literal as _Literal  # noqa: E402

from pydantic import BaseModel as _BaseModel  # noqa: E402

from app.models.user import User as _User  # noqa: E402
from app.schemas.auth import UserInfo  # noqa: E402


def _user_info(u: _User) -> UserInfo:
    return UserInfo(
        id=u.id,
        username=u.username,
        email=u.email,
        role=u.role,
        status=u.status,
        display_name=u.display_name,
        created_at=u.created_at,
    )


class UserWithStats(UserInfo):
    qa_count: int = 0


@router.get("/users", response_model=list[UserWithStats])
async def list_users(user_id: AdminUserId, db: DbSession) -> list[UserWithStats]:  # noqa: ARG001
    from sqlalchemy import func as _sql_func

    from app.models.qa import QaMessage as _QaMessage

    rows = db.query(_User).order_by(_User.id.asc()).all()
    # 每用户累计提问数(role=user 消息),一次聚合查询
    counts = dict(
        db.query(_QaMessage.user_id, _sql_func.count(_QaMessage.id))
        .filter(_QaMessage.role == "user")
        .group_by(_QaMessage.user_id)
        .all()
    )
    return [
        UserWithStats(
            **_user_info(u).model_dump(),
            qa_count=int(counts.get(u.id, 0)),
        )
        for u in rows
    ]


@router.get("/users/pending", response_model=list[UserInfo])
async def list_pending_users(user_id: AdminUserId, db: DbSession) -> list[UserInfo]:  # noqa: ARG001
    """当前注册即激活,无 pending 状态;返回空列表占位。"""
    return []


class _UserStatusUpdate(_BaseModel):
    status: _Literal["active", "disabled"]


@router.put("/users/{target_id}", response_model=UserInfo)
async def update_user_status(
    target_id: int,
    payload: _UserStatusUpdate,
    user_id: AdminUserId,
    db: DbSession,
) -> UserInfo:
    if target_id == user_id:
        raise HTTPException(status_code=400, detail="不能修改自己的状态")
    u = db.get(_User, target_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    u.status = payload.status
    db.commit()
    db.refresh(u)
    return _user_info(u)


@router.delete("/users/{target_id}", response_model=OkResponse)
async def delete_user(
    target_id: int,
    user_id: AdminUserId,
    db: DbSession,
) -> OkResponse:
    if target_id == user_id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    u = db.get(_User, target_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.delete(u)
    db.commit()
    return OkResponse(ok=True, message=f"user {target_id} deleted")


# ===== ingestion 调参 =====


class IngestionConfig(_BaseModel):
    max_raw_md_chars: int = 1_000_000
    llm_concurrency: int = 5  # Step2b/2c 同时在飞的 LLM 调用上限;过大触发 provider 限流


@router.get("/ingestion-config", response_model=IngestionConfig)
async def get_ingestion_config(user_id: AdminUserId) -> IngestionConfig:  # noqa: ARG001
    v = await config_store.get("ingestion.max_raw_md_chars")
    try:
        n = int(v) if (v and str(v).strip()) else 1_000_000
    except ValueError:
        n = 1_000_000
    c = await config_store.get("ingestion.llm_concurrency")
    try:
        cc = int(c) if (c and str(c).strip()) else 5
    except ValueError:
        cc = 5
    return IngestionConfig(max_raw_md_chars=n, llm_concurrency=cc)


@router.put("/ingestion-config", response_model=OkResponse)
async def put_ingestion_config(
    payload: IngestionConfig, user_id: AdminUserId
) -> OkResponse:
    n = max(1000, min(payload.max_raw_md_chars, 2_000_000))
    cc = max(1, min(payload.llm_concurrency, 50))
    await config_store.set_value(
        "ingestion.max_raw_md_chars", str(n), encrypted=False, updated_by=user_id
    )
    await config_store.set_value(
        "ingestion.llm_concurrency", str(cc), encrypted=False, updated_by=user_id
    )
    return OkResponse(
        ok=True,
        message=f"ingestion.max_raw_md_chars={n} llm_concurrency={cc}",
    )


# ===== alias-normalize:扫全 wiki 把历史 [[member]] 改成 [[canonical]] =====


class AliasNormalizeReport(_BaseModel):
    pages_scanned: int
    pages_rewritten: int
    links_replaced: int
    examples: list[str]  # 前 20 条 'old → new (页 X)'
    db_resync: dict


@router.post("/wiki/alias-normalize", response_model=AliasNormalizeReport)
async def alias_normalize(
    user_id: AdminUserId, db: DbSession,  # noqa: ARG001
    dry_run: bool = False,
) -> AliasNormalizeReport:
    """按 .wiki-schema.md 别名表,扫全 wiki/* 把 [[member]] 改成 [[canonical]]。

    - dry_run=true:只统计不写盘
    - 改完同步 wiki_pages / wiki_links 索引
    """
    from pathlib import Path

    from app.core.config import settings
    from app.services.ingestion.pipeline import (
        _LINK_RE,
        _load_alias_member_map,
        _sync_db_indices,
    )
    from app.services.wiki.fs_io import atomic_write
    from app.services.wiki.frontmatter_io import parse_text, serialize

    wiki_root = settings.wiki_root_path
    wiki_dir = Path(wiki_root) / "wiki"
    member_to_canonical = _load_alias_member_map(wiki_root)
    if not member_to_canonical:
        return AliasNormalizeReport(
            pages_scanned=0, pages_rewritten=0, links_replaced=0,
            examples=[], db_resync={},
        )

    pages_scanned = 0
    pages_rewritten = 0
    links_replaced = 0
    examples: list[str] = []
    written_paths: list[str] = []

    for sub in ("entities", "topics", "sources", "queries", "synthesis", "comparisons"):
        d = wiki_dir / sub
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md")):
            pages_scanned += 1
            text = p.read_text(encoding="utf-8")
            try:
                fm, body = parse_text(text)
            except Exception:  # noqa: BLE001
                continue
            page_replaced = 0
            page_examples: list[tuple[str, str]] = []

            def repl(m):
                nonlocal page_replaced
                tgt = m.group(1).strip()
                anchor = ""
                if "#" in tgt:
                    tgt, anchor = tgt.split("#", 1)
                    tgt = tgt.strip()
                    anchor = "#" + anchor
                canon = member_to_canonical.get(tgt)
                if canon and canon != tgt:
                    page_replaced += 1
                    page_examples.append((tgt, canon))
                    return f"[[{canon}{anchor}]]"
                return m.group(0)

            new_body = _LINK_RE.sub(repl, body)
            if page_replaced > 0:
                pages_rewritten += 1
                links_replaced += page_replaced
                for old, new in page_examples[:3]:
                    if len(examples) < 20:
                        examples.append(f"{old} → {new} (页 {p.stem})")
                if not dry_run:
                    await atomic_write(p, serialize(fm, new_body))
            written_paths.append(f"wiki/{sub}/{p.name}")

    db_stat: dict = {}
    if not dry_run and pages_rewritten > 0:
        # 跑一次 _sync_db_indices,把 wiki_pages/wiki_links 重建
        db_stat = _sync_db_indices(db, wiki_root=wiki_root, written_paths=written_paths)
        db.commit()

    return AliasNormalizeReport(
        pages_scanned=pages_scanned,
        pages_rewritten=pages_rewritten,
        links_replaced=links_replaced,
        examples=examples,
        db_resync=db_stat,
    )


# ===== dashboard 指标 =====
@router.get("/dashboard/metrics")
async def dashboard_metrics(user_id: AdminUserId, db: DbSession):  # noqa: ARG001
    """管理控制台:用户与活动指标。"""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func as sql_func

    from app.models.announcement import Announcement
    from app.models.qa import QaMessage
    from app.models.user import User
    from app.models.wiki_page import WikiPage
    from app.schemas.admin_metrics import DailyCount, DashboardMetrics, RecentUser

    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)
    fourteen_days_ago = now - timedelta(days=14)

    total_users = db.query(sql_func.count(User.id)).scalar() or 0
    new_users_7d = (
        db.query(sql_func.count(User.id))
        .filter(User.created_at >= seven_days_ago)
        .scalar()
        or 0
    )
    active_users_7d = (
        db.query(sql_func.count(User.id))
        .filter(User.last_login_at.is_not(None))
        .filter(User.last_login_at >= seven_days_ago)
        .scalar()
        or 0
    )
    total_qa_30d = (
        db.query(sql_func.count(QaMessage.id))
        .filter(QaMessage.created_at >= thirty_days_ago)
        .filter(QaMessage.role == "user")
        .scalar()
        or 0
    )
    total_qa_all = (
        db.query(sql_func.count(QaMessage.id))
        .filter(QaMessage.role == "user")
        .scalar()
        or 0
    )
    total_wiki_pages = db.query(sql_func.count(WikiPage.id)).scalar() or 0
    total_announcements = db.query(sql_func.count(Announcement.id)).scalar() or 0

    # 近 14 天每日 QA 数(按 user 消息计;补全无活动的日期为 0)
    raw = (
        db.query(
            sql_func.date(QaMessage.created_at).label("d"),
            sql_func.count(QaMessage.id),
        )
        .filter(QaMessage.created_at >= fourteen_days_ago)
        .filter(QaMessage.role == "user")
        .group_by(sql_func.date(QaMessage.created_at))
        .all()
    )
    by_date: dict[str, int] = {}
    for d, c in raw:
        key = d.isoformat() if hasattr(d, "isoformat") else str(d)
        by_date[key] = int(c)
    qa_daily: list[DailyCount] = []
    for i in range(13, -1, -1):
        day = (now - timedelta(days=i)).date().isoformat()
        qa_daily.append(DailyCount(date=day, count=by_date.get(day, 0)))

    # 最近 5 位有登录的用户
    recent_rows = (
        db.query(User)
        .filter(User.last_login_at.is_not(None))
        .order_by(User.last_login_at.desc())
        .limit(5)
        .all()
    )
    recent_active = [
        RecentUser(
            id=u.id,
            username=u.username,
            email=u.email,
            role=u.role,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
        )
        for u in recent_rows
    ]

    return DashboardMetrics(
        total_users=int(total_users),
        new_users_7d=int(new_users_7d),
        active_users_7d=int(active_users_7d),
        total_qa_30d=int(total_qa_30d),
        total_qa_all=int(total_qa_all),
        total_wiki_pages=int(total_wiki_pages),
        total_announcements=int(total_announcements),
        qa_daily_14d=qa_daily,
        recent_active_users=recent_active,
    )
