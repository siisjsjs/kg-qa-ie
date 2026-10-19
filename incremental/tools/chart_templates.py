"""recommend_chart_templates 工具:智绘图模板推荐(改造计划 §5.4)。

模板列表来自 zhihuitu 实时公开 API(内存缓存 10 分钟),匹配打分基于返回的
name/description/tags/category 动态计算,缩略图走 zhihuitu 公开缩略图接口——
智绘图上架新模板后 kgqa 侧零代码、零部署自动生效(用户硬性要求)。
"""

from __future__ import annotations

import asyncio
import json
import re
import time

import httpx

from app.core.exceptions import ProviderError
from app.services.admin import config_store
from app.services.tools.base import Tool, breaker

DEFAULT_ZHIHUITU_BASE = "https://www.draw.example.com"
_CACHE_TTL_SECONDS = 600.0
# (取回时刻, base_url, 模板列表)
_TEMPLATE_CACHE: dict[str, tuple[float, list[dict]]] = {}
# 模板详情缓存(含 paramSchema):{(base_url, template_id): (取回时刻, 详情)}
_DETAIL_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
# paramSchema 注入 prompt 时的长度上限(防止个别模板 schema 过大撑爆上下文)
_SCHEMA_PROMPT_MAX_CHARS = 1500


def zhihuitu_base_url() -> str:
    return (
        (config_store.get_sync("zhihuitu.base_url") or "").strip().rstrip("/")
        or DEFAULT_ZHIHUITU_BASE
    )


def invalidate_template_cache() -> None:
    _TEMPLATE_CACHE.clear()
    _DETAIL_CACHE.clear()


