"""工具箱单元测试(改造计划 Phase 9:熔断器、配置层降级、证据池编号)。"""

from __future__ import annotations

import asyncio

import pytest

from app.services.admin import config_store
from app.services.query.evidence_pool import EvidencePool
from app.services.tools import base as tools_base
from app.services.tools import build_tools
from app.services.tools.base import breaker
from app.services.tools.chart_templates import (
    RecommendChartTemplatesTool,
    _score_template,
)
from app.services.tools.imagegen import GenerateImageTool
from app.services.tools.search_kb import SearchKBTool
from app.services.tools.web_search import WebSearchTool


@pytest.fixture(autouse=True)
def _reset_breaker():
    breaker._until.clear()
    yield
    breaker._until.clear()


# ---- 熔断器 ----


def test_breaker_trips_on_auth_status_and_expires(monkeypatch):
    assert breaker.is_open("web_search") is False
    assert breaker.maybe_trip_for_status("web_search", 500) is False
    assert breaker.is_open("web_search") is False
    assert breaker.maybe_trip_for_status("web_search", 401) is True
    assert breaker.is_open("web_search") is True
    # 时间推进到超过冷却期 → 自动恢复
    fake_now = [0.0]
    monkeypatch.setattr(tools_base.time, "monotonic", lambda: fake_now[0])
    breaker.trip("web_search", seconds=10)
    fake_now[0] = 5.0
    assert breaker.is_open("web_search") is True
    fake_now[0] = 11.0
    assert breaker.is_open("web_search") is False


def test_breaker_clear():
    breaker.trip("generate_image")
    assert breaker.is_open("generate_image") is True
    breaker.clear("generate_image")
    assert breaker.is_open("generate_image") is False


# ---- 配置层降级(available) ----


def test_web_search_unavailable_without_key():
    assert WebSearchTool().available() is False
    config_store.set_value_sync("tavily.api_key", "tvly-xxx", encrypted=True)
    assert WebSearchTool().available() is True
    config_store.set_value_sync("tavily.api_key", "", encrypted=True)
    assert WebSearchTool().available() is False


def test_imagegen_unavailable_without_full_config():
    assert GenerateImageTool().available() is False
    config_store.set_value_sync("imagegen.base_url", "https://api.example.com/v1")
    config_store.set_value_sync("imagegen.api_key", "sk-img", encrypted=True)
    assert GenerateImageTool().available() is False  # 缺 model
    config_store.set_value_sync("imagegen.model", "gpt-image-1")
    assert GenerateImageTool().available() is True


@pytest.mark.asyncio
async def test_generate_image_file_omits_response_format(monkeypatch, tmp_path):
    import app.services.tools.imagegen as ig

    captured: dict = {}

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"b64_json": "aGVsbG8="}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            return FakeResp()

    monkeypatch.setattr(ig, "_read_config", lambda: {
        "base_url": "https://www.mhcoding.ai",
        "api_key": "sk-test",
        "model": "gpt-image-2",
        "size": "1024x1024",
    })
    monkeypatch.setattr(ig, "genimg_dir", lambda: tmp_path)
    monkeypatch.setattr(ig.httpx, "AsyncClient", FakeClient)
    out = await ig.generate_image_file("img_aaaabbbbcccc", "a red circle")
    assert captured["url"].endswith("/v1/images/generations")
    assert "response_format" not in captured["json"]
    assert captured["json"]["model"] == "gpt-image-2"
    assert out.read_bytes() == b"hello"


def test_decode_image_item_data_url():
    import base64

    from app.services.tools.imagegen import _decode_image_item

    raw = base64.b64encode(b"pngdata").decode()
    assert _decode_image_item({"b64_json": raw}) == b"pngdata"
    assert _decode_image_item({"url": f"data:image/png;base64,{raw}"}) == b"pngdata"


