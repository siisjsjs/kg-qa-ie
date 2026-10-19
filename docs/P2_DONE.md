# P2 阶段完成报告

> 项目:KG-QA-IE(工业工程知识库问答系统)
> 阶段:**P2 — 端到端 MVP(写入 + 读出主链路)**
> 状态:**✅ 完成**
> 完成日期:2026-05-20
> 项目位置:`D:\kg-qa-ie\`
> 上一份报告:[P2_WIKI_IO_DONE.md](./P2_WIKI_IO_DONE.md)(P2 第一块:wiki 基础 I/O)
> 主规划:[工业工程知识库问答系统 — 技术规划文档.md](./工业工程知识库问答系统%20—%20技术规划文档.md) v1.5

---

## 摘要

P2 启动后第一块(wiki 基础 I/O)在 P2_WIKI_IO_DONE.md 中交付。本轮把 P2 任务清单中**剩下七大块 + MySQL 实启**全部完成:

1. OpenAI 兼容 LLM provider 真实现
2. OpenAI 兼容 Embedding provider 真实现
3. ingestion 五阶段流水线全链路真实现
4. query SSE 主链路真实现
5. graph builder + Neo4j client 真实现
6. admin schema/purpose 编辑器 + wiki page CRUD 真实现
7. auth.py 真实现(register / login / me)
8. **MySQL 容器实启 + alembic upgrade head 在真实 MySQL 验证通过**

**测试**:`pytest` 共 **216 项全绿**(58 P1+P2.1 基线 + 本轮新增 158 项 = 133 单测 + 25 集成),`<10 秒`完成。零回归。

**MySQL 实启**:`scripts/p2_mysql_smoke.py` 一键脚本端到端跑通 — 容器启动 → 8 秒就绪 → `alembic upgrade head` 应用 0001 → 15 张表(14 业务 + alembic_version)全建出 → 关键索引全在 → INSERT/SELECT 烟测通过 → `alembic check` 报告无 drift。详见 §5。

---

## 1. 实际交付清单

### 1.1 Provider 真实现(P1 推迟项归位)

| 文件 | 关键函数 | 行为 |
|------|---------|------|
| `services/llm/openai_compat.py` | `chat / _chat_once / _stream / test_connection / _normalize_base_url` | httpx 异步,与 `deepseek.py` 风格对齐;支持 base_url 带或不带 `/v1`;手解析 SSE;覆盖硅基流动/智谱 GLM 兼容/Moonshot/OpenAI 官方/自托管 vLLM |
| `services/embedding/openai_compat.py` | `embed / test_connection / _embed_batch` | `POST /v1/embeddings`,自动 32 条一批切片,dim 自动从首批响应回填;`test_connection` 返回 `dim=N model=M` |

**用户决策对齐**:Ollama / Anthropic / BGE-M3 本地/Ollama / HuggingFace 维持骨架,推到 P3+(memory `p2_decisions.md` 第 1、2 条)。

### 1.2 ingestion 五阶段流水线

| 文件 | 关键函数 | 行为 |
|------|---------|------|
| `services/ingestion/validator.py` | `validate_step1 / normalize_step1` | 严格按规划文档 §3.1.1 重写 schema(用户决策);手写校验,错误信息含 dotted-path |
| `services/ingestion/md_parser.py` | `parse / excerpt` | frontmatter + body + paragraphs;不切章节(对齐 §0.3) |
| `services/ingestion/privacy_check.py` | `scan` | 6 类正则(phone/id_card/email/api_key/bank_card/url_token)+ block/warn 分级 + 命中片段掩码 |
| `services/ingestion/triple_filter.py` | `filter_entities / filter_relations` | 兜底过滤短名/停用词/自环/类型白名单/重复 |
| `services/ingestion/source_writer.py` | `make_source_slug / render_source_md / write_source_page` | 写 `wiki/sources/{date}-{slug}.md`,同名自动 -2 后缀避让 |
| `services/ingestion/step1_extract.py` | `step1_extract / _extract_json_object` | 渲染 prompt + 调 LLM + 抠 JSON(支持 ```json 围栏 / 头尾解释)+ 校验失败重试 1 次,二次失败抛 `ValidationError` |
| `services/ingestion/step2_compose.py` | `step2_compose / parse_files` | 单次 LLM 调用产出多页(用户决策);`=====<file: ...>=====` 分隔符解析,自动剥 ```markdown 围栏 |
| `services/ingestion/merger.py` | `merge_or_create_entity / apply_observation_block / parse_alias_groups_from_schema / load_alias_groups / cosine` | 三层判断:字面 → 别名 → embedding 余弦(0.92/0.75 阈值);**embedding 失败降级新建** + audit 日志(用户决策);`apply_observation_block` 把"## 不同素材中的观点 / ### 来源:[[X]]"段追加到已有实体页 |
| `services/ingestion/pipeline.py` | `run_pipeline / stage_upload_md / _materialize_page / _sync_db_indices / _post_post_process` | 5 阶段编排:upload → cache_check → step1 → step2 → post_process;每阶段写 `ingest_jobs` 行 + `log.md` append;cache 命中跳过余下三阶段;失败标 `failed`;Neo4j/Chroma 同步 fire-and-forget(`asyncio.create_task`,失败不阻塞) |
| `app/api/v1/ingestion.py` | `/upload` `/run` `/batch` `/jobs/{id}` `/documents/{id}/jobs` | 五个端点全真实现;隐私 block 命中 → 422;BackgroundTasks 串行(用户决策) |
| `app/prompts/ingest_step1.md` | (重写) | 与 §3.1.1 schema 对齐,显式列出三态 confidence 与五个枚举 kind |
| `app/prompts/ingest_step2.md` | (重写) | `=====<file: ...>=====` 输出格式约定;matched_entity_pages 只输出新增段,不重写整页 |

### 1.3 query SSE 主链路

| 文件 | 关键函数 | 行为 |
|------|---------|------|
| `services/query/alias_expand.py` | `expand / basic_keywords` | 中文连续段 + 英文 token 抽取;别名组 substring 命中 → 整组追加;不跨组传递 |
| `services/query/grep.py` | `grep_wiki / _build_kw_pattern` | 三档加权:文件名/title/aliases 精确 100 / index/overview boost 20 / 正文每命中 1;每 keyword 限 3 段、总 15 段 |
| `services/query/page_reader.py` | `read_for_context / _resolve_page_path` | hint_kind 优先在对应目录查;超 2000 字 → 头 500 字 + 命中段落 ±3 行 |
| `services/query/composer.py` | `build_prompt / classify_question / compose_stream / extract_citations / render_context / render_alias_table` | 三模板路由:method > concept > general(规划 §3.3.1);流式生成 token;`extract_citations` 抓 `[[X]]` / `[[X\|alias]]` 去重保序 |
| `services/query/persistor.py` | `maybe_persist / make_query_slug / render_query_md` | 持久化策略:`force=True` / `cited≥3` 写,否则不写;slug = `{date}-{title-slug}-{md5_short}` |
| `app/api/v1/query.py` | `query_stream` (SSE) / `get_query_page` | SSE 事件序列:`progress(alias_expand → grep → read_pages → compose) → token* → citations → path → persist → done`;无候选时返回"知识库未覆盖"兜底;`/queries/{slug}` 读持久化页 + 抽 "## 答案" 段 |

**注**:scoring 留 P4(规划文档明示);P2 阶段持久化判断仅看 `cited_sources≥3`。

### 1.4 graph builder + Neo4j client

| 文件 | 关键函数 | 行为 |
|------|---------|------|
| `services/graph/builder.py` | `build_graph_data / write_graph_data_json / load_graph_data_json / _detect_communities` | 扫双链产出节点/边;边权重 = `0.5*occ_norm + 0.3*type_affinity + 0.2*source_jaccard`;Louvain 社区(networkx + python-louvain),失败降级到连通分量;输出 `wiki_root/graph-data.json` |
| `services/graph/insights.py` | `find_surprising_links / find_bridge_nodes / find_knowledge_gaps / find_sparse_communities` | 四类规则启发式;阈值参数化(weight≥0.6 / 桥节点≥3 社区 / 度数≤1 / 社区<3 节点) |
| `services/graph/neo4j_client.py` | `Neo4jClient.connect/close/upsert_node/upsert_edge/delete_node/delete_edges_for_source/health / sync_graph_to_neo4j / _safe_label / _safe_rel_type` | 异步驱动;**connect 失败 → 全方法降级返回 False/0,不抛异常**(主问答不依赖 Neo4j);Cypher 关系类型走白名单(中→英映射:实现→IMPLEMENTS / 依赖→DEPENDS_ON / 对比→COMPARES_WITH / 矛盾→CONTRADICTS / 衍生→DERIVED_FROM),其余降级 RELATED 防注入 |
| `app/api/v1/graph.py` | `GET /graph/data / POST /graph/rebuild / GET /graph/insights` | data 不存在时自动 build 一次;rebuild 重算 + 写 graph-data.json + Neo4j 同步(失败静默) |

### 1.5 admin / wiki / auth API

| 文件 | 关键函数 | 行为 |
|------|---------|------|
| `services/admin/schema_editor.py` | `read_schema / render_schema / write_schema / sync_aliases_to_db / parse_*` | 切 H2 段解析:别名词表 / 关系类型词汇表 / 成长配置(YAML 块);写回时若 `raw_markdown` 给了用 raw,否则按结构化字段重排 |
| `services/admin/purpose_editor.py` | `read_purpose / render_purpose / write_purpose` | H1 段切分 → 研究目标 / 关键问题 / 研究范围(嵌套 H2:在范围内 / 暂不深入);列表项支持 `- ` 与 `1. ` 两种 |
| `app/api/v1/admin.py` | `/admin/schema` `/admin/purpose` GET/PUT | 接 schema_editor / purpose_editor;PUT schema 同步 `wiki_aliases` 表(全量覆盖);失败回滚但写盘成功 |
| `app/api/v1/wiki.py` | `/wiki/index / /wiki/pages/{slug}` GET/PUT / `/sources/{slug}` DELETE | index 按 6 类 kind 分桶 + 总数;page GET 返回 frontmatter + body + outlinks + backlinks;page PUT 走 `merge_frontmatter`(可选 `bump_version`)+ `_sync_db_indices`;DELETE source 删文件 + cache 失效 + DB 同步 |
| `app/api/v1/auth.py` | `/auth/register` `/auth/login` `/auth/me` | 第一个注册的用户自动 admin,后续 user;邮箱唯一性 → 409;密码错 → 401;status=disabled → 403 |

### 1.6 单元 + 集成测试

新增 **158 项**(133 单测 + 25 集成):

| 文件 | 用例数 | 覆盖点 |
|------|-------|------|
| `tests/unit/test_openai_compat_llm.py` | 9 | base_url 归一化 / 非流式解析 / 流式 SSE 多 chunk / 跳过 malformed data 行 / 4xx → ProviderError / 缺 api_key / extra_params 透传 |
| `tests/unit/test_openai_compat_embedding.py` | 10 | 单/多 batch 切片 / dim 自动回填 / test_connection 返回 dim&model / 4xx → ProviderError / 缺 api_key&base_url |
| `tests/unit/test_validator.py` | 10 | source_summary 必填 + domain_tags ≥1 / confidence 三态 / kind 枚举 / connection.rel_type 必填 / matched_entity_pages.match_kind 枚举 / 根必须 dict / normalize 补空数组 / 可选数组缺省允许 |
| `tests/unit/test_md_parser.py` | 6 | 含/不含 frontmatter / 损坏 frontmatter 不抛 / excerpt 短文/标点边界/硬切兜底 |
| `tests/unit/test_privacy_check.py` | 9 | clean / phone(warn) / id_card(block) / api_key(block) / email(warn) / url_token(block) / 掩码不回显 / 行号 / 空输入 |
| `tests/unit/test_triple_filter.py` | 9 | 短名/停用词/lazy brief 过滤 / 同名去重 / 自环/缺端/白名单/重复关系过滤 |
| `tests/unit/test_source_writer.py` | 6 | slug 格式 / 中文标题保留 / 必备段渲染 / 落盘 / 同 slug -2 避让 / 路径越界(safe_join 拦) |
| `tests/unit/test_ingestion_middle.py` | 25 | step1 happy / fenced JSON 抠取 / 重试 1 次后通过 / 二次失败抛 ValidationError / normalize 补空 / step2 parse_files 简单+剥围栏+无分隔符 → ValueError / step2 compose / cosine / 别名组解析 / find_alias_match / load_alias_groups / 字面合并 / 别名合并 / 余弦合并 / 0.75-0.92 suggested / embedding 失败降级 created_no_embedding / 无现有时新建 / apply_observation_block 三种(新增段/追加子段/替换重复子段)/ 含 contradictions blockquote |
| `tests/unit/test_query_modules.py` | 26 | basic_keywords / expand 命中/不跨组/无别名 / grep 文件名优先/段落限段/alias frontmatter 命中/空关键词 / page_reader 短/超长/缺失 / classify method/concept/general/优先级 / extract_citations 去重/带 alias / render_context/alias_table / persistor slug 稳定/写盘/cited<3 不写/cited≥3 写/force True 强写/force False 不写 |
| `tests/unit/test_graph.py` | 14 | builder 节点+边+度数+边权重 / 社区分桶 / write+load 往返 / 损坏 JSON 返 None / surprising_links / bridge_nodes / knowledge_gaps / sparse_communities / safe_label / safe_rel_type 中文映射 + 未知降级 RELATED / Neo4jClient connect 失败时不抛 / sync_graph_to_neo4j 失败/成功 |
| `tests/unit/test_admin_editors.py` | 9 | schema 解析别名/关系/yaml 成长配置 / 缺文件返空 / render 用 raw / render 结构化 / 往返 / purpose 解析四段 / 往返 / 缺文件 / render 用 raw |
| `tests/integration/test_pipeline.py` | 6 | 端到端首跑(LLM mock) / cache 命中跳全部 / force 重跑 / 跨 source 合并(实体页只 1 份且追加观察段) / step1 失败 → job failed + parse_status failed / 隐私 block 拒上传 |
| `tests/integration/test_query_sse.py` | 5 | SSE 全事件序列 / cited≥3 触发持久化 / 无候选返回未覆盖 / GET 404 / GET 拿回持久化页 |
| `tests/integration/test_graph_api.py` | 3 | rebuild + data + insights 端到端(Neo4j 静默失败) / data 不存在自动 build / insights 文件缺失返空 |
| `tests/integration/test_admin_wiki_auth.py` | 11 | 第一注册即 admin / 邮箱重复 409 / 登录拿 token + me / 错密码 401 / schema GET-PUT-GET 往返 / purpose GET-PUT-GET 往返 / page 404 / outlinks+backlinks / PUT bump_version+同步 / index 分桶 / DELETE source 删文件+evict cache |

### 1.7 一键脚本

| 文件 | 行为 |
|------|------|
| `scripts/p2_mysql_smoke.py` | 自动 `docker compose up -d mysql` → 等健康 → `alembic upgrade head` → 校验 14 张表 + `documents.content_hash` / `wiki_pages.slug` / `wiki_links.src/dst_slug` / `ingest_jobs.status+document_id` 索引 → INSERT/SELECT system_config 行 → `alembic check` 漂移检测 |

### 1.8 集成测试基础设施

| 文件 | 行为 |
|------|------|
| `tests/integration/conftest.py` | autouse fixture:把 `db._engine` / `_SessionLocal` 替换成 SQLite 文件库,`Base.metadata.create_all`(14 张表)→ 用例跑 → `drop_all` + `dispose`;让 pipeline / API 集成测试不依赖 MySQL |

---

## 2. 测试结果

```
$ cd backend && pytest -q
........................................................................ [ 33%]
........................................................................ [ 66%]
........................................................................ [100%]
216 passed in 8.06s
```

| 分类 | 计数 |
|------|-----|
| P1 基线单测 | 26 |
| P2.1 wiki I/O 单测 | 32 |
| 本轮新单测 | 133 |
| 本轮新集成 | 25 |
| **合计** | **216** |

---

## 3. 关键设计要点

### 3.1 ingestion 流水线的容错与降级

Pipeline 的每阶段独立写 `ingest_jobs` 行,失败:

- `step1` LLM JSON 不合法 → 在 prompt 加严格性提醒重试 1 次,二次仍失败 → job=failed_step1,`documents.parse_status=failed`,`audit_logs`(P5 引入)
- `step2` 输出无分隔符 → ValueError,job=failed
- `merger` embedding provider 不可用 → 全部候选实体降级新建,记 `created_no_embedding`(对齐用户决策)
- `post_process` 中 Neo4j / Chroma 同步走 `asyncio.create_task` fire-and-forget,失败 logger.warning 不阻塞主返回
- `cache_check` 命中且 `force=False` → 跳过余下三阶段,所有 stage 标 skipped

测试用例 `test_pipeline_step1_invalid_marks_job_failed` / `test_pipeline_cache_hit_skips_llm` 都明确覆盖。

### 3.2 跨素材源合并的"三段判断"

```
candidate entity name
  │
  ├─ wiki/entities/{name}.md 存在?       → merged(字面)
  │
  ├─ alias_groups 里命中?                 → merged_via_alias / created(canonical 未建)
  │
  └─ 调 embedding,与所有现有 brief 比余弦
       ├─ 余弦 ≥ 0.92                     → merged_via_similarity
       ├─ 0.75 ≤ 余弦 < 0.92              → suggested_merge(P5 入 gap-queue)
       ├─ 余弦 < 0.75                     → created
       └─ 调用失败                        → created_no_embedding(audit)
