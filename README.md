# kg-qa-ie · 工业工程知识库门户

> 面向工业工程（IE）领域的知识库与知识图谱平台：把专业资料自动抽取成可检索、可追溯的 Wiki 知识网络。

![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688)
![Neo4j](https://img.shields.io/badge/graph-Neo4j-008cc1)
![Chroma](https://img.shields.io/badge/vector-Chroma-ff6b6b)
![MySQL](https://img.shields.io/badge/database-MySQL%208.0-4479a1)
![Docker](https://img.shields.io/badge/deploy-Docker%20Compose-2496ed)

kg-qa-ie 提供「材料入库 → 结构化抽取 → 混合检索 → 问答/深挖 → 知识图谱」的完整闭环，并带管理后台、会话记录、公告反馈、图表与生图等工具接口。可与智绘图平台（[zhihuitu](https://github.com/siisjsjs/zhihuitu)）通过 SSO 单点登录打通。

## 功能特性

- **知识入库流水线**：上传文档（Markdown/PDF 等）→ 分步抽取（步骤化 LLM pipeline）→ 生成/合并 Wiki 页（材料页、专题页、方法/指标页）→ 校验与门控。
- **混合检索**：关键词检索 + 向量检索（Chroma）+ RRF 融合排序，返回带出处（slug / 证据编号）的上下文。
- **知识图谱**：Neo4j 存储实体与关系，支持图数据、图谱洞察、重建；管理端可维护 schema、别名与关系类型。
- **Wiki 浏览**：书目视图、专题/学习路径（mindmap / roadmap）、页面版本历史、链接自动解析与死链清理。
- **问答会话**：会话与消息持久化、长对话摘要、追问建议（followups）、深度综合（digest / crystallize）。
- **管理后台**：LLM / Embedding / 生图 / Tavily / Firecrawl / 绘图平台地址等配置、用户与审批、缺口队列（gap-queue）、仪表盘指标。
- **工具接口**：图表模板与图表数据 Excel 导出、AI 生图文件服务。
- **身份**：JWT（access 60 分钟 / refresh 14 天），SSO 作为其他平台的用户源。

## 架构

```
                          ┌─────────────────────────────┐
   Browser ──HTTPS──▶ nginx │  静态前端(dist) + /api 反代  │
                          └──────────────┬──────────────┘
                                         │
                              ┌──────────▼──────────┐
                              │  FastAPI backend    │  :8000
                              │  api / services      │
                              └──┬───────┬───────┬───┘
                                 │       │       │
                          ┌──────▼─┐ ┌───▼────┐ ┌▼────────┐
                          │ MySQL  │ │ Neo4j  │ │ Chroma  │
                          │ 业务库 │ │ 图谱   │ │ 向量库  │
                          └────────┘ └────────┘ └─────────┘
                                 │
                     LLM（DeepSeek / OpenAI 兼容）
                     Embedding（BGE-M3 1024 维）
                     抓取增强（Tavily / Firecrawl）
```

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python · FastAPI · SQLAlchemy · Alembic · Pydantic · httpx |
| 业务库 | MySQL 8.0（`kg_qa_ie`） |
| 图谱 | Neo4j 5.24（APOC + Graph Data Science） |
| 向量 | Chroma 0.5.15（客户端版本需与之一致） |
| 模型 | LLM：DeepSeek / OpenAI 兼容；Embedding：`BAAI/bge-m3`（1024 维） |
| 前端 | Vue 3 构建产物，由 nginx 托管 |
| 部署 | Docker Compose（nginx / frontend / backend / mysql / neo4j / chroma） |

## 目录结构

```
.
├── backend/                 后端源码（完整可构建）
│   ├── app/
│   │   ├── api/v1/          路由（auth / wiki / ingestion / graph / admin / conversations ...）
│   │   ├── core/            配置、数据库、安全、crypto
│   │   ├── models/          ORM 模型
│   │   ├── prompts/         提示词
│   │   ├── services/        业务层
│   │   │   ├── ingestion/   入库流水线与批处理
│   │   │   ├── wiki/        Wiki 读写、链接、版本、导航
│   │   │   ├── embedding/   向量化与向量库
│   │   │   ├── graph/       Neo4j 客户端、图谱构建与洞察
│   │   │   ├── llm/         多模型适配
│   │   │   ├── digest/  crystallize/  growth/  深挖、综合、缺口与追问
│   │   │   ├── scraper/     网页抓取与清洗
│   │   │   ├── lint/        质量检查与修复
│   │   │   └── admin/ status/ conversations/
│   │   └── main.py
│   ├── migrations/          Alembic 迁移
│   ├── scripts/             开发/运维脚本（冒烟、校验、清理等）
│   ├── tests/               pytest 用例（unit / integration / regression）
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── alembic.ini
│   └── pyproject.toml
├── frontend/                前端源码（Vue 3 + Vite）
│   ├── src/
│   │   ├── views/           门户、Wiki、图谱、问答、管理后台
│   │   ├── components/
│   │   ├── api/             接口封装
│   │   ├── utils/           SSO、格式化等工具
│   │   └── router/
│   ├── public/
│   ├── tests/
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── vite.config.js
│   └── package.json
├── deploy/                  部署配置（compose 变体 + nginx 站点配置）
│   ├── docker-compose.yml / docker-compose.deploy.yml / *.prod.yml / *.smoke.yml
│   ├── nginx/               默认 / HTTPS / 生产 nginx 配置
│   └── nginx-default.conf
├── docs/                    设计、改造、运维与交接文档
├── incremental/             生产环境历史增量改动源码（参考用，已并入 backend/）
├── nginx/default.conf       生产站点 nginx 配置（TLS + 反代）
├── tools/                   运维与批处理脚本
├── .env.example             环境变量模板（全占位）
├── start.bat / stop.bat     Windows 本地一键启停
└── docker-compose.yml       生产编排（mysql / neo4j / chroma / backend / frontend / nginx）
```


## 快速开始

### 前端本地开发

```bash
cd frontend
npm install
npm run dev        # Vite 默认 http://localhost:5180/5181
```

`vite.config.js` 已把 `/api` 代理到 `http://127.0.0.1:8000`（可用环境变量覆盖），所以本地同时起后端即可联调；生产环境由 nginx 托管 `dist`。

### 后端本地开发

```bash
cd backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .                                  # 依赖见 pyproject.toml
cp ../.env.example .env                           # 改成自己的连接串/密钥
alembic upgrade head
uvicorn app.main:app --reload --port 8000
pytest                                            # 可选：跑测试
```

### 整栈（Docker Compose）

```bash
# 1) 构建后端镜像（仓库已含 backend/Dockerfile）
docker build -t kgqa-backend:latest backend/

# 2) 修改 docker-compose.yml 中所有 CHANGE_ME
#    MYSQL_PASSWORD / MYSQL_ROOT_PASSWORD / NEO4J_AUTH / NEO4J_PASSWORD / APP_SECRET_KEY / JWT_SECRET

# 3) 启动
docker compose up -d

# 前端也可用镜像方式：cd frontend && docker build -t kgqa-frontend:latest .
```

- 站点：`https://<你的域名>/`，健康检查 `GET /health` → `{"status":"ok","service":"kg-qa-ie"}`
- 首次启动自动建表；`data/`（wiki / cache / chroma / uploads）需要另行准备或从备份恢复
- TLS：nginx 配置里使用 certbot（Let's Encrypt）证书，请替换为自己的域名与证书路径

## 配置（关键环境变量）

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | MySQL 连接串，如 `mysql+pymysql://user:pass@mysql:3306/kg_qa_ie?charset=utf8mb4` |
| `APP_SECRET_KEY` | 应用密钥（与 JWT 无关） |
| `JWT_SECRET` / `JWT_ALGORITHM` / `JWT_ACCESS_TTL_MIN` / `JWT_REFRESH_TTL_DAYS` | 登录令牌配置 |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | 图谱连接 |
| `CHROMA_HOST` / `CHROMA_PORT` / `CHROMA_PERSIST_DIR` | 向量库连接与持久化目录 |
| `WIKI_ROOT` / `CACHE_DIR` | Wiki 内容与缓存目录 |
| `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_MODEL` / `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` | 大模型配置 |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` | 向量模型配置（`BAAI/bge-m3` / 1024） |
| `CORS_ORIGINS` | 允许的前端来源 |

> 模型 Key、抓取服务 Key、生图 Key 等建议在管理后台写入（加密存储），或通过环境变量注入，切勿提交到仓库。

## 部署与运维要点

- **增量部署**：生产更新用「`docker commit` 快照为 base 镜像 → `docker cp` / `COPY` 改动文件 → `compose up -d --no-build`」，不要全量重建（会重跑 apt/pip，构建上下文大时容易卡死）。
- `docker cp` 的改动在容器重建后丢失，必须 `docker commit` 覆盖 `latest`。
- Chroma 客户端与服务端版本必须一致（0.5.15），否则向量检索会异常。
- Neo4j 需要 APOC 与 GDS 插件；内存按容器限制调 `heap` 与 `pagecache`。
- 证书续期后如 `nginx -s reload` 不生效，可 `docker compose restart nginx`。

## 说明

本仓库是知识库门户的完整源码（含前后端、部署配置与文档），并保留了生产环境相关的运维材料：

- `backend/`：后端完整源码，含 `Dockerfile`、`migrations/`、`scripts/`、`tests/`，可直接本地运行或构建镜像。
- `frontend/`：门户前端 Vue 3 源码，本地 `npm run dev` 可联调，生产由 nginx 托管 `npm run build` 产物。
- `deploy/`：多种 compose 编排与 nginx 配置；`docs/`：设计与运维文档；`tools/`、`incremental/`：生产环境历史运维脚本与增量补丁（参考用）。
- 数据目录、数据库导出、TLS 证书、备份包与真实凭据均已排除；`docker-compose.yml`、`deploy/*`、`config.py`、`.env.example` 中的敏感值统一替换为 `CHANGE_ME`。

## 许可证

[MIT](LICENSE) © 2026 siisjsjs