def test_build_tools_respects_availability_and_breaker():
    names = {t.name for t in build_tools()}
    assert "search_kb" in names
    assert "recommend_chart_templates" in names
    assert "web_search" not in names  # 未配 key
    config_store.set_value_sync("tavily.api_key", "tvly-xxx", encrypted=True)
    assert "web_search" in {t.name for t in build_tools()}
    breaker.trip("web_search")
    assert "web_search" not in {t.name for t in build_tools()}
    breaker.trip("recommend_chart_templates")
    assert "recommend_chart_templates" not in {t.name for t in build_tools()}


# ---- EvidencePool ----


def test_evidence_pool_numbering_and_resolve():
    pool = EvidencePool()
    added = pool.add_chunks([
        {"chunk_id": "a::00", "slug": "a", "title": "A", "kind": "entity",
         "heading": "", "text": "ta", "line_start": 1, "line_end": 2},
        {"chunk_id": "b::01", "slug": "b", "title": "B", "kind": "topic",
         "heading": "步骤", "text": "tb", "line_start": 5, "line_end": 9},
    ])
    assert [it["ref"] for it in added] == ["E1", "E2"]
    # 去重:同 chunk 再加不产生新编号
    assert pool.add_chunks([{"chunk_id": "a::00", "slug": "a", "text": "ta"}]) == []
    # 跨轮接续
    added2 = pool.add_chunks([
        {"chunk_id": "c::00", "slug": "c", "title": "C", "kind": "source",
         "heading": "", "text": "tc", "line_start": 1, "line_end": 3},
    ])
    assert added2[0]["ref"] == "E3"
    web = pool.add_web_results([
        {"url": "https://x.com/1", "title": "T1", "content": "c1"},
        {"url": "https://x.com/1", "title": "dup", "content": "dup"},
        {"url": "https://x.com/2", "title": "T2", "content": "c2"},
    ])
    assert [it["ref"] for it in web] == ["W1", "W2"]
    assert pool.resolve("E2")["slug"] == "b"
    assert pool.resolve("w1")["url"] == "https://x.com/1"
    assert pool.resolve("E99") is None
    assert pool.resolve("X1") is None
    rendered = pool.render_for_prompt()
    assert "[E1] [[a]]" in rendered
    assert "[W2] T2" in rendered


# ---- search_kb ----


@pytest.mark.asyncio
async def test_search_kb_adds_evidence_with_alias_expansion(monkeypatch, tmp_path):
    calls: dict = {}

    async def fake_hybrid(**kwargs):
        calls.update(kwargs)
        return [{
            "chunk_id": "p::00", "slug": "p", "title": "P", "kind": "entity",
            "heading": "", "text": "body", "line_start": 1, "line_end": 2,
        }]

    import app.services.tools.search_kb as sk

    monkeypatch.setattr(sk, "hybrid_search", fake_hybrid)
    pool = EvidencePool()
    ctx = {"wiki_root": tmp_path, "alias_groups": [["帕累托图", "柏拉图", "排列图"]], "evidence": pool}
    out = await SearchKBTool().run({"query": "柏拉图 怎么画"}, ctx)
    # 别名扩展生效:关键词里应包含同组词
    assert any("帕累托图" in k for k in calls["keywords"])
    assert "E1" in out["result"]
    assert pool.resolve("E1")["slug"] == "p"


# ---- chart templates 打分 ----


def test_score_template_matches_name_tags():
    tpl = {
        "id": "pareto", "name": "帕累托图", "engine": "matplotlib",
        "category": "质量管理", "description": "80/20 分析常用图", "tags": ["质量", "排序分析"],
    }
    assert _score_template(tpl, ["帕累托图"]) > 0
    assert _score_template(tpl, ["质量"]) > 0
    assert _score_template(tpl, ["完全无关词汇"]) == 0


@pytest.mark.asyncio
async def test_chart_templates_top3_and_event(monkeypatch):
    import app.services.tools.chart_templates as ct

    async def fake_fetch(base_url, timeout):
        return [
            {"id": f"t{i}", "name": f"帕累托图{i}", "engine": "vue-component",
             "category": "质量", "description": "d", "tags": []}
            for i in range(5)
        ] + [
            {"id": "x", "name": "无关模板", "engine": "drawio",
             "category": "其他", "description": "n", "tags": []}
        ]

    monkeypatch.setattr(ct, "_fetch_templates", fake_fetch)
    ctx: dict = {}
    out = await RecommendChartTemplatesTool().run({"keywords": ["帕累托图"]}, ctx)
    assert "result" in out
    event = ctx["events"][0]
    assert event["event"] == "chart_templates"
    assert len(event["data"]["templates"]) == 3
    assert event["data"]["templates"][0]["preview_url"].endswith("/api/v1/templates/t0/preview")
    assert event["data"]["templates"][0]["editor_path"] == "/mode-select/t0"


