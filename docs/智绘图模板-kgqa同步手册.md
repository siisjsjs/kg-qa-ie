# 智绘图模板变更 → kgqa 同步手册

> 你在 **智绘图（zhihuitu）** 里新增/修改 `chart-templates` 后，如何让 **知识库问答（kg-qa-ie）** 也能正确推荐、出图、预览、导出 Excel、跳转编辑器。
>
> 源码路径：
> - 智绘图：`C:\Users\lenovo\Desktop\zhihuitu`
> - kgqa：`D:\kg-qa-ie`
>
> 服务器/域名/凭据/增量部署命令：`C:\Users\lenovo\Desktop\部署对接信息.md`

---

## 0. 铁律：本地联本地，云端联云端（绝不混连）

两套环境各自成闭环。**改模板、联调、部署、验证，都在同一套环境里完成**，不要跨环境测一半、配一半。

| | **本地环境** | **云端环境** |
|---|-------------|-------------|
| kgqa 入口 | http://localhost:5180（或 vite 顺延端口） | https://kb.example.com |
| kgqa 后端 | http://127.0.0.1:8000 | 同域 nginx 反代 |
| 智绘图前端 | http://localhost:5173 | https://draw.example.com |
| 智绘图后端 | http://127.0.0.1:8010（sqlite） | 同域 nginx 反代 |
| kgqa 后台 `zhihuitu.base_url` | **`http://127.0.0.1:5173`** | **`https://draw.example.com`** |
| 智绘图 `KGQA_BASE_URL` | **`http://127.0.0.1:8000`** | **`https://kb.example.com`** |
| 用户/图表数据 | 本地 sqlite + 本地 MySQL | 云端 MySQL（与本地互不相通） |
| SSO 校验 | kgqa 8000 ↔ 智绘图 8010 | kb.example.com ↔ draw.example.com |

**禁止事项（踩坑清单）**：

- ❌ 本地 kgqa 的 `base_url` 填 `draw.example.com`（会把本地问答打到云端智绘图）
- ❌ 云端 kgqa 的 `base_url` 填 `127.0.0.1:5173`（生产用户无法访问）
- ❌ 本地改完模板只部署云端、不先在本地验通
- ❌ 本地验证通过就认为云端已生效（必须分别在云端再验一遍）
- ❌ 本地 sqlite 里存的 chart_id 拿去测云端 Excel 导出

**切换环境时**：只改 kgqa 管理后台「绘图平台地址」+ 确认智绘图 `.env` 里 `KGQA_BASE_URL`；保存后会清模板/token 缓存，无需重启。

---

## 1. 架构（与环境无关）

```
用户提问 (kgqa)
    │
    ├─ recommend_chart_templates ──GET /api/v1/templates──► 智绘图
    ├─ generate_chart ──POST /charts/generate + POST /charts──► 智绘图
    ├─ SidePanel「前往编辑器」── SSO ──► 智绘图前端编辑器
    └─ SidePanel「导出 Excel」── kgqa 代理 ──► 智绘图 GET /charts/{id}/export-excel
```

**账号**：kgqa 是唯一用户源；出图/存图/导出 Excel 用提问用户 token，经 `POST /api/v1/auth/sso-token` 换智绘图 JWT。**不需要**在 kgqa 配服务账号。

---

## 2. 什么会自动同步，什么不会（两边通用）

| 内容 | kgqa 是否自动跟上 | 说明 |
|------|-------------------|------|
| 模板 id / name / description / tags | ✅ | 调智绘图 `GET /api/v1/templates`，缓存 10 分钟 |
| `paramSchema` | ✅ | API 返回；改后 ≤10 分钟或清缓存 |
| `exportFormats` | ✅ | 来自 `meta.json` |
| 缩略图 preview | ✅ | 智绘图 `GET /templates/{id}/preview` |
| 服务端 PNG（问答里出图） | ❌ | 要有 `chart-templates/{id}/generator.py` 并部署到**当前环境**的 chart-templates |
| Excel 直接下载 | ❌ | 要有 `excel_export.py` 实现并部署到**当前环境**智绘图 backend |
| drawio 可编辑 | ❌ | 要部署**当前环境**智绘图前端 + chart-templates |
| LLM 何时出图 | ⚠️ | 大改类型时可能要改 kgqa `agent_system.md`（改一次，本地云端共用同一份源码） |

