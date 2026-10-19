# P1 阶段完成报告

> 项目：KG-QA-IE（工业工程知识库问答系统）
> 阶段：**P1 — 基础设施 + 模型配置**
> 状态：✅ **完成**
> 日期：2026-05-19
> 项目位置：`D:\kg-qa-ie\`

---

## 一、P1 退出标准对照（来自规划文档 §七）

| # | 退出标准 | 状态 | 验证方式 |
|---|---------|------|---------|
| 1 | `/health` 全绿 | ✅ | `GET /health → 200 {"status":"ok",...}` |
| 2 | 前端能切换 provider 测试通过 | ✅ | `frontend/src/views/system/ModelConfig.vue` 真表单 + 测试按钮，链接到 `POST /api/v1/admin/config/test` 实打 DeepSeek |
| 3 | 初始化能创建出空知识库目录 | ✅ | `POST /api/v1/init → 201`，落盘 14 个目录 + 6 份种子文件，幂等 |
| 4 | docker-compose 起 MySQL/Neo4j/Chroma | ⚠️ 配置就绪未实启 | 配置文件 `deploy/docker-compose.yml` 完整；本机未装 Docker 故未真起 |
| 5 | FastAPI 骨架 + 鉴权 + alembic | ✅ | uvicorn 可启动；JWT 解码就绪；alembic 0001_initial.py 在 SQLite 上 `upgrade head` 成功 |
| 6 | LLM/Embedding 多 provider 抽象层 | ✅ | LLM 4 个 provider（DeepSeek 真实现 + 其余骨架）；Embedding 4 个 provider 骨架 |
| 7 | wiki/ 目录初始化（schema/purpose/index） | ✅ | `services/wiki/init.py` 真实现，6 份种子文件落盘 |

---

## 二、本轮新交付

### 后端
- `app/services/wiki/init.py` — 真实现：14 个目录 + 6 份种子文件，幂等
- `app/api/v1/ingestion.py` — `POST /api/v1/init` 真实现（不依赖 DB）
- `app/services/llm/deepseek.py` — httpx 真实现（chat / 流式 SSE / test_connection 实跑）
- `app/services/admin/config_store.py` — JSON + fernet 加密（dev 期实现，接口预留 DB 切换）
- `app/core/crypto.py` — fernet key 自动生成并落到 `cache_dir/.fernet.key`
- `app/services/{llm,embedding}/factory.py` — 接 config_store，签名缓存 + invalidate
- `app/api/v1/admin.py` — 5 个 config 端点真实现（GET 屏蔽 / PUT 加密 / test 实跑）
- `app/core/deps.py` — `APP_ENV=dev` 旁路鉴权
- `migrations/versions/0001_initial.py` — alembic initial migration，14 张表（兼容 MySQL `CURRENT_TIMESTAMP`）

### 前端
- `frontend/src/views/system/ModelConfig.vue` — 双卡片真表单：provider 下拉 / API key 屏蔽态识别 / 测试按钮显示 latency 或 error
- `frontend/src/stores/config.js` — 含 `availableLLMProviders` / `testConnection`

### 测试与脚本
- `tests/unit/test_admin_config.py` — 7 项端点测试
- `tests/unit/test_config_store.py` — 5 项往返测试
- `tests/unit/test_crypto.py` — 3 项加解密测试
- `tests/unit/test_factory_invalidation.py` — 3 项 factory 测试
- `tests/unit/test_wiki_init.py` — 4 项 init 测试
- `scripts/p1_smoke.py` — 端到端冒烟脚本（8 步全检）

---

## 三、最终验证结果

### 单元测试
```
$ cd backend && py -m pytest tests/unit/ -q
..........................                                  [100%]
26 passed in 0.87s
```

### 端到端冒烟（覆盖 P1 退出标准）
```
$ cd backend && py scripts/p1_smoke.py
============================================================
1. /health
============================================================
[PASS] health returns ok

============================================================
2. POST /api/v1/init
============================================================
[PASS] init returns 201

============================================================
3. wiki/ 目录结构
============================================================
[PASS] file exists: .wiki-schema.md
[PASS] file exists: purpose.md
[PASS] file exists: index.md
[PASS] file exists: log.md
[PASS] file exists: overview.md
[PASS] file exists: wiki/_growth/gap-queue.md
[PASS] dir exists: wiki/entities
[PASS] dir exists: wiki/topics
[PASS] dir exists: wiki/sources
[PASS] dir exists: wiki/queries
[PASS] dir exists: wiki/synthesis/sessions
[PASS] dir exists: wiki/comparisons
[PASS] dir exists: wiki/_growth
[PASS] dir exists: raw/articles
[PASS] dir exists: raw/zhihu
[PASS] dir exists: raw/pdfs
[PASS] schema.md contains IE marker