# ---- imagegen 异步 pending ----


# ---- generate_chart(智绘图服务端出图) ----


def test_strip_fake_image_placeholders():
    from app.api.v1.query import _strip_fake_image_placeholders

    real = "img_abc123def456"
    text = (
        "上面是示例：\n\n{{image:image_id}}\n\n"
        f"真图 {{{{image:{real}}}}} 保留。\n"
        "{{image:not-a-real-id}}"
    )
    out = _strip_fake_image_placeholders(text, {real})
    assert "{{image:image_id}}" not in out
    assert "{{image:not-a-real-id}}" not in out
    assert f"{{{{image:{real}}}}}" in out


def test_generate_chart_available_only_gated_by_breaker():
    from app.services.tools.chart_generate import GenerateChartTool

    # 账号走请求用户的登录态同步,无需任何配置即可用
    assert GenerateChartTool().available() is True
    breaker.trip("generate_chart")
    assert GenerateChartTool().available() is False


@pytest.mark.asyncio
async def test_generate_chart_run_success(monkeypatch):
    import app.services.tools.chart_generate as cg

    seen: dict = {}

    async def fake_generate(template_id, params, *, kgqa_token, title="", engine="matplotlib"):
        seen.update(template_id=template_id, kgqa_token=kgqa_token, title=title, engine=engine)
        return {
            "image_id": "img_abc123def456",
            "summary": {"最高项": "外观划伤"},
            "chart_id": 77,
        }

    async def fake_brief(base_url, template_id):
        return {"id": template_id, "name": "帕累托图", "engine": "matplotlib",
                "export_formats": ["xlsx", "png"]}

    monkeypatch.setattr(cg, "generate_chart_image", fake_generate)
    monkeypatch.setattr(cg, "fetch_template_brief", fake_brief)
    ctx: dict = {"kgqa_token": "kg-user-token"}
    out = await cg.GenerateChartTool().run(
        {"template_id": "pareto", "params": {"categories": ["a"], "values": [1]},
         "caption": "示例帕累托图"},
        ctx,
    )
    assert seen["kgqa_token"] == "kg-user-token"
    assert out["status"] == "ready"
    assert "{{image:img_abc123def456}}" in out["note"]
    assert out["summary"] == {"最高项": "外观划伤"}
    img = ctx["images"]["img_abc123def456"]
    assert img["status"] == "ready"
    assert img["caption"] == "示例帕累托图"
    assert img["chart"]["template_id"] == "pareto"
    assert img["chart"]["name"] == "帕累托图"
    # 已存进用户「我的图表」→ 编辑器直达已保存图表
    assert img["chart"]["editor_path"] == "/chart-editor/77"
    # 追问改图与 Excel 导出所需的元数据
    assert img["chart"]["params"] == {"categories": ["a"], "values": [1]}
    assert img["chart"]["export_formats"] == ["xlsx", "png"]
    assert img["chart"]["chart_id"] == 77
    event = ctx["events"][0]
    assert event["event"] == "image_ready"
    assert event["data"]["url"].endswith("/img_abc123def456.png")
    assert event["data"]["chart"]["template_id"] == "pareto"


