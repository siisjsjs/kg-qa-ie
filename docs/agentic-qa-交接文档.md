# Agentic QA 改造 — AI 对接交接文档

> 写给下一个接手的 AI/开发者。截至 2026-08-14 21:15,改造计划 Phase 1–9 全部完成并在本地验证通过,**唯一未做的是 Phase 10 生产部署**。
>
> 必读配套文件:
> - 改造计划(本次工作的需求源头):`D:\kg-qa-ie\docs\agentic-qa-改造计划.md`
> - 部署对接信息(服务器/域名/凭据/增量部署方法,**含生产密码,不在仓库里**):`C:\Users\lenovo\Desktop\部署对接信息.md`

## 一、项目是什么

两个关联项目,本地源码是唯一数据源:

| 项目 | 本地路径 | 说明 |
|------|---------|------|
| 知识库门户 kg-qa-ie | `D:\kg-qa-ie` | FastAPI + Vue3,本次改造主体 |
| 智绘图平台 zhihuitu | `C:\Users\lenovo\Desktop\zhihuitu` | 绘图平台,本次只改了 SSO redirect 和模板缩略图接口 |

本次改造把 kg-qa-ie 的问答从「固定 RAG 管线」升级为 **Agentic RAG**:LLM 原生 function calling 驱动的 ReAct 循环 + chunk 级检索 + 工具箱(联网搜索/图片生成/图表模板推荐)。

## 二、已完成内容(Phase 1–9)

### 后端(`D:\kg-qa-ie\backend`)

| 模块 | 文件 | 要点 |
|------|------|------|
| LLM function calling | `app/services/llm/base.py`, `deepseek.py`, `openai_compat.py` | `ToolCall` 结构、流式工具调用累积(`ToolCallAccumulator`)、SSE 解析 |
| Chunk 切分 | `app/services/query/chunker.py` | wiki 页 → 章节级 chunk,带 heading/行号 |
| Chunk 向量索引 | `app/services/query/vector_index.py`, `app/services/embedding/vector_store.py` | Chroma collection 按 chunk 存,`page_content_hash` 增量同步;本地已建好(927 页 → 12,946 chunks) |
| Chunk 级混合检索 | `app/services/query/hybrid.py` | 词法 grep + 语义检索都在 chunk 粒度,分数融合 |
| 工具箱 | `app/services/tools/`(`base.py` 熔断器、`registry.py`、`search_kb.py`、`web_search.py`(Tavily)、`imagegen.py`(OpenAI 兼容生图,异步落盘)、`chart_templates.py`(调 zhihuitu API 推荐模板)) | 三层降级:配置缺失→不注册;运行时错→熔断;调用失败→正文照常 |
| Agent 编排 | `app/services/query/agent.py` | ReAct 循环,最多 2 轮工具,内容缓冲/replace_answer 策略 |
| 证据池 | `app/services/query/evidence_pool.py` | KB chunk 编号 E1..En,web 结果 W1..Wn |
| 引用协议校验 | `app/services/query/validation.py` | `[[slug#En]]` / `[[web#Wn]]` / `[[slug]]`;编号反查、slug 纠错、降级、删除;裸 `[[E4]]` 自动补全 slug;剥引用外的反引号 |
| 主链路重写 | `app/api/v1/query.py` | SSE 事件序列见文件头注释;done 后等 pending 图片(320s);图片完成后**回写落库消息 meta**(`_attach_image_meta_writeback`) |
| 系统提示词 | `app/prompts/agent_system.md` | 引用协议、域外问题 200 字限制、编造概念零引用、禁止反引号包引用 |
| 管理配置 | `app/api/v1/admin.py` | `/admin/config/{tavily,imagegen,zhihuitu}` GET/PUT + smoke test 端点 |
| 图片文件服务 | `app/api/v1/files.py` | `/api/v1/files/genimg/{id}.png`,防路径穿越 |
| 评分 | `app/services/query/scoring.py` | chunk 引用感知的 broken_links;概念桶(alias group)口径的 question_coverage;引用 token 剥离后再算覆盖 |

### 前端(`D:\kg-qa-ie\frontend`)

