# P3 阶段完成报告

> 项目:KG-QA-IE(工业工程知识库问答系统)
> 阶段:**P3 — 写入侧扩展**
> 状态:**✅ 完成**
> 完成日期:2026-05-20
> 项目位置:`D:\kg-qa-ie\`
> 上一份报告:[P2_DONE.md](./P2_DONE.md)
> 主规划:[工业工程知识库问答系统 — 技术规划文档.md](./工业工程知识库问答系统%20—%20技术规划文档.md) v1.6

---

## 摘要

P3 把规划文档七章的"写入侧扩展"五块全部交付:

1. **delete 素材级联清理 + slug-redirect** — `services/wiki/delete.py` 真实现 + `slug_redirects` 表落地
2. **batch-ingest 分批暂停状态机** — `services/ingestion/batch_state.py` + 6 个新 API(start/get/continue/cancel/list)
3. **crystallize 用户文本结晶** — `services/crystallize/__init__.py` 真实现 + 候选合并入 `gap_queue(merge_suggestion)`
4. **节点版本历史 diff** — `services/wiki/version_history.py` 真实现 + `_persist_version_records` 接入 pipeline + 3 个新 API
5. **反链 API + 优化** — `GET /wiki/pages/{slug}/backlinks` 走 `wiki_links` 表(降级到全文扫描)

新建 / 改动:
- ORM 新表:**`slug_redirects`**(P3.1)+ wiki_node_versions 加 `actor_user_id` / `title_snapshot`(0002 + 0003 双 migration)
- 新服务模块:`services/wiki/slug_redirect.py` / `services/wiki/version_history.py` / `services/ingestion/batch_state.py`
- 改服务模块:`services/wiki/delete.py`(从骨架 → 完整级联) / `services/crystallize/__init__.py`(从骨架 → 完整) / `services/ingestion/pipeline.py`(`_materialize_page` 多返一个 version_record + post_process 写 wiki_node_versions)
- 改 API 模块:`api/v1/wiki.py`(GET 跟随 redirect → 410 / backlinks / versions list+detail / DELETE 用真级联) / `api/v1/ingestion.py`(/batch 状态机 + 4 个新端点) / `api/v1/crystallize.py`(从 NotImplementedError → 真实现)
- 改 schema:`schemas/wiki.py`(BacklinkItem / DeleteSourceResponse / NodeVersionList[Item] / NodeVersionDetail) / `schemas/ingestion.py`(BatchStateResponse / BatchSummary / BatchResultItem)

**测试**:`pytest -q` 共 **276 项全绿**(P2 基线 216 + P3 新增 60 项),零回归。

**alembic**:0001 → 0002 → 0003 在 MySQL 3309 上 `alembic upgrade head` + `alembic check` **无 drift**。SQLite 同样无 drift。

---

## 1. 实际交付清单

### 1.1 ORM + Migration

| 文件 | 行为 |
|------|------|
| `app/models/slug_redirect.py` | 新表 `slug_redirects(id, old_slug, new_slug, kind, reason, note, created_at)`,`(old_slug, kind)` 唯一 |
| `app/models/wiki_node_version.py` | 加 `actor_user_id`(int, nullable) + `title_snapshot`(varchar(255), nullable) |
| `migrations/versions/0002_slug_redirects.py` | create_table slug_redirects + 2 索引 |
| `migrations/versions/0003_node_version_actor.py` | batch_alter_table wiki_node_versions add_column 两个新字段(linter 自动生成) |
| `app/models/__init__.py` | 导出 SlugRedirect |
| `migrations/env.py` | 增加 `-x url=...` 命令行覆盖支持 + 把 SlugRedirect 加进 `target_metadata` import 列表 |

### 1.2 wiki/slug_redirect.py(新)

| 函数 | 行为 |
|------|------|
| `write_redirect(db, *, old_slug, new_slug, kind, reason, note)` | 写一条 redirect;同 `(old_slug, kind)` 已存在时覆盖 new_slug/reason/note |
| `lookup(db, *, old_slug, kind=None)` | 查一条;kind 为空时跨 kind 取最新 |
| `resolve_chain(db, *, slug, kind=None, max_hops=5)` | 跟随多跳重定向直到稳定 / 删除 / 超限;**带防环**(已访问 slug 再出现 → 终止链) |
| `list_redirects(db, *, kind=None, limit=100)` | 按 created_at desc 列出 |
| `remove_redirect(db, *, old_slug, kind)` | 删一条,**flush 后** lookup 立即返 None |
| `write_many(db, rows)` | 批量写 |

### 1.3 wiki/version_history.py(新)

| 函数 | 行为 |
|------|------|
| `compute_diff(*, old_fm, old_body, new_fm, new_body)` | frontmatter 三态 diff(added/removed/changed,**忽略 updated/created 时间戳字段**)+ body unified diff(8000 字符截断 + 行数统计) |
| `write_version(db, *, slug, version, diff, triggered_by, ...)` | 写一条 wiki_node_versions 行 |
| `list_versions(db, *, slug, limit=50)` | version desc 列出 |
| `get_version(db, *, slug, version)` | 取单条 |
| `diff_stat(diff_json_str)` | 从 JSON 字符串提取摘要(added_lines / fm_added 等);损坏 JSON 返 `{}` |

### 1.4 wiki/delete.py(从骨架 → 完整)

| 函数 | 行为 |
|------|------|
| `_strip_source_subsection(body, source_slug)` | 从正文删除 `### 来源:[[X]]` H3 子段;支持中英文冒号、`[[X]]` / `[[X\|alias]]`;吃到下一个 `### ` 或 `## ` 之前 |
| `_strip_empty_observations_section(body)` | 子段清完后,如果"## 不同素材中的观点" H2 段下没有任何 H3 → 删整 H2 段 |
| `delete_source(*, source_slug, wiki_root, db, cascade=True, delete_orphans=True)` | 主入口:删 source 文件 → 级联清理引用页 frontmatter.sources / 来源段 → 孤立实体/主题整页删 + 写 redirect(reason=source_deleted) → 缓存失效 → DB 同步 → 计算断链。返回 `DeleteResult` dict |