@pytest.mark.asyncio
async def test_generate_chart_editor_fallback_when_save_fails(monkeypatch):
    import app.services.tools.chart_generate as cg

    async def fake_generate(template_id, params, *, kgqa_token, title="", engine="matplotlib"):
        return {"image_id": "img_0123456789ab", "summary": {}, "chart_id": None}

    async def fake_brief(base_url, template_id):
        return None

    monkeypatch.setattr(cg, "generate_chart_image", fake_generate)
    monkeypatch.setattr(cg, "fetch_template_brief", fake_brief)
    ctx: dict = {"kgqa_token": "kg-user-token"}
    out = await cg.GenerateChartTool().run(
        {"template_id": "pareto", "params": {"x": 1}, "caption": "c"}, ctx
    )
    assert out["status"] == "ready"
    assert ctx["images"]["img_0123456789ab"]["chart"]["editor_path"] == "/mode-select/pareto"


def test_editor_path_by_engine():
    from app.services.tools.chart_generate import _editor_path

    # 已保存:drawio 直达 drawio 编辑器,其余直达 chart-editor
    assert _editor_path("drawio", "gantt", 9) == "/drawio-editor/9"
    assert _editor_path("vue-component", "man-machine", 8) == "/chart-editor/8"
    assert _editor_path("matplotlib", "pareto", 7) == "/chart-editor/7"
    # 未保存:drawio 落数据表单页,其余落模式选择页
    assert _editor_path("drawio", "gantt", None) == "/form/gantt"
    assert _editor_path("matplotlib", "pareto", None) == "/mode-select/pareto"


@pytest.mark.asyncio
async def test_generate_chart_run_failure_degrades(monkeypatch):
    import app.services.tools.chart_generate as cg

    async def fake_generate(template_id, params, *, kgqa_token, title="", engine="matplotlib"):
        raise RuntimeError("绘图平台账号同步失败 HTTP 401")

    async def fake_brief(base_url, template_id):
        return None

    monkeypatch.setattr(cg, "generate_chart_image", fake_generate)
    monkeypatch.setattr(cg, "fetch_template_brief", fake_brief)
    ctx: dict = {"kgqa_token": "kg-user-token"}
    out = await cg.GenerateChartTool().run(
        {"template_id": "pareto", "params": {"x": 1}, "caption": "c"}, ctx
    )
    assert "error" in out
    assert "image" not in ctx.get("images", {})
    # 无用户登录态 → 直接报错不外呼
    out2 = await cg.GenerateChartTool().run(
        {"template_id": "pareto", "params": {"x": 1}, "caption": "c"}, {"kgqa_token": ""}
    )
    assert "error" in out2
    # 参数缺失也直接报错不外呼
    out3 = await cg.GenerateChartTool().run({"template_id": "", "params": {}}, ctx)
    assert "error" in out3


# ---- 绘图模板确定性预匹配与提示 ----


@pytest.mark.asyncio
async def test_match_templates_for_question(monkeypatch):
    import app.services.tools.chart_templates as ct

    async def fake_fetch(base_url, timeout):
        return [
            {"id": "yamazumi", "name": "山积图", "engine": "matplotlib",
             "category": "精益", "description": "", "tags": []},
            {"id": "pareto", "name": "帕累托图", "engine": "matplotlib",
             "category": "质量", "description": "", "tags": []},
            {"id": "gantt", "name": "甘特图", "engine": "drawio",
             "category": "计划", "description": "", "tags": []},
        ]

    monkeypatch.setattr(ct, "_fetch_templates", fake_fetch)
    hits = await ct.match_templates_for_question("山积图怎么画?能结合实例演示吗")
    assert [t["id"] for t in hits] == ["yamazumi"]
    # 词干匹配:问题里只出现"帕累托"也应命中"帕累托图"
    hits2 = await ct.match_templates_for_question("怎么做帕累托分析")
    assert [t["id"] for t in hits2] == ["pareto"]
    assert await ct.match_templates_for_question("什么是精益生产") == []
    # tags 命中:「动作研究」打到动素分析图;过短拉丁标签 IE 不误伤
    async def fake_fetch_tags(base_url, timeout):
        return [
            {"id": "therblig", "name": "动素分析图", "engine": "vue-component",
             "category": "方法研究", "description": "", "tags": ["动素分析", "动作研究", "IE"]},
            {"id": "pareto", "name": "帕累托图", "engine": "matplotlib",
             "category": "质量", "description": "", "tags": ["IE"]},
        ]

    monkeypatch.setattr(ct, "_fetch_templates", fake_fetch_tags)
    hits3 = await ct.match_templates_for_question("动作研究怎么做")
    assert [t["id"] for t in hits3] == ["therblig"]
    assert await ct.match_templates_for_question("IE 是什么") == []


