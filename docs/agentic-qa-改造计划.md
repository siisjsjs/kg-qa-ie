# 智能问答 Agentic RAG 改造计划（任务交接文档）

> 交接对象：负责实施的 AI/开发者。本文档自包含全部背景、已确认决策、现状代码坐标、分阶段实施细节、验收标准与部署流程。
> 生成日期：2026-08-14。
> 服务器 IP / 账号密码 / 部署增量构建的具体命令 **不在本文档**，见本机 `C:\Users\lenovo\Desktop\部署对接信息.md`（含生产凭据，勿提交仓库）。

---

## 0. 背景与目标

本项目 `D:\kg-qa-ie`（知识库门户 kgqa，FastAPI + Vue3 + MySQL/Neo4j/Chroma）现有智能问答是固定流水线：
意图路由 LLM → 6 选 1 prompt 模板 → 整页级检索 + 读页拼 context → 流式生成 → 页面级 `[[slug]]` 引用。

**用户需求（已确认）：**

1. 提高回答质量：接入 Tavily 联网搜索、OpenAI 兼容图片生成接口、调用另一平台"智绘图"（zhihuitu，`C:\Users\lenovo\Desktop\zhihuitu`）的图表模板做推荐展示
2. 引用可点击查看**具体引用了哪几句话**（句子级引用 + 目标页高亮），方便学生学习
3. 废除 6 套固定 prompt，改为 Agentic RAG（模型自主决策），实现方式 = **原生 function calling 的 ReAct 循环**（不引入 LangChain 等框架）
4. 废除"读页拼 context"（整页粒度不准），改 chunk 级检索
5. **速度不能明显变慢**（核心约束）
6. 模板推荐 UI：回答中出现**蓝色提示条**，点击在**右侧面板**展示（参考 Cursor/Trae 分栏排版），面板内可跳转智绘图编辑器
7. 降级硬要求：Tavily/画图接口未配置或额度耗尽时**不暴露该工具**，运行失败不进死循环（三层防护，见 §5.4）

**用户已拍板的决策：**

- Tavily：用户有 key（实施时找用户要，填入管理后台即可，不阻塞开发）
- 图片生成：走 **OpenAI 兼容接口**（base_url / model / api_key 全部做成后台可配，用户稍后提供具体值）
- zhihuitu SSO 接口**同意修改**，支持 redirect 参数直达具体模板页
- 新链路**直接替换**旧链路，不留配置开关

---

## 1. 现状代码坐标（已核实）

### 1.1 后端（D:\kg-qa-ie\backend）

| 文件 | 现状 | 改造动作 |
|---|---|---|
| `app/api/v1/query.py` | SSE 编排 `_stream_answer`（L121-531）：并行 route/rewrite/prefetch → 三分支（chitchat/out_of_domain/kb）→ compose → 校验/补充 → citations/score/persist/followups | **重写主链路**，保留会话/落库/评分/persist/followups 骨架 |
| `app/services/llm/base.py` | `ChatMessage` 只有 role∈{system,user,assistant}+content（L26-29）；`chat()` 返回 `str \| AsyncIterator[str]` | 扩展 tool 消息 + 新增 `chat_tools()` 结构化流式接口 |
| `app/services/llm/deepseek.py` | `_stream` 只 yield `delta.content`（L121-138），丢弃 tool_calls | 实现 tools 传参 + 流式 tool_calls 增量解析 |
| `app/services/llm/openai_compat.py` | 同上（L147-186），另有 `_normalize_base_url`、重试逻辑 | 同上（两文件逻辑同构，可抽共享函数） |
| `app/services/llm/factory.py` | config_store > settings 兜底，签名缓存 | 不动 |
| `app/services/query/vector_index.py` | 整页一条向量：`_index_document`（L28-45）只嵌 title+aliases+headings+body[:2400]，collection=`wiki_query_pages`，content_hash 增量，15min freshness watcher | 改为 chunk 级，新 collection `wiki_query_chunks` |
| `app/services/embedding/vector_store.py` | `WikiVectorStore`：ids=slug，search_similar 不返回 document 原文 | 扩展支持 chunk id、返回 documents |
| `app/services/query/hybrid.py` | 页级 RRF 融合 + 确定性 rerank（`fuse_and_rerank` L124-179），语义失败降级纯 grep | 重构为 chunk 级 |
| `app/services/query/grep.py` | 行级命中：`hit_segments` 带 `line`/`text`/上下文（L167-179），页面缓存 TTL 300s | 保留，命中行映射到 chunk |
| `app/services/query/page_reader.py` | 读页拼 context（预算 8000/页、28000 总） | **废弃删除** |
| `app/services/query/evidence.py` | 方面规划/二次检索/`validate_and_clean_answer`（L328-356，无效引用回磁盘找回+删除）/`assess_answer_coverage` | 方面规划与补充生成废弃；`validate_and_clean_answer` 思路保留但适配 chunk 引用协议 |
| `app/services/query/composer.py` | 6 模板选择（L75-82）+ `CITATION_RULES`/`CITATION_FOOTER` + `extract_citations`（L199-208）+ `split_citation_footer` | 模板机制废弃；`extract_citations`/`split_citation_footer` 适配新协议后保留 |
| `app/services/query/router.py` | 快路径正则 chitchat（L54-74）+ LLM 分类（L177-217） | **保留正则快路径，删除 LLM 分类调用**（省 1-2s） |
| `app/services/query/rewriter.py` | 多轮指代改写（仅含代词时调 LLM），供检索用 | 保留 |
| `app/services/query/citations.py` | `resolve_citation`/`build_citation_details`，页级 | 扩展 chunk 级 + web 来源 |
| `app/services/query/scoring.py` `persistor.py` `followups.py` `inflight.py` `alias_expand.py` `chitchat.py` | 评分/落盘/追问/去重/别名/闲聊 | 基本保留，接口适配 |
| `app/services/admin/config_store.py` | JSON+Fernet（`{cache_dir}/system_config.json`），get_sync/set_many 等 | 不动，新增 key |
| `app/api/v1/admin.py` | `GET/PUT /admin/config/llm\|embedding\|firecrawl` + test | 新增 `tavily`、`imagegen` 配置端点 |
| `backend/scripts/rebuild_query_vector_index.py` | 手动重建页级索引 | 新增 chunk 版重建脚本 |