### 1.5 crystallize/__init__.py(从骨架 → 完整)

| 函数 | 行为 |
|------|------|
| `crystallize_text(*, text, target_topic, kind, wiki_root, db, user_id)` | 主入口:渲染 prompt → 调 LLM → 解析 frontmatter(LLM 没生成时补)→ 写盘 `wiki/synthesis/{date}-{title}.md` 或 `wiki/comparisons/` → 抽 `[[X]]` 引用 → 未建页的实体写 `gap_queue(origin=merge_suggestion, suggested_seeds=[X])` → 同步 wiki_pages/wiki_links + 写 wiki_node_versions(triggered_by=crystallize)。返回 `(slug, title, candidates_added)` |
| `_strip_markdown_fence(raw)` | 剥 LLM 返回的 ```markdown ... ``` 围栏 |
| `_make_synthesis_slug(title)` | `{date}-{slug(title)}` |
| `_candidate_entity_slugs(body)` | 抽 body 中所有 `[[X]]` 目标,去重保序 |

### 1.6 ingestion/batch_state.py(新)

| 函数 | 行为 |
|------|------|
| `start_batch(*, document_ids, pause_every)` | 校验 + 分配 batch_id(`batch-YYYYMMDD-HHMMSS-mmm-N`)+ 写 `{cache_dir}/batches/{batch_id}.json` |
| `get_state(batch_id) -> dict \| None` | 读状态;损坏 JSON → None |
| `run_chunk(batch_id)` | 跑下一段:从 `next_index` 开始,直到 `pause_every` 个或全跑完;每个 doc 单独 try/except,失败 → results 写一条 failed 行,流水继续 |
| `continue_batch(batch_id)` | 显式继续(等价 run_chunk) |
| `cancel_batch(batch_id)` | 状态置 cancelled;completed 不变 |
| `list_batches(limit=50)` | 按 mtime desc 列出摘要(含 succeeded/skipped/failed 计数) |

### 1.7 ingestion/pipeline.py 改造

| 改动 | 说明 |
|------|------|
| `_materialize_page(...)` 返回 `(rel, slug, action, version_record)` | 新增第 4 个返回值,带本次写入的 frontmatter+body diff;`triggered_by="merge"`(余弦/别名合并)/`"ingest"`(新建/字面合并/topic 等) |
| `_materialize_all(..., version_records)` | 收集所有 version_record |
| `_persist_version_records(db, records, *, document_id, triggered_default)` | 在 post_process 阶段把 records 写入 wiki_node_versions;**幂等**(同 `(slug, version)` 跳过)+ **过滤空 diff**(避免无意义版本号膨胀) |
| `run_pipeline` 返回 jobs[i] payload 含 `versions_written` | 便于前端展示 |
| `run_batch` 不再使用,被 `batch_state.run_chunk` 取代 | 旧 API 路由仍兼容 |

### 1.8 api/v1/wiki.py 改造