@pytest.mark.asyncio
async def test_build_chart_hint_includes_schema(monkeypatch):
    import app.services.tools.chart_templates as ct

    async def fake_detail(base_url, template_id, timeout=5.0):
        # 有服务端生成器的模板才有 paramSchema(与引擎无关)
        if template_id in ("yamazumi", "man-machine"):
            return {"config": {"paramSchema": {"title": {"type": "string"}}}}
        return {"config": {}}

    monkeypatch.setattr(ct, "fetch_template_detail", fake_detail)
    matched = [
        {"id": "yamazumi", "name": "山积图", "engine": "matplotlib",
         "category": "精益", "description": "", "tags": []},
    ]
    hint = await ct.build_chart_hint(matched)
    assert "yamazumi" in hint
    assert "paramSchema" not in hint  # 注入的是 schema 内容而非字段名包装
    assert "title" in hint
    assert "generate_chart" in hint
    # vue-component 模板:只要有 paramSchema 同样可服务端出图
    matched_vue = [
        {"id": "man-machine", "name": "人机作业分析图", "engine": "vue-component",
         "category": "方法研究", "description": "", "tags": []},
    ]
    hint_vue = await ct.build_chart_hint(matched_vue)
    assert "generate_chart" in hint_vue
    # 无 paramSchema 的模板:只提示亲手绘制
    matched2 = [
        {"id": "tech-roadmap-x", "name": "某未接入图", "engine": "drawio",
         "category": "计划", "description": "", "tags": []},
    ]
    hint2 = await ct.build_chart_hint(matched2)
    assert "generate_chart" not in hint2
    assert "某未接入图" in hint2


@pytest.mark.asyncio
async def test_build_session_chart_hint(monkeypatch):
    import app.services.tools.chart_templates as ct

    async def fake_detail(base_url, template_id, timeout=5.0):
        return {"config": {"paramSchema": {"title": {"type": "string"}}}}

    monkeypatch.setattr(ct, "fetch_template_detail", fake_detail)
    charts = [
        {"image_id": "img_x", "template_id": "gantt", "name": "甘特图",
         "params": {"title": "项目计划", "phases": [{"name": "阶段1"}]}},
    ]
    hint = await ct.build_session_chart_hint(charts)
    assert "gantt" in hint and "甘特图" in hint
    assert "项目计划" in hint  # 旧参数注入
    assert "generate_chart" in hint
    assert "无法修改" in hint  # 明确禁止说"无法修改"


def test_render_catalog_for_prompt_is_short():
    from app.services.tools.chart_templates import render_catalog_for_prompt

    long_desc = "将操作分解为基本动素" + "甲" * 40
    text = render_catalog_for_prompt([
        {"id": "therblig", "name": "动素分析图", "category": "方法研究",
         "description": long_desc},
        {"id": "pareto", "name": "帕累托图", "category": "质量管理",
         "description": "80/20"},
        {"id": "", "name": "空", "description": "应跳过"},
    ])
    assert "therblig" in text and "动素分析图" in text
    assert "pareto" in text
    assert "paramSchema" not in text
    assert "空" not in text
    assert long_desc not in text
    assert len(text) < len(long_desc) + 80


@pytest.mark.asyncio
async def test_build_catalog_hint_empty_on_fetch_fail(monkeypatch):
    import app.services.tools.chart_templates as ct

    async def boom(base_url, timeout):
        raise RuntimeError("down")

    monkeypatch.setattr(ct, "_fetch_templates", boom)
    assert await ct.build_catalog_hint() == ""

    async def fake_fetch(base_url, timeout):
        return [
            {"id": "therblig", "name": "动素分析图", "category": "方法研究",
             "description": "将操作分解为基本动素"},
        ]

    monkeypatch.setattr(ct, "_fetch_templates", fake_fetch)
    hint = await ct.build_catalog_hint()
    assert "therblig" in hint
    assert "generate_chart" in hint
    assert "禁止改用 generate_image" in hint