**结论**：只改 `meta.json` 文案/参数 → 多数情况**只部署/重启当前环境的智绘图**即可。改 generator / Excel / drawio 前端 → 当前环境的智绘图必动；kgqa 代码通常不用动。

---

## 3. 改模板：按变更类型（源码层，本地云端共用）

以下说的是**改哪些文件**；具体在哪个环境生效，见 §4（本地）和 §5（云端）。

### 3.1 只改文案 / 标签 / 示例（不动 paramSchema）

- **智绘图**：`chart-templates/{id}/meta.json`
- **kgqa**：一般不改代码

### 3.2 改 `paramSchema`

- **智绘图**：`meta.json` + 对应 Vue / `buildMxGraphXml.ts` / `generator.py` 字段对齐
- **kgqa**：一般不改；预匹配不准时改 `chart_templates.py`；可选改 `agent_system.md`

### 3.3 新增模板

- **智绘图**：新建 `chart-templates/{id}/`，按 engine 补齐 `generator.py` / `*.vue` / `buildMxGraphXml.ts` / `preview.png`
- **kgqa**：列表自动出现；新 Excel 模板要在智绘图 `excel_export.py` 注册；可选改 `agent_system.md`

### 3.4 改 Vue `exportExcel`（5 个 IE 模板）

| template_id | Vue | Python（智绘图 `excel_export.py`） |
|-------------|-----|-------------------------------------|
| man-machine | ManMachineChart.vue | `_build_man_machine` |
| two-hand | TwoHandChart.vue | `_build_two_hand` |
| joint-operation | JointOperation.vue | `_build_joint_operation` |
| process-flow | ProcessFlow.vue | `_build_process_flow` |
| therblig | TherbligChart.vue | `_build_therblig` |

流程：Vue 改好 → 同步到 `excel_export.py` → `pytest tests/test_excel_export.py`。kgqa 只代理，不改 Excel 逻辑。

### 3.5 改 `generator.py`

- **智绘图**：`chart-templates/{id}/generator.py`
- **kgqa**：不改

### 3.6 改 drawio 模板

- **智绘图**：`buildMxGraphXml.ts` + `drawio-editor/index.vue` + 可选 `generator.py`
- **kgqa**：一般不改（存原始 params，编辑器现场建 xml）

### 3.7 改 `exportFormats`

- **智绘图**：`meta.json`；若新增 xlsx 须有 `excel_export.py` 实现
- **kgqa**：SidePanel 读 API 的 `export_formats` 决定是否显示按钮

---

## 4. 本地环境：改完模板后怎么做

### 4.1 启动四服务（缺哪个起哪个）

```powershell
# 1 kgqa 后端
cd D:\kg-qa-ie\backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2 kgqa 前端
cd D:\kg-qa-ie\frontend
pnpm dev
# → http://localhost:5180（占用则 5181）

# 3 智绘图后端
cd C:\Users\lenovo\Desktop\zhihuitu\backend
venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8010

# 4 智绘图前端
cd C:\Users\lenovo\Desktop\zhihuitu\frontend
pnpm dev
# → http://localhost:5173
```

### 4.2 确认本地配置（必做）

| 配置项 | 正确值 | 在哪改 |
|--------|--------|--------|
| kgqa `zhihuitu.base_url` | `http://127.0.0.1:5173` | kgqa 管理后台 → 模型配置 |
| 智绘图 `KGQA_BASE_URL` | `http://127.0.0.1:8000` | `zhihuitu/backend/.env` |