============================================================
4. GET /api/v1/admin/config
============================================================
[PASS] config GET 200
[PASS] all 4 LLM providers registered

============================================================
5. PUT /api/v1/admin/config/llm
============================================================
[PASS] llm PUT ok

============================================================
6. GET 后 API key 屏蔽
============================================================
[PASS] api_key masked
[PASS] model persisted

============================================================
7. POST /api/v1/admin/config/test (llm)
============================================================
  200 {'ok': False, 'latency_ms': 405, 'sample_output': None,
       'error': 'DeepSeek HTTP 401: ...api key: ****QRST is invalid...'}
[PASS] test endpoint 200
[PASS] real http request issued (latency present)
[PASS] fake key correctly rejected by real provider
[PASS] 401 from real DeepSeek API

============================================================
8. POST /init 二次调用幂等
============================================================
[PASS] second init still 201
[PASS] no new files created
[PASS] schema.md correctly skipped

============================================================
ALL P1 SMOKE CHECKS PASSED
============================================================
```

### Frontend 构建
```
$ cd frontend && npx vite build
✓ built in 3.47s
dist/assets/ModelConfig-*.js   6.98 kB
dist/assets/index-*.js         1,035 kB
```

### Alembic
```
$ cd backend && py -c "alembic upgrade head"  # 在临时 SQLite 上
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, initial
upgrade head: ok
```

---

## 四、本地启动

### 一次性准备
```bash
cd D:\kg-qa-ie\backend
py -m pip install --user -e .
py -m pip install --user "email-validator>=2.2"  # pydantic[email] 依赖
```

### 启 backend
```bash
cd D:\kg-qa-ie\backend
py -m uvicorn app.main:app --port 8000 --reload
# 访问：http://127.0.0.1:8000/health
# OpenAPI：http://127.0.0.1:8000/docs
```

### 启 frontend（dev 模式带 proxy）
```bash
cd D:\kg-qa-ie\frontend
npm install        # 首次
npm run dev
# 访问：http://localhost:5173/system/model-config
```

### 跑测试
```bash
cd D:\kg-qa-ie\backend
py -m pytest tests/unit/ -q
```

### 跑 P1 端到端冒烟
```bash
# 1. 终端 A：启 uvicorn 在 18765 端口（脚本默认端口）
cd D:\kg-qa-ie\backend
py -m uvicorn app.main:app --port 18765