| 端点 | 改动 |
|------|------|
| `GET /wiki/pages/{slug}` | 文件不存在时查 `slug_redirects`:链终点是 None → **410 Gone** + `{code, message, old_slug}`;链终点是另一个 slug + `follow_redirect=true` → 跟随并设 `X-KGQA-Redirected-From` 响应头 |
| `GET /wiki/pages/{slug}/backlinks` | 新端点,从 `wiki_links` 表查反链(按 occurrences desc),JOIN `wiki_pages` 拿 src_title/src_kind;**表为空时降级到 `find_backlinks` 全文扫描** |
| `GET /wiki/pages/{slug}/versions` | 新端点,列 wiki_node_versions(version desc),含 `diff_stat` 摘要 |
| `GET /wiki/pages/{slug}/versions/{version}` | 新端点,返单版本完整 diff |
| `PUT /wiki/pages/{slug}` | bump_version=True 时调用 `compute_diff` + `write_version(triggered_by='manual', actor_user_id=user_id)` |
| `DELETE /sources/{slug}` | 接 `services/wiki/delete.delete_source`,返 `DeleteSourceResponse(removed_pages, updated_pages, broken_links, cache_evicted, redirects_written)` |

### 1.9 api/v1/ingestion.py 改造

| 端点 | 改动 |
|------|------|
| `POST /ingestion/batch` | 校验 doc_ids 全存在 → start_batch → 同步跑第一段 → 返回 `BatchIngestResponse(batch_id, status, next_index, next_pause_at, results)` |
| `GET /ingestion/batch/{batch_id}` | 新端点,读状态 |
| `POST /ingestion/batch/{batch_id}/continue` | 新端点,继续跑下一段;404 当 batch_id 不存在 |
| `POST /ingestion/batch/{batch_id}/cancel` | 新端点,标 cancelled |
| `GET /ingestion/batches` | 新端点,列摘要(给 GrowthDashboard 用) |
| 上传 `multipart filename` 中文修复 | latin-1 → utf-8 还原(P3 顺手修) |

### 1.10 api/v1/crystallize.py 改造

| 端点 | 改动 |
|------|------|
| `POST /crystallize` | NotImplementedError → 真实现:接 `services/crystallize.crystallize_text`;text 太短 → 422(pydantic min_length);kind 非 synthesis/comparison → 400 |

### 1.11 测试

新增 **60 项**(43 单测 + 17 集成):

| 文件 | 用例数 | 覆盖点 |
|------|-------|--------|
| `tests/unit/test_slug_redirect.py` | 10 | write+lookup / 覆盖已存在 / resolve_chain 两跳 / chain 到 None(deleted) / 无 redirect 时返起点 / 防环 / 跨 kind / remove + flush / list filter kind / write_many |
| `tests/unit/test_version_history.py` | 8 | compute_diff frontmatter changes / 忽略时间戳 / body added+removed_lines / body 截断 / write+list / get_version / diff_stat / 损坏 JSON 返 {} |
| `tests/unit/test_wiki_delete.py` | 9 | _strip_source_subsection 三态(命中/全角冒号/无匹配)/ _strip_empty_observations_section 两态 / delete_source 简单 / 级联 observation block / 孤立实体整页删 + 写 redirect / DB 同步 / 不存在 → FileNotFoundError |
| `tests/unit/test_batch_state.py` | 14 | start 校验 / 写状态文件 / 拒绝空 doc_ids / 拒绝 pause_every=0 / get_state None / list_batches summary / cancel / cancel idempotent / cancel 不存在 → 404 / **run_chunk 暂停**(2/5)/ **continue 完成** / 失败 doc 不阻塞流 / pipeline crash 也走 completed / continue 不存在 → 404 / 总数<pause_every 直接 completed |
| `tests/integration/test_p3_writes.py` | 17 | DELETE 404 / DELETE 级联 + 实体页 sources 移除 + 来源段被清 / 孤立实体 → GET 410 + code=GONE / GET 跟随 X→Y redirect + X-KGQA-Redirected-From 响应头 / follow=false 时 404 / backlinks 走 DB(JOIN title+kind+occurrences) / 表空降级到全扫 / versions list+detail / versions 404 / **crystallize 创建 synthesis 页 + gap_queue 入队 + 落盘** / 短文本 422 / crystallize 写 wiki_node_versions(triggered_by=crystallize) / batch ingest 暂停后 continue × 2 → completed / batch 缺 doc 404 / continue 不存在 404 / cancel 改状态 |
| `tests/integration/test_pipeline.py`(扩) | +2 | ingest 端到端写 wiki_node_versions(triggered_by=ingest, document_id=N) / 跨 source 合并触发 merge 类版本 |