| 文件 | 要点 |
|------|------|
| `src/components/MarkdownViewer.vue` | `[[slug#En]]`→`[N]` 角标(点击跳 wiki 带 `hl`/`heading` 高亮定位);`[[web#Wn]]`→绿色外链角标;`{{image:id}}`→生成中骨架/图片/失败态 |
| `src/utils/citations.js` | token 解析、引用归一化、脚注剥离、反引号包引用兜底(`unwrapCodeCitations`) |
| `src/views/Query.vue` | 工具执行状态条、流式阶段提示、KB/网络来源分列、蓝色模板推荐提示条、图片点击放大 |
| `src/components/SidePanel.vue` | 右侧面板:模板卡片列表(缩略图+跳转绘图编辑器)/ 图片大图预览 |
| `src/stores/conversations.js` | SSE 新事件处理(tool_start/tool_result/image_*/chart_templates/web_citations),历史消息 meta 富媒体还原 |
| `src/views/system/ModelConfig.vue` | Tavily / 生图 / 智绘图三块配置 UI + 连通性测试按钮 |
| `src/utils/sso.js` | 隐藏表单 POST 跳智绘图 SSO(支持 redirect 到指定编辑器) |
| `vite.config.js` | 代理显式指向 `127.0.0.1:8000`(防 localhost 解析到 ::1 被其他进程劫持) |

### zhihuitu 侧改动(`C:\Users\lenovo\Desktop\zhihuitu\backend`)

- `app/api/v1/auth.py`:SSO 端点支持 `redirect` 参数(白名单校验 + 长度限制)
- `app/api/v1/templates.py`:新增 `GET /api/v1/templates/{id}/preview` 公开缩略图接口(防路径穿越)

**注意:这两处改动还没部署到生产**(YOUR_SERVER_IP),所以目前 kgqa 里模板缩略图会 404(前端已做降级卡片),SSO 的 redirect 参数会被生产端忽略(仍落到 /templates,不报错)。

### 测试

- 单测+集成:`cd D:\kg-qa-ie\backend && python -m pytest` → 482+ passed;有 **7 个预先存在的失败**(`tests/unit/test_admin_wiki_auth.py` 等,schema 字段不匹配,与本次改造无关,在干净 HEAD 上同样失败)
- QA 对抗回归(**调真实 LLM,花钱,勿随意跑**):`python -m pytest tests/regression -q`,14 题基线已过;产物在 `tests/regression/_artifacts/`
- 前端:`cd D:\kg-qa-ie\frontend && pnpm build` 通过

## 三、本地环境现状(截至交接时刻)

### 运行中的服务

| 服务 | 位置 | 说明 |
|------|------|------|
| kgqa 后端 | 原生进程,`127.0.0.1:8000` | 启动命令:`cd D:\kg-qa-ie\backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`;启动要 1–3 分钟(alembic + 缓存预热),`/health` 通了才算好 |
| kgqa 前端 | vite dev,`http://localhost:5180` | `cd D:\kg-qa-ie\frontend && pnpm dev` |
| 基础设施 | docker compose 项目 `deploy`(`D:\kg-qa-ie\deploy\docker-compose.yml`) | deploy-mysql-1(:3309)、deploy-neo4j-1(:7476/:7689)、deploy-chroma-1(:8001,**版本 0.5.15 强约束**) |

本机 docker 里还有其他项目的容器(kg-*、kg2-*、knowledge-graph-master、zhihuitu 本地套件),**与 kgqa 无关,别动**。本地 `zhihuitu-frontend-1` 一直在重启是历史遗留(找不到本地 zhihuitu 后端),不影响任何事。

### 已配好的密钥(存在本地 MySQL 配置表,管理后台可见)

- `llm.*`:DeepSeek(openai_compat)
- `embedding.*`:硅基流动 BAAI/bge-m3
- `tavily.api_key`:已配,**但本机连不通 api.tavily.com(网络问题),smoke test 失败**;云服务器上待验证
- `imagegen.*`:中转站 `https://www.mhcoding.xyz`,gpt-image-2,smoke test 通过;**出图慢(1–3 分钟)且不稳是服务商侧限制**
- `zhihuitu.base_url`:`https://draw.example.com`(生产)