async def _fetch_templates(base_url: str, timeout: float) -> list[dict]:
    cached = _TEMPLATE_CACHE.get(base_url)
    now = time.monotonic()
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        resp = await client.get(f"{base_url}/api/v1/templates")
    if resp.status_code >= 400:
        breaker.maybe_trip_for_status("recommend_chart_templates", resp.status_code)
        raise ProviderError(f"zhihuitu templates HTTP {resp.status_code}")
    data = resp.json()
    if isinstance(data, list):
        items = data
    else:
        # zhihuitu 标准包装 {code, message, data: [...]};兼容 items/templates 变体
        for key in ("data", "items", "templates"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
        else:
            items = []
    templates = [
        {
            "id": str(t.get("id") or ""),
            "name": str(t.get("name") or ""),
            "engine": str(t.get("engine") or ""),
            "category": str(t.get("category") or ""),
            "description": str(t.get("description") or ""),
            "tags": [str(x) for x in (t.get("tags") or [])],
            # 列表接口的 config 里直接带 paramSchema,省一次详情请求
            "param_schema": (t.get("config") or {}).get("paramSchema")
            if isinstance(t.get("config"), dict)
            else None,
            # 完整参数示例(meta.json 的 example.data):注入给模型时
            # 照示例结构填 params,避免漏填 matrix 类字段(如雷达图 values)
            "example": (t.get("config") or {}).get("example")
            if isinstance(t.get("config"), dict)
            else None,
            "export_formats": [
                str(x)
                for x in ((t.get("config") or {}).get("exportFormats") or [])
            ]
            if isinstance(t.get("config"), dict)
            else [],
        }
        for t in items
        if t.get("id")
    ]
    _TEMPLATE_CACHE[base_url] = (now, templates)
    return templates


async def fetch_template_detail(base_url: str, template_id: str, *, timeout: float = 5.0) -> dict:
    """取单个模板详情(含 paramSchema),10 分钟缓存;失败抛异常。"""
    key = (base_url, template_id)
    cached = _DETAIL_CACHE.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        resp = await client.get(f"{base_url}/api/v1/templates/{template_id}")
    if resp.status_code >= 400:
        raise ProviderError(f"zhihuitu template detail HTTP {resp.status_code}")
    body = resp.json()
    detail = body.get("data") if isinstance(body.get("data"), dict) else body
    detail = detail if isinstance(detail, dict) else {}
    _DETAIL_CACHE[key] = (now, detail)
    return detail


async def fetch_template_brief(base_url: str, template_id: str) -> dict | None:
    """从列表缓存/详情接口取模板基本信息(name/engine 等);失败返回 None。"""
    cached = _TEMPLATE_CACHE.get(base_url)
    if cached:
        for tpl in cached[1]:
            if tpl["id"] == template_id:
                return tpl
    try:
        detail = await fetch_template_detail(base_url, template_id)
    except Exception:  # noqa: BLE001
        return None
    config = detail.get("config") if isinstance(detail.get("config"), dict) else {}
    return {
        "id": template_id,
        "name": str(detail.get("name") or ""),
        "engine": str(detail.get("engine") or ""),
        "category": str(detail.get("category") or ""),
        "description": str(detail.get("description") or ""),
        "tags": [str(x) for x in (detail.get("tags") or [])],
        "export_formats": [str(x) for x in (config.get("exportFormats") or [])],
    }


def _param_schema_of(detail: dict) -> dict | list | None:
    config = detail.get("config")
    if isinstance(config, dict) and config.get("paramSchema") is not None:
        return config.get("paramSchema")
    return detail.get("paramSchema")


def _item_field_map(spec: dict | None) -> dict:
    """取出 object / object[] / array 的子字段定义。

    兼容两种写法:
      - DSL:{name:{type,required}, nodes:{type:object[],items:{...}}}
      - JSON Schema 风格:{type:object, fields:{...}} / {type:array, items:{type:object,fields:{}}}
    """
    spec = spec if isinstance(spec, dict) else {}
    fields = spec.get("fields")
    if isinstance(fields, dict):
        return fields
    for key in ("items", "item"):
        items = spec.get(key)
        if not isinstance(items, dict):
            continue
        if items.get("type") == "object" and isinstance(items.get("fields"), dict):
            return items["fields"]
        if any(isinstance(v, dict) and v.get("type") for v in items.values()):
            return items
    return {}


def _items_shape(spec: dict, *, depth: int = 0) -> str:
    """一层结构摘要,如 {name*,color,nodes*:{id*,label*}}。"""
    mapping = _item_field_map(spec)
    if not mapping:
        return ""
    if depth >= 2:
        return "{…}"
    parts: list[str] = []
    for key, child in mapping.items():
        if not isinstance(child, dict):
            continue
        star = "*" if child.get("required") else ""
        ctype = str(child.get("type") or "string")
        if ctype in {"object", "object[]", "array"} and depth < 1:
            inner = _items_shape(child, depth=depth + 1)
            parts.append(f"{key}{star}:{inner or ctype}")
        else:
            parts.append(f"{key}{star}")
    return "{" + ",".join(parts) + "}" if parts else ""


def _translate_dsl_field(field: str, spec: dict | None) -> str:
    """把 zhihuitu 模板 DSL 的单个字段定义翻译成模型可直接执行的描述。

    matrix / string-list 是平台自定义类型,原样注入 JSON 时模型经常不会填
    (实测雷达图 values 被输出成空)。object[] 若不展开子字段,模型会把
    stages/phases 传成字符串数组,智绘图回 400。
    """
    spec = spec if isinstance(spec, dict) else {}
    t = str(spec.get("type") or "string")
    label = str(spec.get("label") or field)
    req = "必填" if spec.get("required") else "可选"
    shape = _items_shape(spec)
    if t in {"string-list", "string[]"}:
        desc = '字符串数组(JSON 数组),如 ["A","B"]'
    elif t == "matrix":
        rf = str(spec.get("rowsFrom") or "")
        cf = str(spec.get("colsFrom") or "")
        desc = f"二维数值数组:行数等于 {rf} 的个数,每行长度等于 {cf} 的个数,如 [[8,9,6]]"
    elif t == "number":
        desc = "数字"
    elif t == "string":
        desc = "字符串"
    elif t == "object":
        desc = f"对象{',' + shape if shape else ''},不要传字符串"
    elif t in {"object[]", "array"}:
        desc = f"对象数组,每项 {shape or '{…}'};禁止传字符串数组"
    else:
        desc = f"{t} 类型"
    return f"- {field} ({label},{req}):{desc}"


def compact_example_data(data, schema, *, max_list: int = 1, max_nested: int = 2):
    """嵌套模板只留 1 个阶段 + 2 个节点,避免 1500 字截断成半截。"""
    if not isinstance(data, dict) or not isinstance(schema, dict):
        return data
    out: dict = {}
    for field, spec in schema.items():
        if field not in data or not isinstance(spec, dict):
            continue
        val = data[field]
        t = str(spec.get("type") or "")
        if t in {"object[]", "array"} and isinstance(val, list):
            keep = max_nested if field in {"nodes", "connections", "tasks", "steps"} else max_list
            child_schema = _item_field_map(spec)
            sliced = []
            for item in val[:keep]:
                if isinstance(item, dict) and child_schema:
                    sliced.append(
                        compact_example_data(
                            item, child_schema, max_list=max_nested, max_nested=max_nested
                        )
                    )
                else:
                    sliced.append(item)
            out[field] = sliced
        elif t == "object" and isinstance(val, dict):
            out[field] = compact_example_data(
                val, spec.get("fields") or {}, max_list=max_list, max_nested=max_nested
            )
        else:
            out[field] = val
    return out


def render_schema_for_prompt(schema, example=None) -> str:
    """paramSchema → 注入 prompt 的紧凑说明(超长截断)。

    自定义类型翻译成模型可直接执行的描述;example 优先压成精简结构再注入,
    避免路线图完整示例被 1500 字上限截成半截。"""
    if schema is None:
        return ""
    parts: list[str] = []
    if isinstance(schema, dict):
        for field, spec in schema.items():
            parts.append(_translate_dsl_field(field, spec))
        text = "\n".join(parts)
    else:
        text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    example_data = None
    if isinstance(example, dict):
        example_data = example.get("data") if "data" in example else example
    if isinstance(example_data, dict) and example_data:
        if isinstance(schema, dict):
            example_data = compact_example_data(example_data, schema)
        text += (
            "\n精简参数示例(按此结构填写 params,可增减条目):"
            + json.dumps(example_data, ensure_ascii=False, separators=(",", ":"))
        )
    if len(text) > _SCHEMA_PROMPT_MAX_CHARS:
        text = text[:_SCHEMA_PROMPT_MAX_CHARS] + "…(已截断)"
    return text


_NAME_KEYS = ("name", "label", "title", "text")
_ID_RE = re.compile(r"[^\w]+", re.UNICODE)


def _default_id(text: str, index: int) -> str:
    cleaned = _ID_RE.sub("", str(text or ""))[:12]
    if cleaned.isascii() and cleaned:
        return cleaned
    return f"n{index + 1}"


def _wrap_string(text: str, item_schema: dict, *, index: int = 0) -> dict:
    """字符串 → 对象:填第一个名称字段,其余必填给可过校验的默认值。"""
    obj: dict = {}
    name_key = next((k for k in _NAME_KEYS if k in item_schema), None)
    if name_key is None:
        for key, spec in item_schema.items():
            if isinstance(spec, dict) and str(spec.get("type") or "string") == "string":
                name_key = key
                break
    if name_key:
        obj[name_key] = text
    else:
        obj["name"] = text
    for key, spec in item_schema.items():
        if key in obj or not isinstance(spec, dict) or not spec.get("required"):
            continue
        t = str(spec.get("type") or "")
        if key == "id":
            obj[key] = _default_id(text, index)
        elif t in {"object[]", "array"}:
            child = _item_field_map(spec)
            obj[key] = [_wrap_string(text, child, index=0)] if child else []
        elif t == "object":
            obj[key] = {}
        elif t == "number":
            obj[key] = index + 1 if key.lower().endswith("month") or key == "startMonth" else 1
        elif t in {"string-list", "string[]"}:
            obj[key] = [text]
        elif t == "string":
            obj[key] = text
    return obj


def coerce_chart_params(params: dict, schema) -> tuple[dict, bool]:
    """把字符串数组/字符串对象收成 schema 要求的对象结构。返回 (新 params, 是否改过)。"""
    if not isinstance(params, dict) or not isinstance(schema, dict):
        return params, False
    changed = False
    out = dict(params)

    def coerce_list(val, item_schema: dict, field: str):
        nonlocal changed
        if not isinstance(val, list):
            return val
        if val and all(isinstance(x, dict) for x in val):
            coerced = [coerce_obj(x, item_schema) for x in val]
            if coerced != val:
                changed = True
            return coerced
        if val and all(isinstance(x, (str, int, float)) for x in val):
            changed = True
            wrapped = [
                _wrap_string(str(x), item_schema, index=i) for i, x in enumerate(val)
            ]
            sibling = out.get("items") if field != "items" else None
            if isinstance(sibling, list):
                for i, extra in enumerate(sibling):
                    if i < len(wrapped) and isinstance(extra, dict):
                        wrapped[i] = {**wrapped[i], **extra}
            return wrapped
        return val

    def coerce_obj(obj, item_schema: dict):
        nonlocal changed
        if not isinstance(obj, dict) or not item_schema:
            return obj
        inner = dict(obj)
        for key, spec in item_schema.items():
            if key not in inner or not isinstance(spec, dict):
                continue
            t = str(spec.get("type") or "")
            if t in {"object[]", "array"}:
                inner[key] = coerce_list(inner[key], _item_field_map(spec), key)
            elif t == "object" and isinstance(inner[key], str):
                inner[key] = _wrap_string(inner[key], spec.get("fields") or {})
                changed = True
            elif t == "object" and isinstance(inner[key], dict):
                inner[key] = coerce_obj(inner[key], spec.get("fields") or {})
        return inner

    for field, spec in schema.items():
        if field not in out or not isinstance(spec, dict):
            continue
        t = str(spec.get("type") or "")
        if t in {"object[]", "array"}:
            out[field] = coerce_list(out[field], _item_field_map(spec), field)
        elif t == "object":
            if isinstance(out[field], str):
                out[field] = _wrap_string(out[field], spec.get("fields") or {})
                changed = True
            elif isinstance(out[field], dict):
                out[field] = coerce_obj(out[field], spec.get("fields") or {})
    return out, changed


async def param_schema_for_template(template_id: str, *, timeout: float = 5.0) -> dict | None:
    """取模板 paramSchema 字典,供纠参使用;失败返回 None。"""
    tid = (template_id or "").strip()
    if not tid:
        return None
    try:
        detail = await fetch_template_detail(zhihuitu_base_url(), tid, timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    schema = _param_schema_of(detail)
    return schema if isinstance(schema, dict) else None


def extract_example_data(example) -> dict | None:
    """从模板 example 取出可直接当 params 的字典。"""
    if not isinstance(example, dict):
        return None
    data = example.get("data") if "data" in example else example
    return data if isinstance(data, dict) and data else None


async def example_params_for_template(template_id: str, *, timeout: float = 5.0) -> dict | None:
    """取模板精简示例参数,供点名模板但模型没出图时服务端补画。"""
    tid = (template_id or "").strip()
    if not tid:
        return None
    try:
        detail = await fetch_template_detail(zhihuitu_base_url(), tid, timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    example = None
    config = detail.get("config") if isinstance(detail.get("config"), dict) else {}
    if config.get("example") is not None:
        example = config.get("example")
    elif detail.get("example") is not None:
        example = detail.get("example")
    data = extract_example_data(example)
    if not data:
        return None
    schema = _param_schema_of(detail)
    if isinstance(schema, dict):
        data = compact_example_data(data, schema) or data
    return data


def build_template_cards(templates: list[dict], base_url: str) -> list[dict]:
    """模板 → 前端模板卡(缩略图 + 编辑器路径);工具与检索预匹配共用。

    param_schema 只给模型用,不进前端事件/落库 meta(体积大且前端用不到)。
    """
    return [
        {
            **{k: v for k, v in tpl.items() if k not in ("param_schema", "example")},
            "preview_url": f"{base_url}/api/v1/templates/{tpl['id']}/preview",
            "editor_path": f"/mode-select/{tpl['id']}",
        }
        for tpl in templates
    ]


async def _schema_for(template: dict, base_url: str, *, timeout: float = 5.0):
    """取模板 paramSchema:优先列表接口自带,缺失时回退详情接口;失败返回 None。"""
    if template.get("param_schema") is not None:
        return template["param_schema"]
    try:
        detail = await fetch_template_detail(base_url, template["id"], timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    return _param_schema_of(detail)


async def _example_for(template: dict, base_url: str, *, timeout: float = 5.0):
    """取模板完整参数示例(meta.json 的 example):列表接口自带,缺失回退详情接口。"""
    if template.get("example") is not None:
        return template["example"]
    try:
        detail = await fetch_template_detail(base_url, template["id"], timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    config = detail.get("config")
    if isinstance(config, dict) and config.get("example") is not None:
        return config["example"]
    return detail.get("example")


def _match_by_name(template: dict, question_lower: str) -> bool:
    """模板名(或剥掉『图/分析/模型』类后缀的词干)出现在问题里,视为强匹配。

    例:问题「怎么做帕累托分析」命中「帕累托图」;「怎么计算生产线平衡」命中「产线平衡分析」。
    tags 里足够具体的词(「动作研究」)也可命中,跳过 IE 这类过短拉丁标签以免误伤。
    """
    name = template["name"].strip().lower()
    if name:
        stems = {name}
        for suffix in ("分析图", "分析", "模型", "图"):
            if name.endswith(suffix) and len(name) - len(suffix) >= 2:
                stems.add(name[: -len(suffix)])
        if any(stem in question_lower for stem in stems):
            return True
    for raw in template.get("tags") or []:
        tag = str(raw).strip().lower()
        if len(tag) < 2:
            continue
        if tag.isascii() and len(tag) < 4:
            continue
        if tag in question_lower:
            return True
    return False


def render_catalog_for_prompt(templates: list[dict]) -> str:
    """模板列表 → 注入 prompt 的短目录(id + 中文名 + 分类/一句用途,不含 schema)。"""
    lines: list[str] = []
    for tpl in templates:
        tid = str(tpl.get("id") or "").strip()
        name = str(tpl.get("name") or "").strip()
        if not tid or not name:
            continue
        category = str(tpl.get("category") or "").strip()
        desc = " ".join(str(tpl.get("description") or "").split())
        if len(desc) > 36:
            desc = desc[:36] + "…"
        mid = f"{tid} / {category}" if category else tid
        if desc:
            lines.append(f"- {name} ({mid}): {desc}")
        else:
            lines.append(f"- {name} ({mid})")
    return "\n".join(lines)


async def build_catalog_hint(*, timeout: float = 3.0) -> str:
    """拉绘图平台当前全部模板,拼成 skill 式短目录。失败返回空,不影响问答。"""
    try:
        templates = await _fetch_templates(zhihuitu_base_url(), timeout)
    except Exception:  # noqa: BLE001
        return ""
    catalog = render_catalog_for_prompt(templates)
    if not catalog:
        return ""
    return (
        "绘图平台当前全部可服务端出图的模板目录(随平台更新,以本列表为准):\n"
        f"{catalog}\n"
        "用户点名目录中的专业 IE 图(帕累托/动素/VSM/甘特/路线图/山积等)时必须 generate_chart,"
        "禁止改用 generate_image 冒充。"
        "开放式「做个可视化/教学用图」且未点名模板时:主题在讲某张 IE 方法图就走 generate_chart;"
        "要的是概念示意、场景、海报感才走 generate_image。"
        "禁止用知识库里另一个方法名去 recommend_chart_templates 跑题(例如学习路径题去画目视管理雷达图)。"
        "params 按命中模板的 schema 填;没有 schema 也可以先调 generate_chart,工具会返回 schema。"
    )


async def schema_text_for_template(template_id: str, *, timeout: float = 5.0) -> str:
    """取单个模板的 paramSchema/示例,供 generate_chart 参数错误时回传给模型。"""
    tid = (template_id or "").strip()
    if not tid:
        return ""
    try:
        detail = await fetch_template_detail(zhihuitu_base_url(), tid, timeout=timeout)
    except Exception:  # noqa: BLE001
        return ""
    example = None
    config = detail.get("config") if isinstance(detail.get("config"), dict) else {}
    if config.get("example") is not None:
        example = config.get("example")
    elif detail.get("example") is not None:
        example = detail.get("example")
    return render_schema_for_prompt(_param_schema_of(detail), example)


async def match_templates_for_question(question: str, *, timeout: float = 3.0) -> list[dict]:
    """确定性预匹配:问题文本里直接出现模板名 → 返回匹配模板(最多 3 个)。

    供 query.py 在 Agent 启动前调用:命中即自动推模板卡 + 给模型注入绘图提示,
    解决"模型基本不主动调用绘图工具"的问题。失败(网络/超时)一律返回空,不影响问答。
    """
    q = (question or "").strip().lower()
    if len(q) < 2:
        return []
    try:
        templates = await _fetch_templates(zhihuitu_base_url(), timeout)
    except Exception:  # noqa: BLE001
        return []
    return [tpl for tpl in templates if _match_by_name(tpl, q)][:3]


async def build_chart_hint(matched: list[dict], *, timeout: float = 3.0) -> str:
    """把预匹配到的模板组织成注入给模型的「绘图平台提示」文本。

    带 paramSchema 的模板(平台侧有服务端生成器)附上 schema,模型可直接调
    generate_chart 服务端画真图;没有 schema 的只提示可在绘图平台亲手绘制。
    """
    if not matched:
        return ""
    base_url = zhihuitu_base_url()
    lines: list[str] = [
        "检测到绘图平台有与本问题直接相关的图表模板(模板卡片已自动展示给用户):",
    ]
    schemas: dict[str, object] = {}
    results = await asyncio.gather(
        *(_schema_for(t, base_url, timeout=timeout) for t in matched),
        return_exceptions=True,
    )
    for tpl, res in zip(matched, results, strict=False):
        if res is not None and not isinstance(res, BaseException):
            schemas[tpl["id"]] = res
    examples: dict[str, object] = {}
    example_results = await asyncio.gather(
        *(_example_for(t, base_url, timeout=timeout) for t in matched),
        return_exceptions=True,
    )
    for tpl, res in zip(matched, example_results, strict=False):
        if res is not None and not isinstance(res, BaseException):
            examples[tpl["id"]] = res
    for tpl in matched:
        line = f"- {tpl['name']}(template_id={tpl['id']},engine={tpl['engine']})"
        schema_text = render_schema_for_prompt(schemas.get(tpl["id"]), examples.get(tpl["id"]))
        if schema_text:
            line += f"\n  参数 schema:{schema_text}"
        lines.append(line)
    if schemas:
        lines.append(
            "本轮必须调用 generate_chart(template_id, params, caption) 画出上列模板,"
            "不得只讲画法、不得假装上一张图就是本题的图。"
            "params 严格按 schema 构造,用户没给数据就用贴合当前问题的示例数据,"
            "caption 写中文说明;拿到真实 image_id 后写进正文占位,没拿到就不要写占位。"
        )
    else:
        lines.append(
            "这些模板不支持服务端出图,正文自然提及「可以在绘图平台用『XX』模板亲手绘制」即可,"
            "不要输出链接。"
        )
    return "\n".join(lines)


async def build_session_chart_hint(charts: list[dict], *, timeout: float = 3.0) -> str:
    """会话中已生成的图表 → 「图表修改提示」。

    charts: [{image_id, template_id, name, params}](时间正序,每模板取最新一次)。
    附上当前参数与 schema,明确指示模型:用户要求修改时改参重画,而不是说"图片不能改"。
    """
    if not charts:
        return ""
    base_url = zhihuitu_base_url()
    lines: list[str] = ["本会话此前已用绘图平台生成过以下图表:"]
    results = await asyncio.gather(
        *(
            _schema_for({"id": c["template_id"], "param_schema": None}, base_url, timeout=timeout)
            for c in charts
        ),
        return_exceptions=True,
    )
    for chart, res in zip(charts, results, strict=False):
        name = chart.get("name") or chart["template_id"]
        line = (
            f"- 「{name}」(template_id={chart['template_id']})\n"
            f"  当前参数:{json.dumps(chart.get('params') or {}, ensure_ascii=False, separators=(',', ':'))}"
        )
        if res is not None and not isinstance(res, BaseException):
            line += f"\n  参数 schema:{render_schema_for_prompt(res)}"
        lines.append(line)
    lines.append(
        "这些图表完全可以修改:用户若要求调整图(改数据/数值/标题/时间/步骤/结构等),"
        "必须再次调用 generate_chart——template_id 不变,params 以「当前参数」为底稿、"
        "按用户要求修改后完整传入(不能只传改动字段),caption 说明改了什么,"
        "正文用工具新返回的真实 image_id 写占位展示新图(新图另存一条记录,旧版本保留可回看)。"
        "严禁回答「图片无法修改」或只让用户自己去平台改。"
    )
    return "\n".join(lines)


def _score_template(template: dict, keywords: list[str]) -> float:
    """按 name/description/tags/category 匹配打分。"""
    score = 0.0
    name = template["name"].lower()
    desc = template["description"].lower()
    category = template["category"].lower()
    tags = [t.lower() for t in template["tags"]]
    for kw in keywords:
        k = kw.strip().lower()
        if not k:
            continue
        if k in name or name in k and name:
            score += 10.0
        if any(k in tag or tag in k for tag in tags if tag):
            score += 6.0
        if k in category and category:
            score += 3.0
        if k in desc:
            score += 2.0
    return score


class RecommendChartTemplatesTool(Tool):
    name = "recommend_chart_templates"
    timeout = 5.0

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "在智绘图平台查找可用的图表模板(帕累托图/鱼骨图/价值流图/甘特图等)。"
                    "当回答中提到的方法可以用图表工具绘制时调用,keywords 传方法名与图表类型。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "匹配关键词,如 [\"帕累托图\", \"质量分析\"]",
                        },
                    },
                    "required": ["keywords"],
                },
            },
        }

    def available(self) -> bool:
        # base_url 有默认值,只受熔断控制
        return not breaker.is_open(self.name)

    async def run(self, args: dict, ctx: dict) -> dict:
        keywords = [str(k) for k in (args.get("keywords") or []) if str(k).strip()]
        if not keywords:
            return {"error": "keywords 不能为空"}
        base_url = zhihuitu_base_url()
        templates = await _fetch_templates(base_url, self.timeout)
        scored = sorted(
            ((tpl, _score_template(tpl, keywords)) for tpl in templates),
            key=lambda x: -x[1],
        )
        top = [tpl for tpl, s in scored[:3] if s > 0]
        if not top:
            return {"result": "绘图平台没有匹配的模板。不必在回答中提及绘图平台。"}
        cards = build_template_cards(top, base_url)
        ctx.setdefault("events", []).append(
            {
                "event": "chart_templates",
                "data": {"base_url": base_url, "templates": cards},
            }
        )
        ctx["chart_templates"] = {"base_url": base_url, "templates": cards}
        # 带 paramSchema 的模板(平台侧有服务端生成器)补上 schema:
        # 模型可紧接着调 generate_chart 服务端画真图
        schema_lines: list[str] = []
        results = await asyncio.gather(
            *(_schema_for(t, base_url, timeout=self.timeout) for t in top),
            return_exceptions=True,
        )
        example_results = await asyncio.gather(
            *(_example_for(t, base_url, timeout=self.timeout) for t in top),
            return_exceptions=True,
        )
        examples = {
            tpl["id"]: res
            for tpl, res in zip(top, example_results, strict=False)
            if res is not None and not isinstance(res, BaseException)
        }
        for tpl, res in zip(top, results, strict=False):
            if res is None or isinstance(res, BaseException):
                continue
            schema_text = render_schema_for_prompt(res, examples.get(tpl["id"]))
            if schema_text:
                schema_lines.append(f"- {tpl['id']} 的参数 schema:{schema_text}")
        result_text = (
            "绘图平台有以下匹配模板(模板卡片会自动展示给用户,你只需在正文自然提及"
            "「可以用绘图平台的『XX』模板绘制」,不要输出链接):\n"
            + "\n".join(
                f"- {tpl['name']}(id={tpl['id']},engine={tpl['engine']},分类:{tpl['category']}):"
                f"{tpl['description'][:80]}"
                for tpl in top
            )
        )
        if schema_lines:
            result_text += (
                "\n其中带参数 schema 的模板可进一步调用 generate_chart(template_id, params, caption)"
                "服务端绘制真实图表插入正文(params 严格按 schema 构造,无数据时用合理示例数据):\n"
                + "\n".join(schema_lines)
            )
        return {"result": result_text}
