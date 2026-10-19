"""generate_chart 工具:以提问用户的身份调用智绘图平台服务端绘制真实图表。

账号体系:知识库门户是唯一用户源,智绘图通过 SSO find-or-create 同步账号。
本工具拿当前请求的 kgqa 用户 token,调智绘图 `POST /auth/sso-token` 换取该用户
在智绘图的 JWT(服务间 SSO,与浏览器 /sso 同一套 find-or-create 逻辑),再:

  1. `POST /charts/generate` 服务端出图(matplotlib 引擎模板,返回 base64 PNG)
  2. `POST /charts` 把图表存进该用户的「我的图表」(留同步记录,可去平台继续编辑)

生成的图片落盘 {cache_dir}/genimg/{image_id}.png,复用 generate_image 的文件服务
与前端 {{image:id}} 占位管道;chart 元信息带编辑器路径:直达已保存图表的编辑器
(drawio → /drawio-editor/{id},其余 → /chart-editor/{id}),保存失败按引擎降级
/form 或 /mode-select。修改图表 = 改参重画并新建记录,旧版本保留可回看。

无需管理员配置账号——谁提问就以谁的身份出图,管理后台只配平台地址。
"""

from __future__ import annotations

import base64
import time
import uuid

import httpx
from loguru import logger

from app.services.admin import config_store  # noqa: F401  (保留:配置读取入口一致性)
from app.services.tools.base import Tool, breaker
from app.services.tools.chart_templates import (
    coerce_chart_params,
    fetch_template_brief,
    param_schema_for_template,
    schema_text_for_template,
    zhihuitu_base_url,
)
from app.services.tools.imagegen import genimg_dir

# 用户 token 交换缓存:{(base_url, kgqa_token): (zhihuitu_token, 取得时刻)}
# 智绘图 token 有效期 7 天;这里短缓存 30 分钟,保证角色/禁用状态及时同步。
_TOKEN_TTL_SECONDS = 1800.0
_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_TOKEN_CACHE_MAX = 500

_HTTP_TIMEOUT = httpx.Timeout(connect=8.0, read=25.0, write=10.0, pool=8.0)


def invalidate_token_cache() -> None:
    """后台改绘图平台配置后调用,强制下次重新换 token。"""
    _TOKEN_CACHE.clear()


def _prune_token_cache() -> None:
    if len(_TOKEN_CACHE) <= _TOKEN_CACHE_MAX:
        return
    now = time.monotonic()
    for key in [k for k, (_, ts) in _TOKEN_CACHE.items() if now - ts >= _TOKEN_TTL_SECONDS]:
        _TOKEN_CACHE.pop(key, None)
    while len(_TOKEN_CACHE) > _TOKEN_CACHE_MAX:
        _TOKEN_CACHE.pop(next(iter(_TOKEN_CACHE)), None)


async def exchange_user_token(
    client: httpx.AsyncClient, base_url: str, kgqa_token: str, *, force: bool = False
) -> str:
    """kgqa 用户 token → 智绘图用户 token(账号同步);失败抛 RuntimeError。"""
    if not kgqa_token:
        raise RuntimeError("缺少用户登录态,无法以用户身份调用绘图平台")
    key = (base_url, kgqa_token)
    cached = _TOKEN_CACHE.get(key)
    if not force and cached and time.monotonic() - cached[1] < _TOKEN_TTL_SECONDS:
        return cached[0]
    resp = await client.post(f"{base_url}/api/v1/auth/sso-token", json={"token": kgqa_token})
    if resp.status_code >= 400:
        # 401 = 用户 token 失效,属用户态问题,不熔断整个工具;5xx/404(未部署)才值得熔断观察
        if resp.status_code != 401:
            breaker.maybe_trip_for_status("generate_chart", resp.status_code)
        raise RuntimeError(f"绘图平台账号同步失败 HTTP {resp.status_code}")
    body = resp.json()
    token = str((body.get("data") or {}).get("token") or body.get("token") or "")
    if not token:
        raise RuntimeError(f"绘图平台 sso-token 响应缺 token: {str(body)[:150]}")
    _prune_token_cache()
    _TOKEN_CACHE[key] = (token, time.monotonic())
    return token


async def _post_generate(
    client: httpx.AsyncClient, base_url: str, token: str, template_id: str, params: dict
) -> httpx.Response:
    return await client.post(
        f"{base_url}/api/v1/charts/generate",
        json={"templateId": template_id, "params": params},
        headers={"Authorization": f"Bearer {token}"},
    )


def _chart_data_for_engine(engine: str, params: dict, image_data_uri: str) -> dict | None:
    """按引擎构造「我的图表」记录的 chartData,保证智绘图编辑器能原生打开。

    - matplotlib:编辑器读 chartData.params 回填表单、image_base64 做预览
    - vue-component:编辑器把 chartData 整体喂给 Vue 组件(params 即组件数据)
    - drawio:同样存原始表单数据;drawio-editor 打开时若无 xml 会用模板的
      buildMxGraphXml 构建器从 chartData 现场生成画布
    """
    if engine == "matplotlib":
        return {"params": params, "image_base64": image_data_uri}
    return dict(params)


