# 前端沿用旧 UI · 跑通完成报告

> 项目：KG-QA-IE（工业工程知识库问答系统）
> 阶段：**前端 UI 沿用与骨架落地**（隶属 P1，附 P2-P6 准备）
> 状态：✅ **完成**
> 日期：2026-05-19
> 项目位置：`D:\kg-qa-ie\frontend\`

---

## 〇、本轮目标

把旧项目 `C:\Users\lenovo\Desktop\kg-qa-refactor\frontend` 的 UI 资产迁到新项目 `D:\kg-qa-ie\frontend`，按 v1.2 规划文档的页面命名（Query / Digest / Graph / WikiViewer / Sources / GrowthDashboard / Crystallize / SchemaEditor / PurposeEditor / ModelConfig / Users / Login / Register）落地真实可运行的前端骨架，与后端 JWT 协议、SSE 协议、`/api/v1/*` 端点对齐，全栈构建跑通。

---

## 一、对照计划的退出标准

来源：`C:\Users\lenovo\.claude\plans\c-users-lenovo-desktop-kg-qa-refactor-d-quirky-kite.md` §十「验证清单」。

| # | 退出标准 | 状态 | 验证方式 |
|---|---------|------|---------|
| 1 | `npm install` 通过 | ✅ | 150 packages，0 漏洞 |
| 2 | `vite build` 通过 | ✅ | 6.22 s 构建成功，仅 chunk size 警告 |
| 3 | `vite dev` 启动正常 | ✅ | 349 ms ready，监听 5173 |
| 4 | 25/25 关键源文件可被 vite 编译 | ✅ | curl `/src/...` 全部 200 |
| 5 | 主页静态依赖链全 200 | ✅ | element-plus.css / katex.css / App.vue / main.scss |
| 6 | 旧 UI 资产沿用（橙色主题 + Logo + 滚动条 + 卡片） | ✅ | `styles/main.scss` 完整保留 IE Palette `#F97316` |
| 7 | 旧独有页面全部不引入（Triple/Template/Document/Roles/Menus/Monitor/PromptVersion/Announcements） | ✅ | 路由表 / 菜单 / API 模块均无残留 |
| 8 | JWT 认证（access + refresh） | ✅ | `api/http.js` 拦截器自动 refresh + 并发雪崩防护 |
| 9 | SSE + JWT 流式问答 | ✅ | `api/index.js streamQuery` fetch + ReadableStream + AbortController |
| 10 | Markdown 双链 + KaTeX | ✅ | `MarkdownViewer.vue` 自写 inline ruler + `$...$` `$$...$$` |
| 11 | 路由守卫（requiresAuth / requiresAdmin / requiresGuest） | ✅ | `router/index.js beforeEach` |
| 12 | 自检发现的 bug 已修 | ✅ | 4 个 bug 已修，详见 §四 |

---

## 二、本轮交付清单

### 顶层接线（5 个）
- `package.json` — 补 markdown-it / katex / @element-plus/icons-vue / three-spritetext / sass-embedded
- `index.html` — 保留（无需改动）
- `vite.config.js` — 保留（`/api → http://localhost:8000` proxy 已就绪）
- `src/main.js` — 注入 Element Plus 中文 locale + 全局图标 + KaTeX CSS
- `src/App.vue` — 极简 RouterView 外壳（布局交给 MainLayout）

### 路由 / 布局 / 状态（5 个）
- `src/router/index.js` — 16 路由 + beforeEach 三守卫
- `src/layouts/MainLayout.vue` — 响应式 sidebar + 顶部 header + 用户下拉 + 全展开 7 主项 5 子项菜单
- `src/stores/auth.js` — JWT access/refresh + bootstrap + isLoggedIn/isAdmin
- `src/stores/config.js` — LLM/Embedding 配置（保留旧字段）
- `src/stores/ui.js` — sidebarCollapsed + isMobile

### API 层（2 个文件，10 个模块）
- `src/api/http.js` — axios + JWT 注入 + 401 自动 refresh（共享 Promise 防雪崩）
- `src/api/index.js` — auth / ingestion / query (含 streamQuery SSE) / digest / graph / wiki / status / crystallize / admin（含 users / gap-queue）

### 通用组件（4 个）
- `src/components/Logo.vue` — KG-Wiki 六边形 SVG（沿用旧 IE-KG）
- `src/components/MarkdownViewer.vue` — markdown-it + 双链 inline ruler + KaTeX 自写 ruler + click 委托
- `src/components/Graph3D.vue` — 3d-force-graph 封装 + props 驱动 + focus(nodeId) 暴露
- `src/components/PathHighlight.vue` — 接收 path payload 显示 tag + emit focus-node

### 页面（13 个）
- `views/Login.vue` — 旧 hero-block + benefits-chip + login-card 三段式，next 跳转
- `views/Register.vue` — 表单 + 密码强度条 + 待审核状态提示
- `views/GrowthDashboard.vue` — 4 stat-card + 入口 grid + 热门主题 + 待跟进
- `views/Query.vue` — fetch SSE + 评分角标 + gap_flag + 引用 tag → 跳 wiki
- `views/Digest.vue` — 三模板（auto / deep_dive / comparison / timeline） + 知识缺口
- `views/Graph.vue` — Graph3D + 节点抽屉 + insights 抽屉 + ?path 高亮 + 重建按钮
- `views/WikiViewer.vue` — 索引视图（按 kind 分组）+ 单页视图（frontmatter 折叠 + 反链 + 引用 + admin 编辑）
- `views/Sources.vue` — 多文件上传 + 隐私自查 dialog + 5 阶段进度表 + 5s 轮询 + 删除
- `views/system/ModelConfig.vue` — LLM + Embedding 双卡片表单（沿用现成实现）
- `views/system/SchemaEditor.vue` — 4 tab：别名词表 / 关系类型 / 成长配置 / 原始 markdown
- `views/system/PurposeEditor.vue` — 左编辑 + 右预览 + Ctrl+S 保存
- `views/system/Users.vue` — 用户列表 + 注册审核 tab
- `views/system/Crystallize.vue` — 合并候选审核 + 用户文本结晶化两 tab

### 样式 / 常量（2 个）
- `src/styles/main.scss` — IE Palette + 滚动条 + 卡片 / 按钮 / 输入 / 表格深度覆盖 + markdown-viewer 主题 + stream-loading 动画
- `src/constants/typeColors.js` — 35 种实体类型颜色 + 30 色动态调色板 + colorOf 辅助

---

## 三、最终验证结果

### 依赖安装
```
$ cd D:\kg-qa-ie\frontend && npm install
added 150 packages in 15s
```

### 生产构建
```
$ npx vite build
✓ built in 6.22s
dist/assets/Graph-*.js              1,468 kB │ gzip: 397 kB
dist/assets/index-*.js              1,240 kB │ gzip: 402 kB
dist/assets/MarkdownViewer-*.js       353 kB │ gzip: 124 kB
dist/assets/index-*.css               391 kB │ gzip:  57 kB
（其余 view chunk 均 < 7 kB）
```

### dev 服务器
```
$ npx vite
VITE v5.4.21  ready in 349 ms
➜ Local: http://127.0.0.1:5173/
```

### 25/25 源文件转译 200
```
200  views/Login.vue        200  components/Graph3D.vue
200  views/Register.vue     200  components/MarkdownViewer.vue
200  views/GrowthDashboard  200  components/PathHighlight.vue
200  views/Query.vue        200  components/Logo.vue
200  views/Digest.vue       200  stores/auth.js
200  views/Graph.vue        200  stores/config.js
200  views/Sources.vue      200  stores/ui.js
200  views/WikiViewer.vue   200  api/http.js
200  views/system/*.vue (5) 200  api/index.js
200  layouts/MainLayout.vue 200  router/index.js
                            200  styles/main.scss
```

### 主页静态依赖链
```
HTTP/1.1 200 OK | /node_modules/element-plus/dist/index.css
HTTP/1.1 200 OK | /node_modules/katex/dist/katex.min.css
HTTP/1.1 200 OK | /src/App.vue
HTTP/1.1 200 OK | /src/styles/main.scss
HTTP/1.1 200 OK | /favicon.svg
HTTP/1.1 200 OK | /src/main.js
```

---

## 四、自检发现并修复的 4 个 bug

| # | 文件 | 现象 | 修复 |
|---|------|------|------|
| 1 | `views/Sources.vue` | `<el-upload>` 同时用 `v-model:file-list` 和 `:on-change` push，导致同一文件重复入列；`pendingFiles` 是死代码 | 改用 ref + el-upload 暴露的 `clearFiles()`，删 pendingFiles 分支 |
| 2 | `views/system/PurposeEditor.vue` | placeholder 写的 `\n` 字面量在浏览器显示反斜杠+n | 改为 `&#10;` HTML entity |
| 3 | `components/Graph3D.vue` | `import * as THREE` 未使用；`el.addEventListener('dblclick', ...)` 内是空函数 | 删除两处死代码 |
| 4 | `api/index.js streamQuery` | `(await r.json()).data \|\| (await r.clone().json())` —— 第二次 `r.clone()` 在第一次 `r.json()` 之后已无意义 | 改为单次 `await r.json()`，再走 `data ?? json` 兜底 |

---

## 五、关键设计决策

### 5.1 沿用粒度：仅复用 UI 资产
- **完整复用**：Graph3D / typeColors / Logo / 橙色 Palette / 滚动条 / 卡片样式
- **结构性改写**：Login（hero+card 三段式）/ Register / Dashboard 卡片 / SSE 解析器 / Settings+EmbeddingConfig 合并 → ModelConfig / 旧 Users 表格
- **不复用**：Triple/Template/Document/Roles/Menus/Monitor/PromptVersion/Announcements 旧页面 + 对应 7 个 API 模块 + qa/announcement/app store + 7 个旧依赖（pinia-plugin-persistedstate / echarts / xlsx / exceljs / html2canvas / dompurify / marked）

### 5.2 认证：JWT access + refresh
- Token 存 localStorage（`kg_access_token` / `kg_refresh_token`）
- axios 请求拦截器注入 Authorization
- 401 自动 refresh：共享 `refreshing` Promise 防止并发雪崩；refresh 失败统一跳 `/login?next=...`
- SSE 不走 axios，自己处理 401 → 调一次 `/auth/refresh` → 重发一次

### 5.3 SSE 流式问答（fetch + ReadableStream）
- 选择 fetch 而非 EventSource：因为 EventSource 不支持自定义 header，无法注入 JWT
- `AbortController` 在组件 unmount 取消，防 setState on unmounted
- 事件类型协议：`meta` / `token` / `citation` / `path` / `quality` / `done` / `error` —— 与后端 P2 设计对齐

### 5.4 Markdown 双链 + KaTeX 自写
- 双链 inline ruler：识别 `[[页面名]]` 或 `[[页面名|别名]]` → `<a class="wikilink" data-slug="...">`
- click 委托：在 MarkdownViewer 容器上监听，如果命中 `a.wikilink` 则 `router.push`（避免 history 模式下 `<a href>` 不走 vue-router）
- KaTeX：自写 `math_inline`（`$...$`）和 `math_block`（`$$...$$`）ruler，不依赖维护不稳的 markdown-it-katex
- `throwOnError: false` —— 渲染失败时显示原始 LaTeX 不崩页

### 5.5 路由表（16 条 + 兜底）
- 守卫层 `bootstrapped` 标志：第一次进路由时 `await auth.bootstrap()` 拉一次 `/auth/me` 校验 token；后续路由不再重复
- `requiresGuest` 已登录时跳 `/dashboard`
- `requiresAdmin` 非 admin 时跳 `/dashboard`（不暴露 403）
- `?next=` 保留登录前目标路径（不含 `/`）

### 5.6 侧边栏菜单：本地常量 + 计算过滤
- 不远端 `fetchMenus`（旧前端做法）—— 菜单是产品骨架不应依赖网络
- `MENU_TREE` 写在 MainLayout 内 + `visibleMenu` computed 按 `auth.isAdmin` 过滤
- icon 直接 import element-plus icons，不靠 unplugin

---

## 六、本地启动

### 一次性
```bash
cd D:\kg-qa-ie\frontend
npm install
```

### 启 dev（带 /api 代理）
```bash
cd D:\kg-qa-ie\frontend
npm run dev
# http://localhost:5173
# /api/* 自动代理到 http://localhost:8000
```

### 生产构建
```bash
cd D:\kg-qa-ie\frontend
npm run build
npm run preview   # 预览 dist
```

### 全栈
```bash
# 终端 A：后端
cd D:\kg-qa-ie\backend
py -m uvicorn app.main:app --port 8000 --reload

# 终端 B：前端
cd D:\kg-qa-ie\frontend
npm run dev
```

---

## 七、目录速览（关键文件）

```
D:\kg-qa-ie\frontend\
├── package.json                          # ★ 11 deps + 3 devDeps
├── vite.config.js                        # /api → :8000 proxy
├── index.html
└── src/
    ├── main.js                           # ★ Element Plus + 全局图标 + KaTeX CSS
    ├── App.vue
    ├── api/
    │   ├── http.js                       # ★ axios + JWT refresh 共享 Promise
    │   └── index.js                      # ★ 10 模块，含 streamQuery SSE
    ├── stores/
    │   ├── auth.js                       # ★ JWT + bootstrap + isLoggedIn/isAdmin
    │   ├── config.js                     # LLM/Embedding 配置（保留旧字段）
    │   └── ui.js                         # sidebar / mobile
    ├── router/index.js                   # ★ 16 路由 + 3 守卫
    ├── layouts/MainLayout.vue            # ★ 响应式 sidebar + header + 用户下拉
    ├── components/
    │   ├── Logo.vue                      # KG-Wiki 六边形 SVG
    │   ├── MarkdownViewer.vue            # ★ markdown-it + 双链 + KaTeX
    │   ├── Graph3D.vue                   # ★ 3d-force-graph + props 驱动
    │   └── PathHighlight.vue
    ├── views/
    │   ├── Login.vue                     # 三段式 hero + benefits + card
    │   ├── Register.vue                  # 密码强度 + 待审核
    │   ├── GrowthDashboard.vue           # 4 卡 + 入口 + 热门 + 跟进
    │   ├── Query.vue                     # ★ SSE + 引用 + 评分
    │   ├── Digest.vue                    # 三模板入口
    │   ├── Graph.vue                     # 3D + insights + path
    │   ├── WikiViewer.vue                # 索引 + 单页（admin 可编辑）
    │   ├── Sources.vue                   # ★ 上传 + 隐私 + 进度轮询
    │   └── system/
    │       ├── ModelConfig.vue           # 双卡片表单（沿用现成）
    │       ├── SchemaEditor.vue          # 别名 / 关系 / 成长 / raw
    │       ├── PurposeEditor.vue         # 左编辑右预览
    │       ├── Users.vue                 # 列表 + 审核
    │       └── Crystallize.vue           # 合并候选 + 文本结晶
    ├── styles/main.scss                  # ★ IE Palette + 通用样式
    └── constants/typeColors.js           # 35 类型 + 30 动态色
```

---

## 八、与后端 P1-P6 的对接点

P1 已就绪的端点，前端今天起即可联调：
- `POST /api/v1/init` —— Sources 页"初始化"按钮可调用
- `GET /api/v1/admin/config` / `PUT /api/v1/admin/config/{llm|embedding}` / `POST /api/v1/admin/config/test` —— ModelConfig 已联通

P2 待后端就绪的端点：
- `POST /api/v1/auth/{login,register,refresh,logout,me}` —— Login / Register / 守卫依赖
- `POST /api/v1/ingestion/upload` + `/{doc_id}/run` + `/batch` + `/jobs/{id}` —— Sources 页流水线
- `POST /api/v1/query`（SSE） —— Query 页主功能
- `GET /api/v1/wiki/{index, pages/{slug}}` + `PUT` —— WikiViewer
- `GET /api/v1/graph/{data, insights}` + `POST /rebuild` —— Graph
- `GET/PUT /api/v1/admin/{schema, purpose}` —— SchemaEditor / PurposeEditor

P4-P5 端点：
- `POST /api/v1/digest`、`GET /synthesis/{slug}` —— Digest
- `GET /api/v1/{status, trend, followups}` —— GrowthDashboard
- `GET /api/v1/admin/gap-queue`、`POST /api/v1/crystallize` —— Crystallize
- `GET /api/v1/admin/users{,/pending}` + `/{id}/{approve,reject}` —— Users

---

## 九、已知 TODO（留给 P2+）

1. **后端协议待对齐**
   - SSE 事件名（meta / token / citation / path / quality / done / error）需与 backend `query_router.py` 同步
   - `/auth/me` 返回结构（user 嵌套还是平铺？permissions 字段？）现在前端做了 `body?.user || body` 兼容，定型后简化
   - `/admin/config` 当前前端调 `/admin/config/llm` + `/admin/config/embedding` 两端点（兼容现成 ModelConfig 实现），后端可两选一

2. **mock fixture 未做**
   - 计划里写的 `src/api/__mocks__/{graph-data,status,wiki-page}.json` 没建
   - 当后端 P2 跟进及时时不需要；如要并行可补

3. **chunk 体积警告**
   - Graph chunk 1.4 MB（gzip 397 KB）—— 主要是 three.js + 3d-force-graph
   - 不阻塞 P1，P6 部署前用 `manualChunks` 拆
   - Element Plus 整包 1.2 MB —— 同上

4. **nginx SSE 反代未补**
   - 计划 §九 R3 提到 `proxy_buffering off; proxy_cache off;`，需要在 `D:\kg-qa-ie\deploy\nginx\` 配置文件里加，留给 P6

5. **Sass legacy API deprecation 警告**
   - vite + element-plus 的 sass 版本组合产生警告（非项目代码），不影响功能
   - sass-embedded 已切换；后续 element-plus 升级后消失

6. **mobile 抽屉手势未做**
   - 当前移动端侧栏靠折叠 +translateX 隐藏，没加触屏滑动手势
   - 不阻塞 MVP

---

## 十、下一步（与后端 P2 并行）

按计划文档 §八「实施阶段」P2 入口：

1. **后端 P2 完成 `/auth/*` → 立即联调** Login / Register / 守卫 / refresh
2. **后端 P2 完成 `/wiki/*` + `/ingestion/*` → 联调** Sources 上传 → ingest pipeline → WikiViewer 看入库结果
3. **后端 P2 完成 `/query` SSE → 联调** Query 页流式问答 + 引用跳转 +`gap_flag`
4. **后端 P2 完成 `/graph/data` → 联调** Graph 页 3D 渲染 + 节点抽屉
5. **后端 P2 完成 `/admin/{schema,purpose}` → 联调** SchemaEditor / PurposeEditor

P2 退出标准（与后端共同）：
- 用 30 篇 IE md 入库，前端 Sources 页能看到 5 阶段进度
- WikiViewer 索引页列出 ≥ 30 个 entity / topic / source
- 提 20 道测试题，Query 页流式答出，引用准确率 ≥ 80%
- Graph 页能渲染 ≥ 30 节点，点击节点抽屉显示
- 同名实体跨 2 个 source 不重复建页（前端在 WikiViewer 里能看到 frontmatter `sources[]` 多于一条）

---

**前端沿用 + 跑通完成签名**：17 个核心源文件 + 25/25 源文件 vite 200 + 1 次 build 通过 + 1 次 dev 启动 + 4 处 bug 已修。