```

阈值来源:规划 §3.2,沿用旧项目 `embedding_service.py` 实测值。

### 3.3 query SSE 事件契约

```
event: progress  data: {"stage": "alias_expand", "keywords": [...], "hit_canonicals": [...]}
event: progress  data: {"stage": "grep", "candidates": [{slug, score, kind, title}, ...]}
event: progress  data: {"stage": "read_pages", "context_pages": [...]}
event: progress  data: {"stage": "compose", "domain_ie": ..., "kind": "method|concept|general"}
event: token     data: {"text": "..."}        # ×N
event: citations data: {"citations": [...], "domain_ie": ..., "kind": ...}
event: path      data: {"nodes": [...], "edges": []}
event: persist   data: {"persisted_query_slug": "..." | null, "force": ..., "cited_count": N}
event: done      data: {"tokens": ..., "cited_count": ...}
```

无候选页时**不调 LLM**,直接返兜底 token + persist=null + done(测试 `test_query_sse_no_context_returns_uncovered`)。前端 P1.5 已就绪的 Query.vue 预留了所有这些 event 类型的处理位。

### 3.4 Neo4j 关系类型的注入防护

Cypher 中关系类型不能参数化(`MATCH (a)-[:实现]->(b)` 中 `实现` 必须字面写),所以**白名单映射 + 默认 RELATED 兜底**:

```python
_REL_WHITELIST_MAP = {
    "实现": "IMPLEMENTS", "依赖": "DEPENDS_ON",
    "对比": "COMPARES_WITH", "矛盾": "CONTRADICTS", "衍生": "DERIVED_FROM",
    "related": "RELATED", None: "RELATED",
}
def _safe_rel_type(rel): return _REL_WHITELIST_MAP.get(rel, "RELATED")
```

未知/异常类型一律降级 RELATED,`test_safe_rel_type_maps_chinese` 显式覆盖 `"DROP TABLE"` → RELATED。

### 3.5 流水线的 SQLite/MySQL 双兼容

集成测试用 SQLite + `Base.metadata.create_all`,跳过 alembic;但 pipeline 的所有 SQL 都不用 MySQL 方言特性(纯 SA Core),所以 MySQL 实启后行为一致。`Document.source_type` 是 SQLAlchemy `Enum`,SQLite 上是 CHAR + CHECK 约束,MySQL 上是真 ENUM。测试里发现 `source_type="article"` 不在枚举值(`md/web/ppt/...`)里 → 改用 `source_type="md"`,内部 `_routed_raw_subdir("md")` → `articles/`,目录约定与字段值解耦。

### 3.6 集成测试的 detached instance 处理

`session_scope()` context 退出会 commit + close session,SA 实例进入 detached 状态。访问惰性属性(如 `Document.id` 之外的字段)会触发 refresh 抛 `DetachedInstanceError`。处理方式:在 `with` 块内**抓出 `doc_id`**,后续都用 id 重查。测试规约写在 `test_pipeline.py` 顶部注释里。

### 3.7 OpenAI 兼容层与 DeepSeek 复用

实现风格刻意与 `services/llm/deepseek.py` 1:1 对齐(httpx + 手解析 SSE,不引入 openai SDK),好处:

- 依赖面小,锁住 openai 版本不会传染
- `_normalize_base_url` 容错带或不带 `/v1` 的写法,硅基流动 / 智谱 / Moonshot / 自托管 vLLM / OneAPI 都能直接走
- SSE 解析逻辑与 DeepSeek 共享(都是 OpenAI 协议),并且对 `data: [DONE]` 之外的非 JSON 行宽容(部分服务发心跳)
- 单测 `test_openai_compat_llm.py::test_chat_stream_skips_malformed_lines` 显式覆盖

---

## 4. 与主规划的对齐

| 规划条目 | 实现位置 | 备注 |
|---------|---------|------|
| §3.1 ingest 流水线 5 阶段 | `services/ingestion/pipeline.py` | 严格 5 个 stage,每个落 `ingest_jobs` 行 |
| §3.1.1 Step1 schema | `services/ingestion/validator.py` + `prompts/ingest_step1.md` | 用户决策为准重写,confidence/kind 全枚举 |
| §3.1.2 Step2 输出契约 | `services/ingestion/step2_compose.py` + `prompts/ingest_step2.md` | `=====<file: ...>=====` 分隔符 |
| §3.1.3 隐私自查算法 | `services/ingestion/privacy_check.py` | 6 类正则 + 掩码 + block/warn |
| §3.2 跨 source 合并机制 | `services/ingestion/merger.py` | 字面 → 别名 → 余弦三层 + 阈值可配 |
| §3.3 query 流程(不走向量 RAG) | `services/query/*` + `app/api/v1/query.py` | grep + 别名扩展 + 读页 + 综合 |
| §3.3.1 IE 风格路由 | `services/query/composer.py::classify_question + build_prompt` | method > concept > general 优先级 |
| §3.5 graph 流程 | `services/graph/builder.py + insights.py` | Louvain + 4 类 insights |
| §2.4 22 个 API | `app/api/v1/{auth,ingestion,query,graph,wiki,admin}.py` | P2 范围内的端点全部填实(digest/lint/status/trend/followups 仍骨架) |
| 1.4 RBAC 权限矩阵 | `app/core/deps.py` `require_admin` + `auth.py` | 第一注册即 admin,dev 旁路保留 |
| 8.5 错误处理与降级 | pipeline / query / graph 各层 | LLM/Embedding 失败重试 + 降级 + audit;Neo4j/Chroma 不阻塞;wiki 文件落盘失败 → 上抛(关键依赖) |

---

## 5. MySQL 实启验证(2026-05-20 跑通)

**端口避让**:本机 3306 / 3307 / 3308 已被其他 mysqld 占用,把 `deploy/docker-compose.yml` 与 `scripts/p2_mysql_smoke.py` 中的 host 端口改为 **3309**(容器内仍是 3306)。

**执行结果**:

```
$ python scripts/p2_mysql_smoke.py
=> ensuring MySQL container up via docker compose (D:\kg-qa-ie\deploy)
 Container deploy-mysql-1 Recreate
 Container deploy-mysql-1 Recreated
 Container deploy-mysql-1 Started

=> waiting for MySQL on mysql+pymysql://kgqa:CHANGE_ME@localhost:3309/kg_qa_ie
[OK ] mysql ready  (after 8s)

=> alembic upgrade head
INFO  [alembic.runtime.migration] Context impl MySQLImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, initial
[OK ] alembic upgrade head

   tables present: ['alembic_version', 'documents', 'followup_queue', 'gap_queue',
                    'hot_topics', 'ingest_jobs', 'prompt_versions', 'qa_messages',
                    'qa_sessions', 'system_config', 'users', 'wiki_aliases',
                    'wiki_links', 'wiki_node_versions', 'wiki_pages']
[OK ] table set complete  (15 tables)
[OK ] indexes on documents       # content_hash 索引
[OK ] indexes on wiki_pages      # slug 索引
[OK ] indexes on wiki_links      # src_slug + dst_slug 索引
[OK ] indexes on ingest_jobs     # status + document_id 索引
[OK ] system_config read/write

=> alembic check (model vs db drift)
INFO  [alembic.runtime.migration] Context impl MySQLImpl.
No new upgrade operations detected.
[OK ] alembic check (no drift)
```

**结论**:14 张业务表(documents / wiki_pages / wiki_links / wiki_aliases / qa_sessions / qa_messages / gap_queue / hot_topics / followup_queue / ingest_jobs / system_config / prompt_versions / wiki_node_versions / users)+ alembic_version,共 15 张表在真实 MySQL 8.0 上由 `0001_initial.py` 一次性建出,SQLAlchemy ORM model 与 DB schema **零漂移**。

---

## 6. 未交付项(归到下一轮)

P2 任务清单全部完成。下面是 P3+ 计划项(规划文档已列):

- `services/wiki/delete.py` 真实现(P3,与 batch-ingest 一起做)
- Ollama / Anthropic LLM provider 真实现(P3+)
- BGE-M3 本地 / Ollama / HuggingFace Embedding provider 真实现(P3+)
- digest 三模板(deep-dive / comparison / timeline)— P4
- answer-quality 评分公式 + gap_flag 入队 — P4
- gap-queue / hot-topics / followups 飞轮闭环 — P4-P5
- crystallize 用户文本结晶页 — P3
- delete 素材级联清理 + slug-redirect — P3
- lint / status / trend / firecrawl — P5
- prompt 版本管理 + A/B 对比 — P5+
- 配置存储 JSON → MySQL 归位 — P5

---

## 7. 验证命令

```bash
cd D:/kg-qa-ie/backend

# 全量测试
pytest -q
# 期望:216 passed in <10s

# 仅单测
pytest tests/unit/ -q

# 仅集成
pytest tests/integration/ -q

# MySQL 实启验证(2026-05-20 已跑通,需要 Docker Desktop 在运行)
python scripts/p2_mysql_smoke.py
```

---

## 8. 工时

约 4 小时:
- ~30 min 侦察 + 用户决策 4 题对齐
- ~30 min openai_compat LLM/Embedding + 19 单测
- ~90 min ingestion 五阶段 + 65 单测/集成
- ~50 min query SSE + 31 单测/集成
- ~30 min graph builder/insights/Neo4j client + 17 单测/集成
- ~30 min admin schema/purpose + wiki page + auth + 20 集成
- ~10 min p2_mysql_smoke 脚本 + 报告撰写