**Prompt 文件**（`app/prompts/`）：`router.md`、`query_general.md`、`query_ie_method.md`、`query_ie_concept.md`、`query_comparison.md`、`query_timeline.md`、`query_deep_dive.md`、`query_out_of_domain.md` 全部废弃；`chitchat.md`、`followup_suggestions.md` 保留；新增 `agent_system.md`。

**Wiki 数据**：`{wiki_root}/wiki/{entities|topics|sources}/{slug}.md`（约 939 可检索页：entities 753 / topics 156 / sources 30）；`queries/comparisons/synthesis` 为生成页不参与检索（`grep._GENERATED_SUBDIRS`）。frontmatter 解析用 `app/services/wiki/frontmatter_io.py: parse_text`。

### 1.2 前端（D:\kg-qa-ie\frontend）

| 文件 | 现状 | 改造动作 |
|---|---|---|
| `src/api/index.js` | `streamQuery` fetch SSE，事件 switch（L136-160），未知事件静默丢弃 | 新增 tool/image/template/web 事件分发 |
| `src/stores/conversations.js` | `sendMessage`（L316-344）管理流；AbortController；离开页面不断流 | 消息模型加 `toolEvents[]`/`images[]`/`chartTemplates[]`/`webCitations[]` |
| `src/views/Query.vue` | assistant 渲染（约 L268-336）：MarkdownViewer + quality + 引用列表 + 追问 chips | 状态条、蓝色模板提示条、**右侧分栏面板**、网络来源区、图片卡 |
| `src/components/MarkdownViewer.vue` | citation-mode：`[[slug]]`→上标 [N]，点击 `router.push(/kb/wiki/slug)`（L299-310），无锚点 | 支持 `[[slug#En]]`，点击带高亮参数 |
| `src/views/WikiViewer.vue` | 无 hash/滚动/高亮逻辑 | 新增引用文本高亮 + 滚动定位 |
| `src/utils/citations.js` | 页级 slug 规范化/剥脚注 | 适配 chunk 协议 |

### 1.3 智绘图平台（C:\Users\lenovo\Desktop\zhihuitu，部署在另一台服务器）

- `GET /api/v1/templates`、`GET /api/v1/templates/{id}`：**公开无需 token**（`backend/app/api/v1/templates.py`），返回 id/name/engine/category/description/tags/config；`thumbnail` 恒 null
- 缩略图在 zhihuitu 仓库 `chart-templates/<id>/preview.png`（约 38 张），**后端不暴露**，需拷贝到 kgqa 前端静态资源
- 三种引擎：`matplotlib`（服务端出图需登录）/ `vue-component` / `drawio`（前端画布）；跨服务只做**推荐 + 跳转**，不做远程出图
- 前端路由：`/templates` → `/mode-select/:templateId` → `/form/:id` 或 `/ai-chat/:id`
- SSO：kgqa 门户 `Portal.vue: openDrawingPlatform()` 动态建隐藏 form POST token 到 `https://draw.example.com/api/v1/auth/sso`；zhihuitu `backend/app/api/v1/auth.py` 校验后返回 HTML 写 localStorage 并 `location.replace('/templates')`（**固定落地页，需改**）

---

## 2. 目标架构