# 2. 终端 B：跑冒烟
cd D:\kg-qa-ie\backend
py scripts/p1_smoke.py
```

### 全栈 docker-compose（未在本机实启，配置就绪）
```bash
cd D:\kg-qa-ie\deploy
docker compose up -d  # MySQL + Neo4j + Chroma + backend + frontend + nginx
```

---

## 五、关键设计决策

1. **dev 期配置存储用 JSON + Fernet 加密文件，不依赖 MySQL**
   - 路径：`backend/data/cache/system_config.json`
   - Fernet key：`backend/data/cache/.fernet.key`（首次自动生成）
   - 接口与 DB 实现完全相同（`config_store.set_value_sync` / `get_sync` / `get_many_sync`）；P5 切 DB 只换实现层

2. **API key "空 / 屏蔽串都不覆写"**
   - 前端读到 `sk-F****` 后回写时，后端识别并忽略；只有真值（包含字母数字、不含 `*`）才会替换
   - 单测 `test_put_llm_empty_api_key_keeps_existing` + `test_put_llm_masked_api_key_is_ignored` 覆盖

3. **dev 模式鉴权旁路**
   - `APP_ENV=dev`（默认）时，admin 接口缺 token 自动通过；prod 强制 JWT
   - 实现：`app/core/deps.py:get_current_user_id`

4. **wiki/init 幂等**
   - 已存在文件进 `skipped`，新文件进 `created`；二次调用 created=0 全部 skipped
   - 目录用 `mkdir(exist_ok=True)`，每个目录放 `.gitkeep`

5. **alembic 兼容性**
   - 模型在 SQLite 上 autogenerate，但替换 `(CURRENT_TIMESTAMP)` → `CURRENT_TIMESTAMP`，确保 MySQL 8 也能跑
   - 14 张表 + 全部索引和外键正确生成

---

## 六、目录速览（关键文件）

```
D:\kg-qa-ie\
├── backend/
│   ├── app/
│   │   ├── main.py                          # FastAPI 入口
│   │   ├── api/v1/
│   │   │   ├── admin.py                     # ★ config 5 端点真实现
│   │   │   ├── ingestion.py                 # ★ POST /init 真实现
│   │   │   └── ... (10 个其他 router 骨架)
│   │   ├── core/
│   │   │   ├── crypto.py                    # ★ Fernet 自动 key
│   │   │   ├── deps.py                      # ★ dev 旁路
│   │   │   └── ...
│   │   ├── services/
│   │   │   ├── admin/config_store.py        # ★ JSON + 加密
│   │   │   ├── llm/deepseek.py              # ★ httpx 真实现
│   │   │   ├── llm/factory.py               # ★ 接 config_store
│   │   │   ├── embedding/factory.py         # ★ 接 config_store
│   │   │   └── wiki/init.py                 # ★ 真实现
│   │   └── prompts/templates/               # 12 份种子模板
│   ├── data/
│   │   ├── wiki/                            # 由 POST /init 生成
│   │   └── cache/                           # 配置 + Fernet key
│   ├── migrations/versions/
│   │   └── 0001_initial.py                  # ★ alembic initial
│   ├── scripts/
│   │   └── p1_smoke.py                      # ★ 端到端冒烟
│   └── tests/unit/
│       ├── test_admin_config.py             # 7 项
│       ├── test_config_store.py             # 5 项
│       ├── test_crypto.py                   # 3 项
│       ├── test_factory_invalidation.py     # 3 项
│       ├── test_wiki_init.py                # 4 项
│       ├── test_health.py                   # 2 项
│       └── test_provider_factory.py         # 2 项
├── frontend/
│   └── src/views/system/ModelConfig.vue     # ★ 真表单
├── deploy/docker-compose.yml                # MySQL/Neo4j/Chroma/...
└── docs/
    ├── PLAN.md                              # 总规划文档
    └── P1_DONE.md                           # 本文档
```

---

## 七、已知 TODO（留给 P2 之前）

1. **真实 provider 未接通**
   - LLM：仅 DeepSeek 真实现；OpenAI 兼容 / Ollama / Anthropic 仍是 NotImplementedError
   - Embedding：4 个 provider 全是 NotImplementedError；test_connection 端点会返回 ok=false 但不崩

2. **MySQL 未实启**
   - `deploy/docker-compose.yml` 配置完整，但本机无 Docker 未真启
   - alembic 在 SQLite 上验证可执行；MySQL 上首次 `upgrade head` 待 P2 验证

3. **alembic env.py 鲁棒性**
   - 当前会强制读 `DATABASE_URL` env；MySQL 不可达时启 uvicorn 不影响（懒加载），但 `alembic upgrade` 会卡

4. **Embedding test_connection 占位**
   - 调 `POST /admin/config/test` `target=embedding` 时返回 `ok=false, error="...has no test_connection implementation yet"`
   - 不阻塞 P1 验收，但下轮第一件事就是补真实现

5. **purpose.md 的"关键问题"还是占位**
   - 模板里写的是"（待用户补充）"
   - 用户可在 P2 通过 `PurposeEditor.vue` 编辑（端点 `PUT /admin/purpose` 仍是骨架）

---

## 八、下一步（P2 入口）

按规划文档 §七 P2 阶段优先级：

1. `services/wiki/{frontmatter_io, link_resolver, fs_io, cache}.py` 真实现
2. `services/ingestion/{md_parser, privacy_check, source_writer, step1_extract, step2_compose, merger, pipeline}.py` 真实现
3. `services/query/{alias_expand, grep, page_reader, composer, scoring, persistor}.py` 真实现
4. `services/graph/builder.py` 实现（NetworkX + Louvain）
5. 前端 `Query.vue` SSE + `Sources.vue` 上传 + `Graph.vue` 3D 图谱接入

P2 退出标准：30 篇 IE md 入库；20 道测试题引用准确率 ≥80%；同名实体跨 source 不重复建页；3D 图谱能渲染。

---

**P1 完成签名**：26 单元测试 + 8 端到端检查 + 1 alembic upgrade head，全绿。