@pytest.mark.asyncio
async def test_generate_chart_missing_params_returns_schema(monkeypatch):
    import app.services.tools.chart_generate as cg

    async def fake_schema(template_id, *, timeout=5.0):
        return "- steps (动素步骤,必填):数组"

    monkeypatch.setattr(cg, "schema_text_for_template", fake_schema)
    ctx: dict = {"kgqa_token": "kg-user-token"}
    out = await cg.GenerateChartTool().run(
        {"template_id": "therblig", "params": {}, "caption": "c"}, ctx
    )
    assert "error" in out
    assert "steps" in out["error"]
    assert "generate_image" in out["error"]
    assert "images" not in ctx or "therblig" not in ctx.get("images", {})


def test_chart_data_for_engine_shapes():
    from app.services.tools.chart_generate import _chart_data_for_engine

    params = {"rows": [1, 2]}
    mpl = _chart_data_for_engine("matplotlib", params, "data:image/png;base64,xx")
    assert mpl == {"params": params, "image_base64": "data:image/png;base64,xx"}
    vue = _chart_data_for_engine("vue-component", params, "data:image/png;base64,xx")
    assert vue == params and vue is not params  # 组件数据本体,且不共享引用
    # drawio 同样存原始数据:drawio-editor 打开时用构建器现场生成 xml
    drawio = _chart_data_for_engine("drawio", params, "x")
    assert drawio == params and drawio is not params


@pytest.mark.asyncio
async def test_imagegen_returns_pending_and_background_failure(monkeypatch):
    config_store.set_value_sync("imagegen.base_url", "https://img.example.com")
    config_store.set_value_sync("imagegen.api_key", "sk-img", encrypted=True)
    config_store.set_value_sync("imagegen.model", "m1")

    import app.services.tools.imagegen as ig

    async def fake_generate(image_id, prompt):
        raise RuntimeError("boom")

    monkeypatch.setattr(ig, "generate_image_file", fake_generate)
    queue: asyncio.Queue = asyncio.Queue()
    ctx = {"image_queue": queue}
    out = await GenerateImageTool().run({"prompt": "a flow chart"}, ctx)
    assert out["status"] == "pending"
    image_id = out["image_id"]
    assert f"{{{{image:{image_id}}}}}" in out["note"]
    assert ctx["images"][image_id]["status"] == "pending"
    assert ctx["events"][0]["event"] == "image_pending"
    await asyncio.wait_for(ctx["image_tasks"][image_id], timeout=5)
    event = queue.get_nowait()
    assert event["event"] == "image_failed"
    assert event["data"]["image_id"] == image_id
    assert ctx["images"][image_id]["status"] == "failed"


# ---- 嵌套 schema 翻译 / 纠参 / 分流 ----


_ROADMAP_SCHEMA = {
    "title": {"type": "string", "label": "标题", "required": True},
    "stages": {
        "type": "object[]",
        "label": "阶段",
        "required": True,
        "items": {
            "name": {"type": "string", "label": "阶段名", "required": True},
            "color": {"type": "string", "label": "颜色"},
            "nodes": {
                "type": "object[]",
                "label": "节点",
                "required": True,
                "items": {
                    "id": {"type": "string", "required": True},
                    "label": {"type": "string", "required": True},
                    "detail": {"type": "string"},
                },
            },
            "connections": {
                "type": "object[]",
                "label": "连线",
                "items": {
                    "from": {"type": "string", "required": True},
                    "to": {"type": "string", "required": True},
                },
            },
        },
    },
}


def test_translate_dsl_field_expands_object_array():
    from app.services.tools.chart_templates import _translate_dsl_field

    line = _translate_dsl_field("stages", _ROADMAP_SCHEMA["stages"])
    assert "对象数组" in line
    assert "禁止传字符串数组" in line
    assert "name" in line and "nodes" in line