```
用户提问 (POST /api/v1/query, SSE)
  ├─ 正则快路径命中闲聊 → chitchat 流式秒回（不变）
  └─ 主路径:
      并行: [指代改写(有代词才调LLM)] [chunk级混合检索投机预取]
        ↓
      Agent 循环 (最多 2 轮工具, 原生 function calling):
        round 1: system(统一prompt) + history + user(问题+预取证据chunks)
          → 模型直接流式作答(多数情况,零额外往返)
          → 或 tool_calls: search_kb / web_search / generate_image / recommend_chart_templates
             (并行执行,单工具超时,失败降级,见§5.4)
        round 2: 工具结果回填 → 流式作答(强制,不再给工具)
        ↓
      后处理: 引用校验清洗 → citations(chunk级+web) → score → persist → followups → 落库
```

**速度账本**：删 router LLM 调用省 1-2s；context 从 ≤28000 字降到 6000-10000 字（首字更快）；简单问题 1 轮直答与现在等速或更快；联网/补检索 +3-8s（有状态条）；图片生成完全异步不阻塞文字。

**明确保留清单（用户特别提醒，不许删）：**

- **`.wiki-schema.md` 相关的全部检索能力**：`app/services/ingestion/merger.py: load_alias_groups()` 读 schema 别名组 + `app/services/query/alias_expand.py: expand()` 做同义词扩展——**投机预取和 `search_kb` 工具内部都必须走别名扩展**（工具收到 query 后先 expand 再 hybrid_search，与预取同一条路径）；`query.py: _detect_domain_ie()` 读 schema 的 `domain_ie` 配置也保留（下游 maybe_persist / citations 事件用）
- 闲聊正则快路径、多轮指代改写 rewriter、inflight 检索去重、评分/gap-queue/persist/followups/自动摘要

---

## 3. Phase 1：LLM 层 function calling 支持

**文件**：`app/services/llm/base.py`、`deepseek.py`、`openai_compat.py`

1. `ChatMessage` 扩展：

```python
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON 字符串（与 OpenAI 协议一致）

@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCall] | None = None   # assistant 消息携带
    tool_call_id: str | None = None            # tool 消息携带
```

序列化进 payload 时：assistant 带 tool_calls 输出 OpenAI 格式 `{"role":"assistant","content":...,"tool_calls":[{"id","type":"function","function":{"name","arguments"}}]}`；tool 消息输出 `{"role":"tool","tool_call_id","content"}`。**注意**：现有 `_build_payload` 的 `[{"role": m.role, "content": m.content} for m in messages]` 两个文件都要改，抽一个共享的 `messages_to_payload()` 放 base.py。

2. 基类新增抽象方法 `chat_tools()`（只做流式，非流式没有使用场景）：

```python
async def chat_tools(
    self, messages: list[ChatMessage], *, tools: list[dict], **params,
) -> AsyncIterator[dict]:
    """结构化流式事件:
      {"type": "token", "text": str}                        # 正文增量
      {"type": "tool_calls", "calls": [ToolCall, ...]}      # 完整拼装后一次性给出
      {"type": "finish", "finish_reason": str|None}
    """
```

3. deepseek/openai_compat 实现（逻辑同构，抽共享解析函数）：
   - payload 带 `tools`（OpenAI schema）+ `tool_choice: "auto"`
   - 流式解析 `delta.tool_calls`：按 `index` 增量拼 `id`/`function.name`/`function.arguments` 片段，`finish_reason=="tool_calls"` 或流结束时组装完整 ToolCall 列表 yield
   - `delta.content` 照常 yield token（模型可能先说几句话再调工具）
   - anthropic/ollama 两个 provider 本来就是 `NotImplementedError`，`chat_tools` 同样抛即可
   - 保留 `stats` 回填 usage/finish_reason（复用 `_record_finish`）
   - DeepSeek 注意：`extra_params` 默认有 `reasoning_effort: "none"`（factory L42-45），tools 请求保持不变

**单测**（新增 `backend/tests/test_llm_tools.py`）：用假的 SSE 行序列喂解析函数，验证多工具并行调用的 arguments 增量拼装、纯文本回答不产生 tool_calls 事件。

---

## 4. Phase 2/3：chunk 级索引与混合检索

### 4.1 切块器（新文件 `app/services/query/chunker.py`）

```python
def chunk_page(path: Path) -> list[dict]:
    """返回 [{chunk_id, slug, title, kind, heading, text, line_start, line_end, content_hash}]"""
```

规则：
- 按 `## ` 二级标题切段；无标题的开头部分算"引言"块
- 超过 ~700 字的章节按空行段落再切，目标每块 300-600 字，块间不重叠（wiki 页面结构规整，无需 overlap）
- `chunk_id = f"{slug}::{序号:02d}"`（序号按文中顺序，Chroma id 用它）
- `line_start/line_end` 是**正文 body 的行号**（1-based，与 grep 的行号同一坐标系——grep 用 `body.splitlines()` 从 1 计数，务必对齐）
- 嵌入文本 = `f"{title} · {heading}\n{text}"`（带页面标题上下文，提升检索质量）
- frontmatter 的 aliases 并入第一个 chunk 的嵌入文本

