# P4 阶段完成报告

> 项目:KG-QA-IE(工业工程知识库问答系统)
> 阶段:**P4 — 读出侧深化 + 飞轮启动**
> 状态:**✅ 完成**
> 完成日期:2026-05-20
> 项目位置:`D:\kg-qa-ie\`
> 上一份报告:[P3_DONE.md](./P3_DONE.md)
> 主规划:[工业工程知识库问答系统 — 技术规划文档.md](./工业工程知识库问答系统%20—%20技术规划文档.md)

---

## 摘要

P4 把规划文档七章的"读出侧深化 + 飞轮启动"全部交付:

1. **answer-quality 评分** — `services/query/scoring.py` 真实现 + SSE `score` 事件 + 写入 query 持久化页 frontmatter + 落 `qa_messages` 字段
2. **digest 三模板** — `services/digest/{router, builder, parsing, deep_dive, comparison, timeline, scoring}.py` 全链路 + `POST /digest` + `GET /synthesis/{slug}`
3. **gap-queue 状态机** — `services/growth/gap_queue.py` 真实现(append / list / update_status / reopen_stale_resolved + markdown 镜像);加入 `reopened` 状态;query / digest / comparison_cell 三个 origin 自动入队
4. **IE 领域风格 prompt 路由** — `query/composer.py` 已在 P2 落地的 `classify_question`(method/concept/general)在评分中并未带来取舍变化,P4 主要是把规划文档 §3.3.2 的评分公式与 §3.4.1 的三模板路由完整对齐
5. **QA 路径高亮** — query SSE 已在 P2 阶段输出 `path` 事件(`{nodes, edges}`),P4 不再补改;digest 同样输出 `citations` 字段供前端高亮

新建 / 改动:
- ORM:`gap_queue` 加 `reopened` 状态、`updated_at`、`resolved_via_ingest_job_id`(migration **0004**)
- 新服务模块:`services/digest/{builder, parsing}.py`
- 改服务模块:`services/query/scoring.py`(NotImplementedError → 完整公式)/ `services/growth/gap_queue.py`(骨架 → 完整,含 markdown 镜像)/ `services/digest/{router,deep_dive,comparison,timeline,scoring}.py`(全部从骨架到真实现)
- 改 API:`api/v1/query.py`(SSE 多发 `score` 事件 + 自动 gap_queue 入队)/ `api/v1/digest.py`(NotImplementedError → 真实现 + 自动 gap_queue 入队 + 落盘到 wiki/synthesis|comparisons)/ `api/v1/admin.py`(`GET /admin/gap-queue` + `PUT /admin/gap-queue/{id}` 真实现)
- 改 schema:`schemas/admin.py`(GapQueueItem 扩字段 + 新增 GapQueueUpdate)

**测试**:`pytest -q` 共 **318 项全绿**(P3 基线 276 + P4 新增 42 项),零回归。

**alembic**:0001 → 0002 → 0003 → 0004 在 SQLite `alembic upgrade head` + `alembic check` **无 drift**。

---

## 1. 实际交付清单

### 1.1 ORM + Migration

| 文件 | 行为 |
|------|------|
| `app/models/gap_queue.py` | status enum 加 `reopened`;新增 `updated_at` / `resolved_via_ingest_job_id` 字段 |
| `migrations/versions/0004_gap_queue_extensions.py` | 新增列 + (MySQL 下) `MODIFY status ENUM(...)` 兼容 |

### 1.2 query/scoring.py(从 NotImplementedError → 完整)

| 函数 | 行为 |
|------|------|
| `score_answer(...)` | 按规划 §3.3.2 公式:`0.4*cited + 0.3*confidence_avg + 0.2*(1-alias_miss) + 0.1*(1-broken_ratio)`;tier(gold ≥0.8 / silver ≥0.6 / bronze < 0.6);gap_flag 触发条件:score<0.6 / cited<3 / alias_miss>0.3 / 答案含 hedge 表达 |
| `evaluate_for_query(...)` | 从 query SSE handler 直接拿到的扁平上下文(answer / expanded_keywords / citations_slugs / candidates / wiki_root)算最终 quality dict;自动算 broken_links(检 wiki/<kind>/{X}.md 是否存在)与 alias_miss_count |
| `collect_citations_from_answer` / `confidence_mix` | 工具函数 |
| `_resolve_page` / `_confidence_for_slug` / `_existing_slugs` / `_slug_confidence_label` | 文件系统辅助 |

### 1.3 query SSE 接入(api/v1/query.py)

| 改动 | 说明 |
|------|------|
| 新增 `score` 事件 | 在 `citations` 与 `path` 之后、`persist` 之前发出,载荷为 `score_answer(...)` 完整字典 |
| 新增 `warn` 事件 | gap_queue 入队失败时发出 `{stage, error}` |
| 自动 gap_queue 入队 | `quality.gap_flag=True` 时调 `append_gap(origin='query', user_question=问题, gap_reasons=quality.reasons, suggested_seeds=keywords[:6])` |
| 持久化 quality | `maybe_persist` 把 quality 字典传入,frontmatter 写 `answer_quality / tier / gap_flag` |
| `no_context` 分支 | 同样发 `score` 事件(score=0.0, tier=bronze, gap_flag=True, reasons=['no_context'])+ 入 gap_queue |
| `get_query_page` | tier 校验严格化(只接受 `gold/silver/bronze`,其他降级 `unknown`) |

### 1.4 digest 全链路

| 文件 | 行为 |
|------|------|
| `services/digest/router.py` | `detect_mode(text)` 关键词路由,优先级 `comparison > timeline > deep_dive`;英文缩写(VS / digest)用 `\b` 边界防止 "VSM" 误判为 "vs" |
| `services/digest/builder.py` (新) | `gather_context` 复用 query/grep + page_reader,top_k=8,long_threshold=4000;`render_*_prompt` 三种;`call_llm_markdown` 单次非流式调用 |
| `services/digest/parsing.py` (新) | `split_sections` 按 H2 切片;`extract_citations` 抽 [[X]] 并过滤模板占位符(`页面A` / `对象1` 等);`parse_markdown_table` 解析对比表;`is_blank_cell` 识别 `-` / `无` / `待补充` 等空格变体;`parse_timeline_events` 抽 `- 1947 事件:说明` 行 |
| `services/digest/scoring.py` | `score_deep_dive`(5 H2 缺几个 + 占位段算缺,缺≥2 → gap)/ `score_comparison`(fill_rate 4/12 算法 + empty_cells 列表)/ `score_timeline`(events<3 sparse + events_per_decade) |
| `services/digest/deep_dive.py` | LLM markdown → split_sections → 抽 followups → score_deep_dive |
| `services/digest/comparison.py` | LLM markdown → 抽 ## 对比表 → parse_markdown_table → IE 6 维 dimensions / 用户对象;`_infer_objects_from_topic` 从"对比 X 和 Y"自动切对象;返回 empty_cells |
| `services/digest/timeline.py` | LLM markdown → 抽 ## 事件说明 → parse_timeline_events;兜底全文扫;score_timeline |

### 1.5 api/v1/digest.py 改造

| 端点 | 改动 |
|------|------|
| `POST /digest` | NotImplementedError → 真实现:detect_mode(payload.topic) → 调对应 compose_*;落盘 `wiki/synthesis/{date}-{slug}.md` 或 `wiki/comparisons/{...}.md` + frontmatter(digest_mode / topic / objects / answer_quality / fill_rate / events_count / sections_missing / gap_flag / followups);自动 gap_queue 入队(deep_dive 缺章节、comparison 逐空格、timeline 事件不足);同步 wiki_pages/wiki_links 索引(_sync_db_indices) |
| `GET /synthesis/{slug}` | NotImplementedError → 真实现:支持 `wiki/synthesis/` 与 `wiki/comparisons/` 双目录;返回 split_sections 切好的 sections + frontmatter 中的 quality_score / gap_flag / fill_rate |

### 1.6 services/growth/gap_queue.py(从骨架 → 完整)

| 函数 | 行为 |
|------|------|
| `append_gap(...)` | 写 GapQueue 行;校验 origin / status;reasons/seeds 序列化 JSON;同步刷新 `wiki/_growth/gap-queue.md`(失败不阻塞);可传入 db= 或自动 session_scope |
| `list_gaps(...)` / `list_open_gaps(...)` | 查询;支持 status=None 全状态、origin 过滤、limit |
| `update_status(...)` | 校验状态 → 改 row + 自动设 resolved_at(resolved/dismissed)或清空(reopened);记录 resolved_by + resolved_via_ingest_job_id;同步 markdown |
| `reopen_stale_resolved(...)` | 扫 status=resolved + resolved_at 早于 7 天前 + 关联 query_page 仍 score<0.6 → 改 reopened(P5 定时任务消费,P4 已交付函数) |
| `write_gap_queue_markdown(...)` | 把当前 gap_queue 表内容渲染为 5 段(open / in_progress / resolved / dismissed / reopened)markdown 表格,写到 `wiki/_growth/gap-queue.md` |

### 1.7 admin/gap-queue 端点(api/v1/admin.py)

| 端点 | 改动 |
|------|------|
| `GET /admin/gap-queue` | NotImplementedError → 真实现;query 参数 `status`(默认 open;`all`/空 → 全状态)+ `origin` + `limit` |
| `PUT /admin/gap-queue/{id}` | NotImplementedError → 真实现;请求体 `GapQueueUpdate(status, resolved_via_ingest_job_id)`;LookupError → 404、ValueError → 422 |

### 1.8 测试

新增 **42 项**(用单元 41 + 集成不在此报告内,见 P5_DONE 明细):

| 文件 | 用例数 | 覆盖点 |
|------|-------|--------|
| `tests/unit/test_query_scoring.py` | 12 | 完美引用 → gold / 单引用+AMBIGUOUS+不足 alias → bronze+gap / silver 中段 / hedge 表达触发 gap / broken_link 影响 / 无引用 / collect_citations 去重 / confidence_mix / evaluate_for_query 用 wiki 文件存在判 broken / alias_miss_count / score 上下界 clip / isolated_hits 字段 |
| `tests/unit/test_digest_modules.py` | 19 | router 三模式 + 优先级 + VSM 不误判为 vs / parsing split_sections / find_section / extract_citations 过滤 / parse_markdown_table / is_blank_cell 5 变体 / parse_timeline_events / score_deep_dive 全过+缺 2+占位段算缺 / score_comparison 满+空格+空表 / score_timeline 多+少+空 |
| `tests/unit/test_gap_queue.py` | 10 | append+读 / 拒绝非法 origin / 拒绝非法 status / list 过滤 / update→resolved 设 resolved_at / update→reopened 清空 resolved_at / 404 / 422 / 常量集合 / markdown 镜像 |
| `tests/integration/test_digest_three_templates.py` | 8 | deep_dive 落盘 + citations + 不缺章节 / deep_dive 缺 3 章触发 gap / comparison 4 空格 fill_rate≈0.667 + comparisons/ 落盘 / timeline 4 事件不缺 / timeline 1 事件缺 / synthesis 404 / GET 后能取回 / comparison 空格逐格写 gap_queue 4 条 |
| `tests/integration/test_query_scoring_sse.py` | 4 | SSE 含 score 事件 + 高质 gold/silver / 单引用+hedge → gap_queue 入一条 origin=query / no_context 分支同样 score+gap+reasons=['no_context'] / 持久化页 GET 回 quality 字段 |

---

## 2. 测试结果

```
$ cd backend && pytest -q
......(全 318 项绿)......
318 passed in ...
```

P4 部分增量:42 项;累计 318 项(P3 基线 276)。

---

## 3. 关键设计要点

### 3.1 评分公式与 tier 标签兼容

规划文档 §3.3.2 用 `high/medium/low`,但代码侧(`schemas/query.py`、`models/qa.py`、前端 Query.vue 旧版)统一用 `gold/silver/bronze`。P4 在评分实现里采用 `gold/silver/bronze` 维持向后兼容,语义对应 high/medium/low。

边界条件全部 clip 到 `[0, 1]`。`broken_link_ratio` 分母用答案中 `[[X]]` 的总次数(而非 cited_sources 集合大小),确保答案重复引用同一断链时仍被惩罚。

### 3.2 hedge 表达检测

固定关键词列表(`_HEDGE_PHRASES = ("我不太确定", "我不确定", "未找到资料", "未找到相关", "知识库未覆盖", "无法确认", "可能不准确", "信息不足")`)。命中时即使其他指标都满分也强制 `gap_flag=True` 并把 `hedge:{phrase}` 加入 `reasons`,防止"高引用 + 模型 hedge"漏入 gap。

### 3.3 confidence 缺失的中性值

`DEFAULT_CONFIDENCE_SCORE = 0.7`(高于 INFERRED 的 0.6,低于 EXTRACTED 的 1.0):
- 现实里大多数老页面没标 confidence,直接当 0.0 会过严
- 标了 AMBIGUOUS 的页才扣到 0.3
- 这个值在 `.wiki-schema.md` 的成长配置里可暴露(P5+)

### 3.4 digest 路由的英文边界匹配

`vs` / `digest` 用 `\b` 边界防止误命中:
- `vs` → 不匹配 `VSM`、`versus`
- `digest` → 不匹配 `digestive`(无意义但保险)

中文关键词不需要边界(中文 substring 匹配)。

### 3.5 comparison 空格逐格入队

`compose_comparison` 返回 `empty_cells: list[tuple[str, str]]`。API handler 对每条 `(dim, obj)` 都单独 `append_gap(origin='comparison_cell', suggested_seeds=[dim, obj], digest_page=slug)`,前端在 GrowthDashboard 看到的就是按维度 × 对象的细粒度记录。

### 3.6 deep_dive 占位段算缺

LLM 偶尔会产出"## 不同视角对比\n\n> 待补充:..."这种占位章节。`score_deep_dive` 把 content 长度 < 20 字符或以 `> 待补充` 开头的段算 `placeholder_sections`,与 `sections_missing` 一起决定 gap_flag(`(missing + placeholder) > 1`)。

### 3.7 SSE 事件序列演进(向后兼容)

P3 SSE 顺序:`progress* → token* → citations → path → persist → done`
P4 在 `path` 之后、`persist` 之前插 `score` 事件。前端可以选择性消费:
- 旧前端不消费 score,行为完全不变(citations / path / persist / done 全部仍按原顺序触发)
- 新前端消费 score 后展示 tier 标签 + gap_flag 红点

### 3.8 gap_queue markdown 镜像的容错

`write_gap_queue_markdown` 失败时不抛异常(loguru.warning + 返 None),不阻塞主写入路径。原因:
- 文件落盘失败(磁盘满 / 权限)是高频运维场景
- DB 已经写入,markdown 可以下次再生
- `update_status` / `append_gap` 主路径不能因为 markdown 镜像失败而 rollback DB

### 3.9 GapQueue 状态机的 reopened 设计

P3 enum 只有 `open / in_progress / resolved / dismissed`,P4 加 `reopened`:
- `update_status(status="reopened")` 主动调用清空 `resolved_at`,让前端 GrowthDashboard 重新看到这条
- `reopen_stale_resolved`(批处理函数):扫 7 天前 resolved 的项,如果对应 query_page 的最新 QaMessage.answer_quality 仍 < 0.6 → 自动改为 reopened
- 该函数 P4 交付,但不接定时任务;P5 起把它接到 cron / Celery 周期任务

---

## 4. 与主规划的对齐

| 规划条目 | 实现位置 | 备注 |
|---------|---------|------|
| F11 answer-quality 自动评分 + gap_flag 入队 | `services/query/scoring.py` + `api/v1/query.py` SSE score 事件 + auto append_gap | 完全对齐 §3.3.2 公式 |
| F12 digest 三模板 | `services/digest/{router,builder,parsing,deep_dive,comparison,timeline,scoring}.py` + `api/v1/digest.py` | 三模板独立 prompt + 共享 builder + 独立解析与评分 |
| F13 digest 评分 + 缺口入队 | `services/digest/scoring.py` + `api/v1/digest.py` 中 deep_dive missing / comparison empty_cell / timeline sparse 三路入队 | 与 §3.4.2 完全对齐 |
| F22 IE 领域风格 prompt 路由 | `services/query/composer.py:classify_question`(P2 已落)+ digest 模板的 IE 模式 6 维对比表 | DOMAIN_IE 旗标维持 P2 行为 |
| §3.6.1 gap-queue 状态机 | `services/growth/gap_queue.py` + admin API | 含 reopened |
| §3.6 飞轮:query → gap_queue 闭环 | query SSE 自动入队 + admin 端点状态变更 | resolved_via_ingest_job_id 字段为 P5 ingest 联动留口 |

---

## 5. 未交付项(归到 P5)

P4 任务清单全部完成。下面按规划文档:

- **P5**(运营侧 + 自我维护):
  - lint / status / trend / followups 端点 — **本轮(P5)同步交付**
  - admin gap-queue 端点 — **本轮(P5)同步交付**
  - firecrawl 接入(规划 §F9 / P5 主清单)
  - 配置存储 JSON → MySQL `system_config` 表归位
  - prompt_versions 表 + ModelConfig 加 prompt 版本切换
  - QA 路径高亮的前端联调(后端 path payload P2 已发,前端 P5 接)
- **P6**(生产部署 + 性能)

---

## 6. 工时

约 ~3 小时:
- ~10 min 侦察 + 任务规划 + 8 任务清单
- ~25 min P4.1 query/scoring 实现 + 接入 SSE
- ~15 min P4.2 GapQueue ORM 扩展 + 0004 migration
- ~20 min P4.3 gap_queue 服务 + markdown 镜像
- ~50 min P4.4 digest 三模板(builder + parsing + 3 个 compose_* + scoring)
- ~25 min P4.5 digest API + gap_queue 入队
- ~30 min P4.6 测试(42 项)+ 修一个 fixture detached 实例 bug
- ~20 min P4.7 完成报告