### 4.3 本地生效模板改动

| 改了什么 | 本地操作 |
|----------|----------|
| 仅 `meta.json` | 保存文件；重启智绘图 backend（8010）或等 registry 扫描 |
| `generator.py` | 保存到本地 `chart-templates/`；重启智绘图 backend |
| `*.vue` / drawio 前端 | 保存源码；vite HMR 或刷新 5173 |
| `excel_export.py` | 保存；重启智绘图 backend |
| kgqa 的 tools / prompt | 保存；重启 kgqa backend（8000） |

**本地没有 docker 部署步骤**——改文件 + 重启对应 dev 进程即可。

### 4.4 本地验证清单

在 **http://localhost:5180** 登录（admin / 本地密码），逐项打勾：

- [ ] 智绘图 **5173** 模板中心：该模板能打开、编辑器正常
- [ ] kgqa 问相关题：`generate_chart` 被调用，示意图预览有图
- [ ] 点「前往编辑器」→ 打开 **localhost:5173** 的编辑器（不是 draw.example.com）
- [ ] 若有 xlsx：SidePanel「导出 Excel」直接下载，与 5173 编辑器内导出一致
- [ ] 追问改图：新图新 chart_id；旧图「前往编辑器」仍打开旧版本
- [ ] 智绘图 **8010** 的「我的图表」里能看到本次记录

本地全部通过后，再进入 §5 部署云端（**重新在云端验一遍，不要跳过**）。

---

## 5. 云端环境：改完模板后怎么部署

### 5.1 部署前确认

| 配置项 | 正确值 |
|--------|--------|
| kgqa `zhihuitu.base_url` | `https://draw.example.com` |
| 智绘图 `KGQA_BASE_URL` | `https://kb.example.com`（compose environment） |

### 5.2 智绘图云端（YOUR_SERVER_IP）

**原则**：增量部署，禁止 `docker compose build` 全量重建。详见 `部署对接信息.md`。

#### A. 仅 chart-templates（meta / generator / preview）

```text
本地 chart-templates/{id}/ 改动
  → SFTP 上传到 /opt/zhihuitu/chart-templates/{id}/
  → 无需重启 frontend；backend 按需重启或等 startup sync
```

#### B. 智绘图 backend 改动

```text
本地 backend/app/... 改动
  → SFTP 到 /opt/zhihuitu/backend/app/...
  → docker commit 快照 + Dockerfile.incr 增量 COPY + compose up -d --no-build backend
```

常见路径：

- `app/services/excel_export.py`
- `app/services/chart_generator.py`
- `app/api/v1/charts.py`、`auth.py`、`templates.py`

#### C. 智绘图 frontend 改动

```text
本地 pnpm build
  → dist 打包上传
  → docker cp 进 zhihuitu-frontend-1:/usr/share/nginx/html/
  → 清理旧 assets 哈希文件
  → docker commit zhihuitu-frontend-1 zhihuitu-frontend:latest
  → 腾讯云 EdgeOne 清 CDN 缓存（draw.example.com）
```

### 5.3 kgqa 云端（YOUR_SERVER_IP）

**原则**：服务器无前端源码；backend 用 docker cp + commit。

#### A. kgqa backend

```text
本地 D:\kg-qa-ie\backend\app\... 改动
  → docker cp 进 kgqa-backend-1
  → docker commit kgqa-backend-1 kgqa-backend:latest
```

与模板/绘图相关的主要目录：

- `app/services/tools/`（chart_generate、chart_templates 等）
- `app/api/v1/files.py`（Excel 代理）
- `app/api/v1/query.py`
- `app/prompts/agent_system.md`

#### B. kgqa frontend

```text
本地 pnpm build
  → dist 上传
  → docker cp 进 kgqa-frontend-1:/usr/share/nginx/html/
  → 清理旧 assets → docker commit
  → 浏览器强刷（无 CDN）
```