### 4.2 vector_index.py 改造

- `COLLECTION_NAME = "wiki_query_chunks"`（老 `wiki_query_pages` collection 不删，验证后手动清理）
- marker 文件换名 `query_chunk_index.json`，增加 `chunk_count` 字段
- `sync_query_vector_index`：按**页级 content_hash**（全部 chunk hash 拼接再 hash）判断页面是否变化；变了就删该页旧 chunk（`where={"slug": slug}`）再插新 chunk。增量逻辑、freshness watcher（60s 首查 / 900s 周期）、`_SYNC_LOCK`、失效回调（`invalidate_page_cache`/`invalidate_vector_index_state`）全部保留
- `WikiVectorStore`：ids 用 chunk_id；metadata 增加 `heading`/`line_start`/`line_end`/`chunk_index`；`search_similar` 的 `include` 加 `"documents"`，返回项带 `text` 原文
- 调用方需同步改：`app/api/v1/wiki.py` L1061 附近（编辑后重建）、`app/services/ingestion/pipeline.py` L1612 附近（ingest 收尾）、`backend/scripts/` 新增 `rebuild_query_chunk_index.py`

### 4.3 hybrid.py 重构为 chunk 级

- 语义腿：直接返回 chunk 命中（含 text/行号），阈值仍 0.35
- 词法腿：`grep_wiki` 返回不动；每个页面的 `hit_segments`（带行号）按行号落到所属 chunk → 页面 grep 得分 × 命中集中度分摊给对应 chunk
- 融合：chunk 级 RRF（k=60）+ 确定性 rerank（权重沿用 0.35 lex + 0.35 sem + 0.2 title_overlap + 0.1 hits）
- 约束：**每页最多 3 个 chunk** 进最终结果（防单页霸榜）；默认返回 top 10 chunk（原页级 top 5）
- 返回结构：`[{chunk_id, slug, title, kind, heading, text, line_start, line_end, score, retrieval_sources}]`
- `retrievable_slugs` 陈旧行过滤逻辑保留（按 slug 判断）

---

## 5. Phase 4：工具箱（新目录 `app/services/tools/`）

统一工具接口：

```python
class Tool:
    name: str
    def spec(self) -> dict: ...            # OpenAI function schema
    def available(self) -> bool: ...       # 配置齐全且未熔断
    async def run(self, args: dict, ctx: dict) -> dict: ...  # 返回给模型的结果 + 给前端的事件数据
```

### 5.1 `search_kb`

包装 §4.3 的 chunk 级 hybrid_search。参数 `{query: str}`。返回新证据 chunk（编号接续 E 序列），并入证据池。超时 5s。

### 5.2 `web_search`（Tavily）

- `POST https://api.tavily.com/search`，body `{"api_key", "query", "max_results": 5, "search_depth": "basic", "include_answer": false}`，httpx 直调，**不装 SDK**
- 配置 key：`tavily.api_key`（加密存 config_store）
- 返回给模型：编号 `[W1]..[W5]` 的 `{title, url, content 摘要}`；同时缓存映射供引用解析
- 超时 8s

### 5.3 `generate_image`（OpenAI 兼容，**异步不阻塞**）

- 配置：`imagegen.base_url` / `imagegen.api_key`（加密）/ `imagegen.model` / `imagegen.size`（默认 1024x1024）
- `POST {base}/v1/images/generations`，body `{model, prompt, size, n:1, response_format:"b64_json"}`；兼容返回 url 的服务（两种都处理）
- **关键设计**：工具 run() **立即返回** `{"image_id": "img_xxx", "status": "pending"}` 给模型（prompt 里告知模型：图片已开始生成，在正文中用占位标记 `{{image:img_xxx}}` 标注插图位置即可，不要等待）；实际生成用 `asyncio.create_task` 后台跑
- 生成完成：图片存 `{cache_dir}/genimg/{image_id}.png`，新增只读端点 `GET /api/v1/files/genimg/{image_id}.png`（校验文件名格式防路径穿越）；SSE 推 `image_ready` 事件
- SSE 流在 `done` 后若有 pending 图片，**保持连接最多 60s** 等 `image_ready`，超时发 `warn` 后关闭；落库的 assistant meta 记录 image_id → 前端历史加载时用 URL 直接展示（文件已在磁盘）
- 生成失败：SSE 推 `image_failed`，前端占位卡显示失败态

### 5.4 `recommend_chart_templates`

- 服务端 httpx `GET {ZHIHUITU_BASE}/api/v1/templates`（公开接口），`ZHIHUITU_BASE` 配置项 `zhihuitu.base_url`（默认 `https://draw.example.com`），结果**内存缓存 10 分钟**
- 参数 `{keywords: [str]}`；按 name/description/tags/category 匹配打分取 top 3
- 返回给模型：模板 id/name/description（模型据此在正文自然提及）；同时 SSE 推 `chart_templates` 事件（含完整卡片数据）给前端
- 缩略图：**不做本地拷贝**，直接热链 zhihuitu 新增的公开缩略图接口 `GET {zhihuitu.base_url}/api/v1/templates/{id}/preview`（见 §9.2），前端加载失败时显示占位图
- 超时 5s