---

## 2. 测试结果

```
$ cd backend && pytest -q
........................................................................ [ 26%]
........................................................................ [ 52%]
........................................................................ [ 78%]
............................................................   [100%]
276 passed in 15.46s
```

| 分类 | 计数 |
|------|-----|
| P1 基线单测 | 26 |
| P2.1 wiki I/O 单测 | 32 |
| P2 主体单测 | 133 |
| P2 集成 | 25 |
| **P3 单测(新)** | **41** |
| **P3 集成(新)** | **19** |
| **合计** | **276** |

---

## 3. 关键设计要点

### 3.1 slug_redirect 的链跟随与防环

`resolve_chain` 维护 `visited` set,遇到已访问 slug 立刻终止;`max_hops=5` 默认上限。三种返回:
- `(final_slug, hops_path)`:正常终点
- `(None, [])`:链终点是删除(`new_slug=None`)→ API 层映射 410 Gone
- `(slug, [])`:无 redirect → API 层映射 404 Not Found

`API GET /wiki/pages/{slug}` 的 dispatch:
1. 文件存在 → 200 + 内容
2. 文件不存在 → resolve_chain
   - final = None → **410 Gone**
   - final ≠ slug + follow_redirect=true + final 文件存在 → 200 + `X-KGQA-Redirected-From: {old_slug}` 头
   - 其他 → 404

### 3.2 delete 级联的边界

| 场景 | 行为 |
|------|------|
| 实体页 frontmatter.sources 中含被删 source + body 有 `### 来源:[[X]]` 段 | 移除 sources 项 + 清子段;page **保留**(如有其他 sources)|
| 实体页 sources 仅含被删 source(orphan)| **整页删除** + 写 `slug_redirects(old=该 slug, new=None, reason=source_deleted)` → 后续 GET 该 slug 返 410 |
| `## 不同素材中的观点` 段所有子段都被清 | 整 H2 段一并删 |
| source 自身 | 写 `slug_redirects(old=source_slug, new=None, reason=source_deleted)` |
| wiki_links 行 | 删 src=source 的出边 + dst=被删 slug 的入边 |

测试 `test_delete_source_orphan_entity_returns_410_via_redirect` 端到端串完整链路。

### 3.3 节点版本 diff 的幂等与去噪

`_persist_version_records` 两层过滤:
1. **同 (slug, version) 已存在 → 跳过**(cache miss 重跑、`force=True` 等场景下不重复写)
2. **空 diff 跳过**(body 0 行变更 + frontmatter 无 added/removed/changed)→ 避免每次幂等 ingest 都灌进一条无意义记录

`compute_diff` **忽略 updated/created 时间戳字段**(每次 ingest 都会刷,进 diff 是噪声)。

### 3.4 batch state 的崩溃恢复

`{cache_dir}/batches/{batch_id}.json` 持久化。`run_chunk` 进入时:
- status=completed → 直接返回(idempotent)
- status=running 但有 results → 视为崩溃后恢复,继续从 next_index 跑

每个 doc 失败/crash → results 写一条 `final_status: failed` 但流不停。`pause_every` 触发后状态变 `paused`,等前端调 `/continue`。

测试 `test_run_chunk_handles_pipeline_crash` / `test_continue_batch_resumes_to_completion` 显式覆盖。

### 3.5 crystallize 的候选合并

LLM 输出的 markdown 里凡含 `[[X]]` 的目标都视为引用候选。落盘后:
- 候选 X 在 `wiki/{各 kind}/X.md` 都不存在 → 入 `gap_queue(origin=merge_suggestion, suggested_seeds=[X], digest_page=新建的 synthesis slug)`
- 命中已有页 → 不做处理(双链解析时自动连接)

返回值 `candidates_added` 是入队的 slug 列表,前端 Crystallize.vue 据此展示"我帮你建议了 N 个待补节点"。

### 3.6 SQLite 与 MySQL 迁移路径分离

**遇到的坑**:`alembic upgrade head` 在 SQLite 上一次跑多个 revision 时,batch_alter_table add_column 会触发"重建表"路径;重建用 `target_metadata`(已含新列)→ 再 add_column 报"duplicate column"。

**修复方案**:把 0002 拆为两个 migration:
- 0002:仅 create slug_redirects 表
- 0003:wiki_node_versions add_column 两个新字段

这样 0001 → 0002 → 0003 三步独立,SQLite head-jump 不再触发 batch 重建。MySQL 上行为完全一致(MySQL 原生支持 ALTER ADD COLUMN,不走 batch 重建路径)。