def test_compact_example_data_keeps_one_stage_two_nodes():
    from app.services.tools.chart_templates import compact_example_data

    example = {
        "title": "路线图",
        "stages": [
            {
                "name": "阶段1",
                "nodes": [
                    {"id": "a", "label": "A", "detail": "d1"},
                    {"id": "b", "label": "B", "detail": "d2"},
                    {"id": "c", "label": "C", "detail": "d3"},
                ],
            },
            {"name": "阶段2", "nodes": [{"id": "d", "label": "D"}]},
        ],
    }
    compact = compact_example_data(example, _ROADMAP_SCHEMA)
    assert len(compact["stages"]) == 1
    assert compact["stages"][0]["name"] == "阶段1"
    assert len(compact["stages"][0]["nodes"]) == 2


def test_coerce_chart_params_wraps_string_stages():
    from app.services.tools.chart_templates import coerce_chart_params

    raw = {"title": "学习路径", "stages": ["学科内核", "演进动力", "前沿方向"]}
    out, changed = coerce_chart_params(raw, _ROADMAP_SCHEMA)
    assert changed is True
    assert all(isinstance(s, dict) for s in out["stages"])
    assert [s["name"] for s in out["stages"]] == ["学科内核", "演进动力", "前沿方向"]
    assert out["stages"][0]["nodes"][0]["label"] == "学科内核"

    already = {
        "title": "ok",
        "stages": [{"name": "A", "nodes": [{"id": "n1", "label": "A"}]}],
    }
    same, changed2 = coerce_chart_params(already, _ROADMAP_SCHEMA)
    assert changed2 is False
    assert same["stages"][0]["name"] == "A"


def test_build_tools_exclude_generate_image():
    names = {t.name for t in build_tools(exclude={"generate_image"})}
    assert "generate_image" not in names
    assert "generate_chart" in names
    assert "search_kb" in names


def test_claims_image_present():
    from app.api.v1.query import _claims_image_present

    assert _claims_image_present("如上图所示，路线分三阶段")
    assert _claims_image_present("已生成技术路线图")
    assert not _claims_image_present("文字说明即可，不必配图")


def test_item_field_map_reads_singular_item():
    from app.services.tools.chart_templates import _item_field_map

    spec = {
        "type": "array",
        "item": {
            "elementWork": {"type": "string"},
            "leftTherblig": {"type": "string"},
        },
    }
    mapping = _item_field_map(spec)
    assert "elementWork" in mapping and "leftTherblig" in mapping


@pytest.mark.asyncio
async def test_example_params_for_template(monkeypatch):
    import app.services.tools.chart_templates as ct

    async def fake_detail(base_url, template_id, timeout=5.0):
        return {
            "config": {
                "paramSchema": {
                    "categories": {"type": "string-list"},
                    "values": {"type": "number-list"},
                },
                "example": {"data": {"categories": ["A", "B"], "values": [9, 1]}},
            }
        }

    monkeypatch.setattr(ct, "fetch_template_detail", fake_detail)
    params = await ct.example_params_for_template("pareto")
    assert params == {"categories": ["A", "B"], "values": [9, 1]}


@pytest.mark.asyncio
async def test_generate_chart_coerces_string_array_on_shape_400(monkeypatch):
    import app.services.tools.chart_generate as cg

    calls: list[dict] = []

    async def fake_generate(template_id, params, *, kgqa_token, title="", engine="matplotlib"):
        calls.append(params)
        if calls and all(isinstance(x, str) for x in (params.get("stages") or [])):
            raise RuntimeError("智绘图出图 HTTP 400: 第 1 个阶段必须是对象")
        return {"image_id": "img_cafebabeface", "summary": {}, "chart_id": 3}

    async def fake_brief(base_url, template_id):
        return {"id": template_id, "name": "技术路线图", "engine": "drawio", "export_formats": ["png"]}

    async def fake_schema(template_id, *, timeout=5.0):
        return _ROADMAP_SCHEMA

    monkeypatch.setattr(cg, "generate_chart_image", fake_generate)
    monkeypatch.setattr(cg, "fetch_template_brief", fake_brief)
    monkeypatch.setattr(cg, "param_schema_for_template", fake_schema)

    ctx: dict = {"kgqa_token": "kg-user-token"}
    out = await cg.GenerateChartTool().run(
        {
            "template_id": "tech-roadmap",
            "params": {"title": "学习路径", "stages": ["学科内核", "演进动力"]},
            "caption": "路线图",
        },
        ctx,
    )
    assert out["status"] == "ready"
    assert len(calls) == 2
    assert all(isinstance(s, dict) for s in calls[1]["stages"])
    assert calls[1]["stages"][0]["name"] == "学科内核"
    assert "{{image:img_cafebabeface}}" in out["note"]