**自动跟随智绘图更新（用户硬性要求）**：智绘图后台上架新模板后，kgqa 侧**零代码、零部署**自动生效——模板列表来自实时 API（10 分钟缓存过期后自动拿到新模板）；匹配打分基于 API 返回的 name/description/tags 动态计算；缩略图走 §9.2 的实时接口。唯一前提是 zhihuitu 那边新模板正常走它自己的 `sync_templates` 上架流程（模板目录含 `preview.png`）。

### 5.5 降级与熔断（硬性要求，三层）

1. **配置层**：`available()` 检查配置——key 未配的工具**不进 tools 列表**，模型无从调用
2. **运行层**：`run()` 异常/超时 → 返回 `{"error": "工具暂不可用，请基于已有材料直接作答"}` 作为 tool 结果，流程继续；同一工具单次回答内失败 1 次即从后续轮次的 tools 列表移除
3. **熔断层**：HTTP 401/402/403/429 → 模块级熔断器记录 `(tool_name, until_ts)`，**10 分钟内全局不暴露该工具**；熔断触发写 loguru warning
4. 循环硬上限：最多 2 轮工具调用，第 3 轮强制 `tool_choice: "none"` 纯作答；整个 Agent 循环外层 `asyncio.timeout(120)` 兜底

---

## 6. Phase 5：Agent 编排器与引用协议

### 6.1 新文件 `app/services/query/agent.py`

核心函数：

```python
async def run_agent_stream(
    *, question: str, history, summary, evidence: EvidencePool,
    tools: list[Tool], wiki_root: Path,
) -> AsyncIterator[dict]:
    """yield 与 SSE 对齐的事件: token / tool_start / tool_result / replace_answer /
       image_pending / chart_templates / web_citations / agent_done(带最终answer与证据池)"""
```

- **EvidencePool**：管理编号——KB chunk 编 `E1..En`（跨轮次接续），web 结果编 `W1..Wn`；提供 `render_for_prompt()`（证据区块文本）与 `resolve(citation_ref)`（引用解析）
- 第一轮 user 消息 = 问题 + 预取证据区块；工具结果以 role=tool 消息回填
- 工具并行：一轮多个 tool_calls 用 `asyncio.gather`；每个工具调用前 yield `tool_start`（带模型给的调用理由，取自该轮已流出的 content 或工具参数），结束 yield `tool_result`
- **第一轮流出的 content 处理**：若该轮最终产生 tool_calls，则已流出的 content 视为"思考旁白"——不并入正文，转为 `tool_start` 的 reason 展示；若无 tool_calls 则就是正文。实现上：第一轮 content 先缓冲不 yield token，收到 finish 事件后再决定（缓冲延迟对用户不可感，因为 DeepSeek 要么直接说话要么直接调工具）

### 6.2 统一 system prompt（新文件 `app/prompts/agent_system.md`）

要点（写 prompt 时逐条落实）：
- 人设：IE 领域资深专家 + 知识库问答助手，目标读者是学生/工程师（沿用 composer.py L237-244 现有人设文案）
- 引用协议：出自证据 `[En]` 的论断句末写 `[[slug#En]]`；出自网络 `[Wn]` 的写 `[[web#Wn]]`；无对应证据的专业经验补充不标注；文末 `## 引用` 清单（沿用现有 CITATION_FOOTER 风格）
- 回答风格自适应（替代 6 模板）：概念题给定义/特征/场景，方法题给条件/步骤/注意事项，对比题给表格，时间线给阶段叙事——作为**指引**而非硬骨架
- 工具时机：证据不足或缺方面 → search_kb；时效信息/新标准/知识库明显未覆盖 → web_search；流程/布局/结构示意图能显著帮助理解 → generate_image（并说明 `{{image:id}}` 占位用法）；提到的方法在绘图平台有模板 → recommend_chart_templates
- 域外问题：坦诚说明不在知识库范围，克制作答，不编引用（吸收原 out_of_domain 模板要点）
- 空证据：开头说明未命中素材，凭专业经验作答，不硬凑数字（吸收原 EMPTY_CONTEXT_RULES）

### 6.3 引用协议与解析

- 正则扩展 `composer.CITATION_PATTERN` → 匹配 `[[slug#En]]`、`[[web#Wn]]`、`[[slug]]`（兼容降级）
- 后处理 `extract_citations` 返回结构化：`{type: "chunk"|"page"|"web", slug, ref}`
- `citations.py` 新增 `build_citation_details_v2(refs, evidence_pool, wiki_root)`：
  - chunk 引用 → `{type:"chunk", slug, title, kind, heading, line_start, line_end, quote(chunk text 前 200 字)}`
  - 页面引用 → 现有 `resolve_citation` 逻辑
  - web 引用 → `{type:"web", url, title, quote}`