_SHAPE_ERROR_MARKERS = (
    "必须是对象",
    "必须是数组",
    "缺少",
    "缺少 label",
    "缺少 name",
    "不是对象",
    "应为对象",
    "should be object",
    "must be object",
    "must be an object",
    "expected object",
)


def _looks_like_shape_error(err_text: str) -> bool:
    text = (err_text or "").lower()
    return any(m.lower() in text for m in _SHAPE_ERROR_MARKERS)


def _editor_path(engine: str, template_id: str, chart_id: int | None) -> str:
    """预览面板「前往编辑器」的目标路径:直达用户自己那张图的编辑器。

    保存失败时降级:drawio → 数据录入表单页(/form),其余 → 模式选择页。
    """
    if chart_id:
        return f"/drawio-editor/{chart_id}" if engine == "drawio" else f"/chart-editor/{chart_id}"
    return f"/form/{template_id}" if engine == "drawio" else f"/mode-select/{template_id}"


async def _save_chart_record(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    *,
    template_id: str,
    title: str,
    chart_data: dict,
) -> int | None:
    """把图表存进用户的「我的图表」,返回 chart id;失败返回 None(不影响出图)。"""
    try:
        resp = await client.post(
            f"{base_url}/api/v1/charts",
            json={
                "title": title,
                "templateId": template_id,
                "chartData": chart_data,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code >= 400:
            logger.warning("save chart record failed: HTTP {}", resp.status_code)
            return None
        data = resp.json().get("data") or {}
        return int(data["id"]) if data.get("id") is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("save chart record failed: {!r}", exc)
        return None


async def generate_chart_image(
    template_id: str,
    params: dict,
    *,
    kgqa_token: str,
    title: str = "",
    engine: str = "matplotlib",
) -> dict:
    """以用户身份服务端出图并落盘 + 存「我的图表」。

    每次调用都新建一条记录:修改图表时旧记录保持原样,用户仍可从旧图的
    「前往编辑器」回看历史版本,不会被新版本覆盖。
    返回 {"image_id", "summary", "chart_id"(可能为 None)};异常向上抛。
    """
    base_url = zhihuitu_base_url()
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        token = await exchange_user_token(client, base_url, kgqa_token)
        resp = await _post_generate(client, base_url, token, template_id, params)
        if resp.status_code == 401:
            # 缓存的智绘图 token 失效:强制重新换一次
            token = await exchange_user_token(client, base_url, kgqa_token, force=True)
            resp = await _post_generate(client, base_url, token, template_id, params)
        if resp.status_code >= 400:
            if resp.status_code != 401:
                breaker.maybe_trip_for_status("generate_chart", resp.status_code)
            raise RuntimeError(f"智绘图出图 HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        if body.get("code") not in (None, 200):
            raise RuntimeError(f"智绘图出图失败: {str(body.get('message'))[:200]}")
        data = body.get("data") or {}
        image_data_uri = str(data.get("image_base64") or "")
        if not image_data_uri:
            raise RuntimeError("智绘图出图响应缺 image_base64")
        chart_data = _chart_data_for_engine(engine, params, image_data_uri)
        chart_id = None
        if chart_data is not None:
            chart_id = await _save_chart_record(
                client,
                base_url,
                token,
                template_id=template_id,
                title=title or template_id,
                chart_data=chart_data,
            )
    image_b64 = image_data_uri
    if image_b64.startswith("data:") and "," in image_b64:
        image_b64 = image_b64.split(",", 1)[1]
    content = base64.b64decode(image_b64)
    image_id = f"img_{uuid.uuid4().hex[:12]}"
    (genimg_dir() / f"{image_id}.png").write_bytes(content)
    return {"image_id": image_id, "summary": data.get("summary") or {}, "chart_id": chart_id}


class GenerateChartTool(Tool):
    name = "generate_chart"
    timeout = 30.0  # 含换 token + matplotlib 出图 + 保存记录

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "只画目录里的专业 IE 图(帕累托/动素/人机/双手/流程程序/山积/甘特/VSM/路线图/雷达等),"
                    "服务端出 PNG 并存进用户「我的图表」,可去编辑器、部分可导出 Excel。"
                    "用户点名这些图,或主题就是在讲这张方法图时必须用本工具,禁止改用 generate_image。"
                    "开放式可视化且未点名模板时不要乱挑目录里无关的图。"
                    "params 必须按 schema 填对象数组(stages/phases/steps 每项是对象,禁止传字符串数组);"
                    "没有 schema 也可以先调,工具会返回 schema。"
                    "用户没给数据时用贴合当前问答主题的示例数据。"
                    "修改本会话已生成的图表时,以旧参数为底稿改好后重新调用(新图新记录,旧版保留)。"
                    "成功后会返回真实 image_id(形如 img_ 后跟 12 位十六进制);"
                    "把这个真实 id 写进正文占位,没拿到真实 id 就不要写占位。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "template_id": {
                            "type": "string",
                            "description": "模板 id,如 pareto / fishbone / yamazumi",
                        },
                        "params": {
                            "type": "object",
                            "description": "按模板参数 schema 构造的绘图参数(含标题与数据)",
                        },
                        "caption": {
                            "type": "string",
                            "description": "图表的中文一句话说明(展示给用户,说明图里是什么/示例数据来源)",
                        },
                    },
                    "required": ["template_id", "params", "caption"],
                },
            },
        }

    def available(self) -> bool:
        # 账号走请求用户的登录态同步,无需额外配置;只受熔断控制
        return not breaker.is_open(self.name)

    async def run(self, args: dict, ctx: dict) -> dict:
        template_id = str(args.get("template_id") or "").strip()
        params = args.get("params")
        caption = str(args.get("caption") or "").strip()
        if not template_id:
            return {"error": "template_id 不能为空,请从用户消息的模板目录里选一个"}
        if not isinstance(params, dict) or not params:
            schema_text = await schema_text_for_template(template_id)
            if schema_text:
                return {
                    "error": (
                        "params 不能为空,请按下面的 schema 构造后重新调用 generate_chart,"
                        f"不要改用 generate_image,不要写 {{{{image:...}}}} 占位。\n{schema_text}"
                    )
                }
            return {"error": "template_id 与 params 不能为空,params 必须按模板参数 schema 构造"}
        kgqa_token = str(ctx.get("kgqa_token") or "")
        if not kgqa_token:
            return {"error": "缺少用户登录态,无法调用绘图平台。请基于已有材料直接作答。"}
        base_url = zhihuitu_base_url()
        brief = await fetch_template_brief(base_url, template_id)
        template_name = (brief or {}).get("name") or template_id
        engine = (brief or {}).get("engine") or "matplotlib"
        last_exc: Exception | None = None
        result = None
        try:
            result = await generate_chart_image(
                template_id,
                params,
                kgqa_token=kgqa_token,
                title=caption or f"{template_name}(问答生成)",
                engine=engine,
            )
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            err_text = str(exc)
            # 400 类型错:字符串数组 / 缺 label → 服务端按 schema 纠一次再出图
            if _looks_like_shape_error(err_text):
                schema = await param_schema_for_template(template_id)
                coerced, did = coerce_chart_params(params, schema)
                if did:
                    logger.info(
                        "generate_chart coerce retry: template={} err={}",
                        template_id,
                        err_text[:80],
                    )
                    try:
                        result = await generate_chart_image(
                            template_id,
                            coerced,
                            kgqa_token=kgqa_token,
                            title=caption or f"{template_name}(问答生成)",
                            engine=engine,
                        )
                        params = coerced
                        last_exc = None
                    except Exception as retry_exc:  # noqa: BLE001
                        last_exc = retry_exc
        if last_exc is not None:
            logger.warning("generate_chart failed: template={} err={!r}", template_id, last_exc)
            schema_text = await schema_text_for_template(template_id)
            schema_note = (
                f" 请按下面的 schema 改好 params 后再次调用 generate_chart,不要改用 generate_image。\n{schema_text}"
                if schema_text
                else "可在正文提示用户前往绘图平台亲手绘制。"
            )
            return {
                "error": (
                    f"图表生成失败({str(last_exc)[:120]})。请不要写 {{{{image:...}}}} 占位。"
                    f"{schema_note}"
                )
            }
        image_id = result["image_id"]
        chart_id = result.get("chart_id")
        export_formats = [str(x) for x in ((brief or {}).get("export_formats") or [])]
        chart_info = {
            "template_id": template_id,
            "name": template_name,
            "engine": engine,
            "base_url": base_url,
            # 已存进「我的图表」→ 直达该图表的编辑器(drawio 走 drawio-editor);
            # 保存失败 → drawio 落数据表单页、其余落模式选择页兜底
            "editor_path": _editor_path(engine, template_id, chart_id),
            "chart_id": chart_id,
            "export_formats": export_formats,
            # 本次绘图的完整参数:落库进消息 meta,后续追问「修改图表」时
            # 注入给模型在此基础上改参重画
            "params": params,
        }
        ctx.setdefault("images", {})[image_id] = {
            "image_id": image_id,
            "status": "ready",
            "prompt": caption or template_name,
            "caption": caption,
            "chart": chart_info,
        }
        ctx.setdefault("events", []).append(
            {
                "event": "image_ready",
                "data": {
                    "image_id": image_id,
                    "url": f"/api/v1/files/genimg/{image_id}.png",
                    "caption": caption,
                    "chart": chart_info,
                },
            }
        )
        saved_note = "图表已同步保存到用户在绘图平台的「我的图表」。" if chart_id else ""
        return {
            "image_id": image_id,
            "status": "ready",
            "summary": result["summary"],
            "note": (
                f"图表已生成,请在正文合适位置写占位标记 {{{{image:{image_id}}}}} 展示它,"
                f"并结合 summary 里的统计结论解说图表。{saved_note}"
            ),
        }