### 5.4 云端验证清单

在 **https://kb.example.com** 登录，逐项打勾：

- [ ] kgqa 后台 `zhihuitu.base_url` = `https://draw.example.com`
- [ ] 问绘图题 → 出图 → 预览正常
- [ ] 「前往编辑器」→ 打开 **draw.example.com**（SSO 自动登录）
- [ ] Excel 直接下载正常
- [ ] 智绘图 CDN 已清缓存后，编辑器 UI 是最新（drawio / exportExcel 改动）

---

## 6. 推荐总流程（两套环境分开走）

```
【阶段一 · 本地】
  改源码（chart-templates + 必要 backend/frontend）
  → 确认 §4.2 配置全是 127.0.0.1 / localhost
  → 四服务本地跑起来
  → 走完 §4.4 验证清单

【阶段二 · 云端】
  确认 §5.1 配置全是 draw.example.com / kb.example.com
  → 按 §5.2、§5.3 分别部署智绘图、kgqa
  → 清 CDN
  → 走完 §5.4 验证清单
```

**不要在阶段一里测云端 URL，不要在阶段二里依赖本地 sqlite 的数据。**

---

## 7. 关键文件速查

### kgqa（`D:\kg-qa-ie`）

| 文件 | 作用 |
|------|------|
| `backend/app/services/tools/chart_templates.py` | 模板列表、预匹配、改图 hint |
| `backend/app/services/tools/chart_generate.py` | SSO 换 token、出图、存 chart |
| `backend/app/api/v1/files.py` | Excel 代理下载 |
| `backend/app/prompts/agent_system.md` | LLM 出图策略 |
| `frontend/src/components/SidePanel.vue` | 预览 / 下载 / Excel / 跳编辑器 |
| `frontend/src/utils/sso.js` | SSO 跳智绘图 |

### 智绘图（`C:\Users\lenovo\Desktop\zhihuitu`）

| 路径 | 作用 |
|------|------|
| `chart-templates/{id}/meta.json` | 元数据 + paramSchema + exportFormats |
| `chart-templates/{id}/generator.py` | 服务端 PNG |
| `chart-templates/{id}/*.vue` | vue-component 编辑器 |
| `chart-templates/{id}/buildMxGraphXml.ts` | drawio XML |
| `backend/app/services/excel_export.py` | 5 类 IE Excel 服务端导出 |
| `backend/app/api/v1/charts.py` | export-excel 端点 |
| `backend/app/api/v1/auth.py` | SSO + sso-token |
| `frontend/src/views/user/drawio-editor/index.vue` | params → xml |

云端 chart-templates 目录：**`/opt/zhihuitu/chart-templates`**（与本地 `Desktop/zhihuitu/chart-templates` 同步）。

---

## 8. 常见问题

**Q：本地正常，云端出图 404？**  
A：云端 `/opt/zhihuitu/chart-templates` 缺 `generator.py` 或 backend 未部署最新代码。与本地是否通过无关。

**Q：云端问答连到了本地智绘图？**  
A：云端 kgqa 后台 `base_url` 误填了 `127.0.0.1:5173`。改回 `https://draw.example.com` 并保存。

**Q：本地问答图能出，但跳编辑器到了 draw.example.com？**  
A：本地 kgqa 后台 `base_url` 误填了云端地址。改回 `http://127.0.0.1:5173`。

**Q：Excel 400？**  
A：当前环境的智绘图 backend 缺 `excel_export.py` 实现，或 chart 无 chart_id。

**Q：改图后编辑器是旧版？**  
A：点**新消息**上的「前往编辑器」（每次 generate_chart 新建记录）。

**Q：前端改了云端不生效？**  
A：智绘图需清 **CDN**；kgqa 强刷即可。

---

## 9. 版本记录

| 日期 | 说明 |
|------|------|
| 2026-08-15 | 首版 |
| 2026-08-15 | 修订：本地/云端完全分章，强调不混连 |