- 校验清洗（替代 evidence.validate_and_clean_answer 的引用部分）：`En`/`Wn` 编号不存在于证据池 → 降级为 `[[slug]]`（slug 在库里存在时）或删除；slug 不存在 → 沿用"回磁盘找一次，找不到删"的现有策略（`_load_page_by_slug`/`_remove_invalid_citations` 可复用）
- 正文给前端渲染前把 `[[slug#En]]` 保留原样（前端解析），`{{image:id}}` 占位由前端替换成图片卡

### 6.4 query.py 重写要点

保留：`_persist_user_msg`/`_persist_assistant_msg`/`_trigger_auto_summarize`/`_load_history_for_session`/inflight 检索去重/chitchat 正则快路径（改为直接调 `router._fast_path`，**删除 classify_route 的 LLM 调用**）/评分 `evaluate_for_query`/gap_queue/`maybe_persist`/`generate_followups`/done 后发 followup 事件。

新事件序列：

```
progress(stage=rewrite)? → progress(stage=retrieval, candidates=[chunk摘要]) →
[tool_start → tool_result]* → token* → replace_answer? →
citations(chunk级+web) → chart_templates? → path → score → persist →
done → followup_suggestions? → image_ready|image_failed*(若有pending图)
```

assistant 落库 meta 新增：`tool_events`、`images: [{image_id, status, prompt}]`、`chart_templates`、`web_citations`；citations 数组每项带 type/quote/行号。

`schemas/query.py` 的 `Citation` model 增加可选字段 `type/heading/line_start/line_end/quote/url`。

删除文件：`page_reader.py`、`router.md` 及 6 个 query_*.md、`evidence.py` 中方面规划/补充生成相关函数（保留可复用的校验工具函数，挪到 agent.py 或独立 validation.py）。`composer.py` 大部分废弃，保留 `extract_citations`/`split_citation_footer`（适配新正则）。**全局 grep 确认无残留 import**（evidence.py、query.py、scoring.py、persistor.py 都 import 过 composer/page_reader 的符号）。

---

## 7. Phase 6：配置端点与管理界面

- `app/api/v1/admin.py` 仿照 firecrawl 端点新增：
  - `GET/PUT /admin/config/tavily`（key：`tavily.api_key` 加密）
  - `GET/PUT /admin/config/imagegen`（`imagegen.base_url`、`imagegen.api_key` 加密、`imagegen.model`、`imagegen.size`）
  - `GET/PUT /admin/config/zhihuitu`（`zhihuitu.base_url`）
  - `POST /admin/config/test` 扩展支持 tavily（发一个测试查询）与 imagegen（只校验 key 格式与连通性，不真生成图）
- 前端 `ModelConfig.vue`（`/kb/system/model`）新增"工具配置"区块：Tavily key、画图接口三项、绘图平台地址，带测试按钮与掩码显示（复用现有 LLM key 的掩码交互）

**实时生效（用户硬性要求，管理后台改完立即生效、不重启）**：实现上必须让工具在**每次被调用时**现读配置——`Tool.available()` 与 `run()` 内部调 `config_store.get_sync()`（该函数走内存缓存 + 写入时同步刷新，单次读开销微秒级），**禁止**在模块加载或 Agent 启动时把 key 缓存成实例属性。这样后台保存 key → 下一条提问立刻能用；清空 key → 下一条提问该工具立刻从 tools 列表消失。熔断器状态独立于配置，保存新 key 时应顺带清除该工具的熔断记录（用户可能刚充值/换 key）。

---

## 8. Phase 7：前端改造

### 8.1 SSE 分发（`src/api/index.js` streamQuery switch 新增）

`tool_start`/`tool_result` → `onToolEvent`；`image_pending`/`image_ready`/`image_failed` → `onImage`;`chart_templates` → `onChartTemplates`；`web_citations` 并入 `onCitation` 体系（citations 事件本身已带 type 字段也可）。

### 8.2 会话 store（conversations.js）

assistant 消息模型加 `toolEvents[]`、`images{}`、`chartTemplates[]`；`image_ready` 到达时替换对应占位；历史加载时从 meta 恢复这些字段。**注意**流结束后 `loadSession` 会用服务端数据覆盖本地消息，meta 里必须有完整数据。

### 8.3 Query.vue