集成测试(`tests/integration/conftest.py`)用 `Base.metadata.create_all` 直接建表(不走 alembic),所以测试侧不受影响,**同时**生产 MySQL 与开发 SQLite 都走干净的 migration 链。

---

## 4. 与主规划的对齐

| 规划条目 | 实现位置 | 备注 |
|---------|---------|------|
| F4 batch-ingest(每 5 个一暂停)| `services/ingestion/batch_state.py` + `api/v1/ingestion.py` 4 个新端点 | 状态机持久化到文件 |
| F6 delete 素材(级联清理 + 断链检查)| `services/wiki/delete.py` + `api/v1/wiki.py:DELETE /sources/{slug}` | 含 slug_redirects |
| F7 crystallize 结晶化 | `services/crystallize/__init__.py` + `api/v1/crystallize.py` | LLM 单次调用 + gap_queue 入队 |
| §3.6 飞轮:merge_suggestion 入队 | `crystallize_text` 里 `gap_queue(origin=merge_suggestion)` | 与规划 §3.6 状态机对齐 |
| 节点版本化(`wiki_node_versions` + 历史)| `services/wiki/version_history.py` + pipeline `_persist_version_records` + `GET /versions` 两端点 | 含 diff_stat 摘要 + actor_user_id |
| 反链表完善(双向 + 跳转)| `GET /wiki/pages/{slug}/backlinks` 走 `wiki_links` 表 + 全文扫描降级 | 前端 Graph3D 可消费 |
| §6 R5 [[wikilink]] 自动维护 + slug_redirect 表保稳定 | `slug_redirects` 表 + `GET /wiki/pages/{slug}` 跟随逻辑 | rename / source_deleted / merge / manual 四种 reason |

---

## 5. alembic 验证

**MySQL(host:3309)**:

```
$ python -m alembic upgrade head
INFO  [alembic.runtime.migration] Context impl MySQLImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.

$ python -m alembic current
0003 (head)

$ python -m alembic check
No new upgrade operations detected.
```

**SQLite(临时验证)**:

```
$ python -c "from alembic.config import Config; from alembic import command; \
    cfg = Config('alembic.ini'); \
    cfg.set_main_option('sqlalchemy.url', 'sqlite:///./test.db'); \
    command.upgrade(cfg, 'head'); command.check(cfg)"
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, initial
INFO  [alembic.runtime.migration] Running upgrade 0001 -> 0002, P3.1: slug_redirects 表
INFO  [alembic.runtime.migration] Running upgrade 0002 -> 0003, add actor_user_id and title_snapshot
No new upgrade operations detected.
```

**结论**:0003 head 在 MySQL 8.0 与 SQLite 都干净跑通,model ↔ db 零漂移。

---

## 6. 未交付项(归到 P4 / P5)

P3 任务清单全部完成。下面按规划文档:

- **P4**(读出侧深化 + 飞轮启动):
  - digest 三模板(deep-dive / comparison / timeline)
  - answer-quality 评分公式 + tier + gap_flag
  - gap-queue 状态机(open → in_progress → resolved → reopened)
  - IE 领域风格 prompt(`prompts/query_ie_method.md` / `query_ie_concept.md`)
  - QA 路径高亮(query 完成给前端 path payload)
- **P5**(运营侧 + 自我维护):
  - lint / status / trend / followups 端点
  - firecrawl 接入
  - 配置存储 JSON → MySQL `system_config` 表归位
  - prompt_versions 表的 A/B 切换
- **P6**(生产部署 + 性能):
  - Celery + Redis 替换 BackgroundTasks
  - Nginx + HTTPS + Grafana 看板 + 备份脚本

---

## 7. 工时

约 3.5 小时:
- ~10 min 侦察 + 用户决策(范围 = 全做 5 块)+ 8 任务清单
- ~30 min P3.1 slug_redirects 表 + ORM + alembic 0002/0003
- ~50 min P3.2 wiki/delete.py 真实现 + wiki API DELETE/GET 改造 + 0002 拆分修 SQLite migration 坑
- ~30 min P3.3 batch_state.py + ingestion API 5 个端点 + schema 扩展
- ~30 min P3.4 crystallize 真实现(prompt 渲染 + LLM 调用 + 候选入队)
- ~20 min P3.5 version_history.py + pipeline `_persist_version_records` + wiki API 2 个新端点
- ~10 min P3.6 backlinks API
- ~50 min P3.7 单测(43 项)+ 集成(19 项)
- ~10 min P3.8 报告 + 文档更新