def test_forced_tool_choice_only_when_tool_present():
    from app.services.query.agent import _forced_tool_choice
    from app.services.tools.base import Tool

    class Dummy(Tool):
        name = "generate_chart"
        timeout = 1.0

        def spec(self):
            return {"type": "function", "function": {"name": self.name}}

        def available(self):
            return True

        async def run(self, args, ctx):
            return {}

    assert _forced_tool_choice({}, "generate_chart") == "auto"
    assert _forced_tool_choice({"search_kb": Dummy()}, "generate_chart") == "auto"
    choice = _forced_tool_choice({"generate_chart": Dummy()}, "generate_chart")
    assert choice == {"type": "function", "function": {"name": "generate_chart"}}


@pytest.mark.asyncio
async def test_required_first_holds_text_until_chart_returns(monkeypatch):
    from app.services.llm.base import ToolCall
    from app.services.query.agent import run_agent_stream
    from app.services.query.evidence_pool import EvidencePool
    from app.services.tools.base import Tool

    class ChartTool(Tool):
        name = "generate_chart"
        timeout = 5.0

        def spec(self):
            return {
                "type": "function",
                "function": {"name": self.name, "parameters": {"type": "object"}},
            }

        def available(self):
            return True

        async def run(self, args, ctx):
            ctx.setdefault("images", {})["img_deadbeef0123"] = {
                "image_id": "img_deadbeef0123",
                "status": "ready",
            }
            return {"image_id": "img_deadbeef0123", "status": "ready", "note": "ok"}

    choices: list = []
    long_preamble = "甘特图画法讲解" * 40

    class FakeProvider:
        def __init__(self):
            self.round = 0

        async def chat_tools(self, messages, *, tools, tool_choice="auto", stats=None):
            choices.append(tool_choice)
            self.round += 1
            if self.round == 1:
                yield {"type": "token", "text": long_preamble}
                yield {
                    "type": "tool_call_begin",
                    "name": "generate_chart",
                    "id": "call_1",
                }
                yield {
                    "type": "tool_calls",
                    "calls": [
                        ToolCall(
                            id="call_1",
                            name="generate_chart",
                            arguments='{"template_id":"gantt","params":{},"caption":"甘特图"}',
                        )
                    ],
                }
                yield {"type": "finish", "finish_reason": "tool_calls"}
                return
            yield {"type": "token", "text": "下图是甘特图\n\n{{image:img_deadbeef0123}}\n\n步骤如下。"}
            yield {"type": "finish", "finish_reason": "stop"}

    import app.services.query.agent as agent_mod

    monkeypatch.setattr(agent_mod, "get_llm_provider", lambda: FakeProvider())

    events = [
        e
        async for e in run_agent_stream(
            question="画一张甘特图",
            history=[{"role": "assistant", "content": "上图是技术路线图 {{image:img_old}}"}],
            summary=None,
            evidence=EvidencePool(),
            tools=[ChartTool()],
            ctx={"images": {}},
            required_first="generate_chart",
        )
    ]
    types = [e["type"] for e in events]
    assert types[0] == "tool_start"
    assert events[0]["tool"] == "generate_chart"
    assert "tool_result" in types
    token_texts = [e["text"] for e in events if e["type"] == "token"]
    assert any("{{image:img_deadbeef0123}}" in t for t in token_texts)
    assert not any(long_preamble in t for t in token_texts)
    assert choices[0] == {"type": "function", "function": {"name": "generate_chart"}}
    assert choices[1] == "auto"