- **工具状态条**：assistant 消息正文上方，进行中显示"🔍 正在搜索知识库… / 🌐 正在联网搜索… / 🎨 正在生成示意图…"（带模型的调用理由），完成后折叠为可展开的小字记录
- **正文 `{{image:id}}` 占位** → 图片卡组件（pending 骨架屏 / 完成显示图，点击右侧面板看大图 / 失败态）
- **蓝色模板提示条**：收到 `chart_templates` 事件后在消息底部渲染（"💡 本方法可用『XX』模板绘制，点击查看"）
- **右侧面板**（新组件 `SidePanel.vue`，参考 Cursor/Trae 分栏）：点击提示条 → 页面变左右分栏（对话区收窄），右侧展示模板卡：缩略图大图（src = `{zhihuitu.base_url}/api/v1/templates/{id}/preview`，onerror 换占位图；base_url 由 `chart_templates` SSE 事件携带，前端不硬编码）、名称、描述、分类、"前往绘图编辑器"按钮；可关闭恢复全宽；图片大图预览复用同一面板
- **前往编辑器**：复用 `Portal.vue: openDrawingPlatform()` 的 SSO form POST 逻辑（抽成 `utils/sso.js` 共享），多带 `redirect` 字段 = `/mode-select/{templateId}`
- **引用列表**：每条来源下方灰色小字显示 quote 片段；web 来源单独一组，外链新标签打开
- **网络来源卡**：favicon（`https://www.google.com/s2/favicons?domain=` 或纯文字标）+ 标题 + 域名

### 8.4 引用点击高亮

- `MarkdownViewer.vue`：`[[slug#En]]` 渲染为上标 [N]（编号沿用现有首现分配逻辑）；点击时从 citations meta 取该引用的 quote 与行号 → `router.push({path:/kb/wiki/${slug}, query:{kind, hl: encodeURIComponent(quote前80字)}})`
- `WikiViewer.vue`：监听 `route.query.hl` → 内容渲染完成后（nextTick + watch content）用 TreeWalker 遍历文本节点做**归一化匹配**（去空白/标点差异），命中则包 `<mark class="cite-hl">`（黄底）并 `scrollIntoView({block:"center"})`；匹配失败降级：若 query 带 heading 则滚到对应标题；再失败就正常显示不报错
- `utils/citations.js`：`stripCitationFooter`/可见引用抽取适配 `#En` 后缀（剥编号只留 slug 做规范化）

---

## 9. Phase 8：zhihuitu 侧改动（两处，均在 `C:\Users\lenovo\Desktop\zhihuitu\backend`）

### 9.1 SSO redirect（`app/api/v1/auth.py` 的 `POST /auth/sso`）

- Form 接受可选 `redirect: str = Form("")` 字段
- 校验：必须以 `/` 开头、不含 `//` 与 `\`、长度 ≤200，否则忽略用默认 `/templates`
- 成功 HTML 的 `location.replace('/templates')` 改为 `location.replace({redirect_json})`（值经 json.dumps 注入，防 XSS）
- **注意部署文档的坑**：返回 HTML 里 localStorage.userInfo 必须双重 json.dumps，改动时别破坏现有逻辑

### 9.2 公开缩略图接口（`app/api/v1/templates.py` 新增端点）

支撑 §5.4 的"新模板自动跟随"：kgqa 不再本地存任何缩略图。

```python
@router.get("/templates/{template_id}/preview")
async def template_preview(template_id: str):
    # 1) template_id 白名单校验: 必须匹配 ^[A-Za-z0-9_-]+$（防路径穿越）
    # 2) path = settings.CHART_TEMPLATES_DIR / template_id / "preview.png"
    # 3) 存在 → FileResponse(media_type="image/png", 带 Cache-Control: public, max-age=86400)
    # 4) 不存在 → 404（kgqa 前端显示占位图）