## 四、今天踩过并修掉的坑(勿复发)

1. **8000 端口被无关进程劫持**:桌面某 demo 的 `python -m http.server 8000` 与后端并存,localhost 解析到 ::1 时请求打到它 → 全部 404(配置接口、图片文件)。已杀进程,且 vite 代理已改为显式 `127.0.0.1`。若再见"接口 404 但后端明明在跑",先 `netstat -ano | findstr :8000` 查有没有多个监听。
2. **生图超时**:gpt-image-2 出图常超 2 分钟,原 120s 读超时不够。已改 300s + 超时重试一次(`imagegen.py`);SSE done 后等待窗口 60s→320s;**图片完成后回写落库消息 meta**(否则历史会话永远显示"生成中")。
3. **模型把引用包进反引号**:`` `[[slug#E3]]` `` 渲染成原始文本。已三层修复:后端清洗剥反引号、前端渲染兜底(老消息也能修)、提示词明令禁止。
4. **裸编号引用 `[[E4]]`**:模型偷懒漏写 slug,原逻辑当页面引用误删导致段落引用覆盖率归零。已在 `validation.py` 支持从证据池反查补全。
5. **启动日志停在 alembic 后很久**:正常现象(缓存预热),用 `/health` 判活,别急着杀进程。

## 四点五、绘图工具账号同步机制(2026-08-14 定稿,勿回退成服务账号)

用户明确要求:门户(kgqa)是唯一用户源,与智绘图账号天然同步,**管理员不需要在模型配置里配任何绘图账号**。实现:

- 智绘图后端新增 `POST /api/v1/auth/sso-token`(auth.py,与浏览器 `/sso` 共用 find-or-create 逻辑):收 kgqa 用户 token → 调 kgqa `/auth/me` 校验 → 返回该用户在智绘图的 JWT(JSON)。
- kgqa `query.py` 把请求的 bearer token 放进工具 ctx(`kgqa_token`);`chart_generate.py` 用它换智绘图 token(30 分钟缓存,401 强制重换),以**提问用户本人身份**:①`POST /charts/generate` 服务端出图 ②`POST /charts` 把图表存进该用户的「我的图表」(chartData={params, image_base64})。
- `image_ready` 事件的 `chart.editor_path` 优先 `/chart-editor/{chart_id}`(直达已保存图表继续编辑,SSO redirect 支持),保存失败降级 `/mode-select/{templateId}`。
- 管理后台"绘图平台"只配 `base_url`;"测试连通"用当前管理员自己的 token 验证账号同步链路。
- 缩略图:智绘图自己前端是构建时打包的哈希资源,外部不可引用;kgqa 用的稳定端点 `GET /api/v1/templates/{id}/preview` **已部署生产**,推荐卡缩略图直接可显示。
- 开发环境:本地 kgqa + 生产智绘图也能通(两边 kgqa JWT secret 相同且用户在生产存在);标准做法是两个项目同时本地起,本地智绘图 `KGQA_BASE_URL` 指向本地 kgqa。

## 四点六、全部 38 个模板均可服务端出图(2026-08-14 补齐)

用户反馈"人机作业图画不出来、感觉没把所有图做完":原因是智绘图 38 个模板里只有 29 个 matplotlib 引擎的有 generator.py,5 个 vue-component(man-machine/joint-operation/two-hand/therblig/process-flow)和 4 个 drawio(gantt/operation-process/tech-roadmap/value-stream)没有服务端生成器。已补齐:

- 智绘图 `/charts/generate` 按 generator.py 是否存在调度,与 engine 无关 → 给这 9 个模板各写了 matplotlib generator.py + meta.json 补 `paramSchema`(与 example.data 形状一致)。渲染验收图在 `C:\Users\lenovo\Desktop\zhihuitu\_render_check\`。
- **params 形状 = Vue 组件数据形状**:vue-component 模板保存「我的图表」时 chartData 直接存 params,编辑器原生打开可继续编辑;matplotlib 存 `{params, image_base64}`;drawio 编辑器只认 xml,不保存记录(编辑按钮降级 /mode-select 新建页)。逻辑在 kgqa `chart_generate.py:_chart_data_for_engine`。
- kgqa 判定"可服务端出图"的标准从 engine==matplotlib 改为**模板带 paramSchema**(chart_templates.py)。
- 生产部署方式:`/opt/zhihuitu/chart-templates` 是宿主机 bind mount(compose `./chart-templates:/app/chart-templates:ro`),**传文件到宿主机目录即生效,不需要 docker cp/commit**;改 meta.json 后要 `docker compose restart backend` 触发模板同步(BACKEND_SYNC_TEMPLATES_ON_STARTUP=true)。已部署并端到端验证(人机作业图问答 → 出图 → 存「我的图表」chart 221)。

## 五、待办:Phase 10 生产部署(需用户确认后执行)

**严格按 `部署对接信息.md` 的增量方式,绝不 `docker compose build` 全量重建**(踩过多次,build cache 丢失会卡死十几分钟)。

部署清单(按顺序):

1. **kgqa 后端**(YOUR_SERVER_IP,`/root/kgqa`):上传改动的后端文件 → `docker commit kgqa-backend-1` 快照 → Dockerfile.incr 增量 COPY → `compose up -d --no-build backend`。注意:
   - 有新增文件(整个 `app/services/tools/`、`app/services/query/` 多个新模块、`app/api/v1/files.py`、`app/prompts/`、alembic 迁移),建议整目录上传而不是逐文件
   - 上线后要在服务器跑一次 chunk 向量索引全量构建(`backend/scripts/rebuild_query_chunk_index.py`,约 18 分钟、调 embedding API 花钱),或直接把本地 `backend/data/cache/query_chunk_index.json` + Chroma 数据迁移过去
   - 服务器 chroma 是 **0.5.15**,客户端版本必须匹配
2. **kgqa 前端**:本地 `pnpm build` → dist 打包上传 → `docker cp` 进 `kgqa-frontend-1:/usr/share/nginx/html/` → 清旧 assets → `docker commit`。无 CDN,强刷即可验证。
3. ~~**zhihuitu 后端**~~:**已于 2026-08-14 部署完成**(`auth.py` 含 `/auth/sso-token`、`templates.py` 含缩略图端点,commit 快照 + Dockerfile.incr 增量)。生产已验证:`GET /api/v1/templates/pareto/preview` 200、`POST /auth/sso-token` 无效 token 401、真实 token 换 token 出图存记录全链路通。
4. **zhihuitu 前端**:本次没改,跳过(若后续改了记得清腾讯云 EdgeOne CDN 缓存)。
5. **生产配置**:登录 kgqa 管理后台(admin)→ 模型配置,填 Tavily / 生图 / 智绘图三块并跑 smoke test(密钥在用户手里/本地配置表)。重点验证 Tavily 在服务器上是否连通。
6. **回归验证**:生产上人工过一遍:问答引用角标跳转、联网搜索、生图、模板推荐 + SSO 跳编辑器。

## 六、常用命令速查

```bash
# 后端测试(不花钱)
cd D:\kg-qa-ie\backend && python -m pytest -q

# 回归测试(调真实 LLM,花钱,需用户同意)
cd D:\kg-qa-ie\backend && python -m pytest tests/regression -q

# 前端构建
cd D:\kg-qa-ie\frontend && pnpm build

# 重建 chunk 向量索引(调 embedding API,花钱,约 18 分钟)
cd D:\kg-qa-ie\backend && python scripts/rebuild_query_chunk_index.py

# 本地基础设施
cd D:\kg-qa-ie\deploy && docker compose up -d mysql neo4j chroma
```

## 七、行为约定(用户明确要求过)

- 严格按 `agentic-qa-改造计划.md` 执行,有需要确认的事**必须先通知用户**
- 生产部署、花钱操作(LLM 回归/embedding 重建)动手前先问
- Windows 环境:终端是 PowerShell;调远程服务器用 `C:\Users\lenovo\.claude\ssh-tools\` 里的 paramiko 脚本(用 `py` 不是 `python`)