```

- 与列表接口一样**公开无需 token**（templates 路由本就未挂鉴权依赖）
- 前提：`chart-templates/` 目录在后端容器内可读——zhihuitu 后端容器工作目录是 `/app`，构建上下文含 chart-templates；部署时确认容器内该目录存在，若镜像里没有则 `docker cp` 补进去再 commit（目录约几 MB，含 38 个模板子目录）
- 新模板上架流程不变：模板目录（含 preview.png）进服务器 → `sync_templates` 入库 → kgqa 侧列表与缩略图自动出现

---

## 10. Phase 9：本地验证清单

后端（`D:\kg-qa-ie\backend`，用 `py` 不是 `python`，`PYTHONIOENCODING=utf-8`）：
- [ ] `py -m pytest tests/` 现有测试通过（涉及 query/hybrid/vector_index 的测试需同步改）
- [ ] 新增单测：chunker（行号对齐、超长章节切分）、tool_calls 流式解析、引用协议解析与降级、熔断器
- [ ] `py -c "from app.main import app"` 导入无错

前端（`D:\kg-qa-ie\frontend`）：
- [ ] `pnpm build` 通过（此项目 vite build 无类型检查）
- [ ] 本地起 dev server 人工过一遍：提问 → 状态条 → 引用点击高亮 → 模板面板 → SSO 跳转

zhihuitu（`C:\Users\lenovo\Desktop\zhihuitu`）：
- [ ] 后端改动后 `frontend` 无需动；本地无法完整跑 SSO，靠部署后 E2E

---

## 11. Phase 10：部署（凭据与命令见 `Desktop\部署对接信息.md`，严格走增量，禁止全量 build）

顺序：

1. **kgqa 后端**：改动文件 `docker cp` 进 `kgqa-backend-1` → `docker commit kgqa-backend:latest` → `docker compose up -d --no-build backend`（compose 在 `/root/kgqa/`）。无新 pip 依赖（全部 httpx 直调）
2. **服务器重建 chunk 索引**：进容器跑 `py scripts/rebuild_query_chunk_index.py`（后台 nohup，约几分钟；期间旧问答不可用的窗口≈0，因为 grep 腿始终可用，语义腿 marker 未就绪时自动降级——这是 hybrid.py 的既有行为）
3. **kgqa 前端**：本地 `pnpm build` → tar 上传 → `docker cp` 进 `kgqa-frontend-1:/usr/share/nginx/html/` → 清旧 assets 哈希文件 → `docker commit`。无 CDN，强刷即可
4. **zhihuitu 后端**：sftp 上传 `auth.py` + `templates.py` → `Dockerfile.incr` 增量构建（FROM sso-base + COPY 两个文件）→ `docker compose up -d --no-build backend`；确认容器内 `chart-templates/` 目录存在（§9.2 前提），没有则 docker cp 补齐并 commit。**draw.example.com 走 EdgeOne CDN，但 /api POST 不缓存，无需清缓存**；缩略图 GET 若被 CDN 缓存反而是好事（已带 max-age），验证时用 `https://draw.example.com`（直连不经 CDN）排除缓存干扰
5. **配置填入**：管理后台 `/kb/system/model` 填 Tavily key、画图接口 base_url/model/key（**向用户索要**）
6. **E2E 验证**：真浏览器（headless Chrome/puppeteer 或人工）跑：登录门户 → 问答（简单题速度不慢于改造前）→ 引用点击高亮 → 触发联网搜索题（如"2025 年最新的精益生产趋势"）→ 触发模板推荐题（如"怎么做帕累托分析"）→ 右侧面板 → SSO 跳转直达模板页 → 图片生成题
7. Chroma 客户端版本强约束 **0.5.15**，勿升级

---

## 12. 验收标准

1. 简单知识库问题：首字延迟 ≤ 改造前（删掉了 router LLM 调用 + context 更短）
2. 引用点击 → wiki 页对应句子黄底高亮并滚动到位；引用列表显示原文片段
3. 问"最新/2025 年"类问题 → 状态条显示联网搜索 → 答案带可点击网络来源
4. 问绘图平台有模板的方法（帕累托/价值流/鱼骨图等）→ 蓝色提示条 → 右侧面板 → 一键 SSO 直达 zhihuitu 对应模板
5. 图片生成不阻塞文字；图片就绪后自动出现在正文占位处
6. 拔掉 Tavily key（后台清空）→ 模型不再尝试联网，问答正常
7. 闲聊"你好" → 秒回（快路径未破坏）
8. 多轮指代（"它的实施步骤呢"）→ 检索正确（rewriter 未破坏）
9. wiki 页编辑后 ≤15 分钟新内容可被语义检索（增量索引未破坏）
10. 历史会话重新打开：引用/图片/模板卡完整恢复（meta 落库完整）
11. 用别名提问（`.wiki-schema.md` 别名组里的同义词，如用俗称问正式术语）→ 检索命中正确页面（别名扩展未破坏，且 `search_kb` 工具内同样生效）
12. 管理后台改工具配置**不重启即生效**：清空 Tavily key → 下一条提问不再联网；填回 → 下一条恢复；换 key 自动清熔断
13. 智绘图后台上架一个新模板（含 preview.png）→ **不动 kgqa 任何代码**，≤10 分钟后问答里可推荐该模板且缩略图正常显示

## 13. 待办输入（需向用户索要）

- [ ] Tavily API key
- [ ] 画图接口的 base_url / model 名 / API key（OpenAI 兼容）

## 14. 已知风险与注意

- DeepSeek function calling 偶发把 arguments 拆成异常片段：解析器要容忍空片段与乱序 index
- 第一轮 content 缓冲策略（§6.1）若观察到 DeepSeek 总是先输出大段正文再调工具，可改为"content 直接流出 + 工具结果后 replace_answer 重写"，二选一实测决定
- `_recover_cited_pages` 的"引用找回"策略在 chunk 协议下只对页级 `[[slug]]` 降级引用生效，属预期
- 前端 `conversations.js` 流结束后会 `loadSession` 覆盖本地状态：所有富媒体数据必须进 assistant meta，否则刷新即丢
- Windows 开发环境：`py` 不是 `python`；路径含 `*` 设 `MSYS2_ARG_CONV_EXCL='*'`
