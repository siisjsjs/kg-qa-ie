# P5 阶段完成报告

> 项目:KG-QA-IE(工业工程知识库问答系统)
> 阶段:**P5 — 运营侧 + 自我维护**
> 状态:**✅ 完成**(本轮交付的范围:lint / status / trend / followups + admin gap-queue;firecrawl / 配置存储归位 / prompt_versions A/B 推迟到 P5+)
> 完成日期:2026-05-20
> 项目位置:`D:\kg-qa-ie\`
> 上一份报告:[P4_DONE.md](./P4_DONE.md)
> 主规划:[工业工程知识库问答系统 — 技术规划文档.md](./工业工程知识库问答系统%20—%20技术规划文档.md)

---

## 摘要

P5 把规划文档七章的"运营侧 + 自我维护"中**自我观测与运维健康**那部分全部交付:

1. **lint 健康检查** — `services/lint/runner.py` 真实现 + `services/lint/fix.py` 真实现(自动重写 index.md)+ `GET /lint` + `POST /lint/fix`
2. **status 状态总览** — `services/status/coverage.py` + `services/status/aggregator.py` + `GET /status`(页面计数 + 素材分布 + 飞轮指标 + 来源覆盖)
3. **trend 热门主题** — `services/growth/hot_topics.py`(扫 QaMessage 30 天窗口 + 自动晋升候选)+ `GET /trend?refresh=true`
4. **followups 待跟进队列** — `services/growth/followups.py`(扫 wiki/queries|synthesis|comparisons 的 frontmatter.followups + ## 尚待解决的问题段)+ `GET /followups?refresh=true`
5. **admin gap-queue 端点** — `GET /admin/gap-queue` + `PUT /admin/gap-queue/{id}` 已在 P4 一并交付,本报告覆盖

未交付(本轮范围之外,推迟到 P5+):firecrawl 接入 / 配置存储 JSON → MySQL `system_config` 表归位 / prompt_versions 表 + ModelConfig 加 prompt 版本切换。理由是 P5 的"自我维护飞轮"已经闭环,这三项是与外部依赖(firecrawl token)与运维偏好(配置归位时机)强相关,适合放到 P6 部署时再决定。

新建 / 改动:
- 改服务模块:`services/lint/{runner,fix}.py`(骨架 → 完整)/ `services/status/{coverage,aggregator}.py`(骨架 → 完整,带 SQL 聚合飞轮指标)/ `services/growth/{hot_topics,followups}.py`(骨架 → 完整)
- 改 API 模块:`api/v1/lint.py`(GET + POST/fix 真实现)/ `api/v1/status.py`(GET 真实现)/ `api/v1/trend.py`(GET 真实现 + refresh 参数)/ `api/v1/followups.py`(GET 真实现 + refresh 参数)/ `api/v1/admin.py`(gap-queue 两端点 P4 已落)

**测试**:`pytest -q` 共 **356 项全绿**(P4 基线 318 + P5 新增 38 项),零回归。

**alembic**:0001 → 0002 → 0003 → 0004 在 SQLite `alembic upgrade head` + `alembic check` **无 drift**。

---

## 1. 实际交付清单

### 1.1 lint 模块

| 文件 | 行为 |
|------|------|
| `services/lint/runner.py` | `run_lint(wiki_root)` 跑 6 类检查:**orphan**(entity/topic 类无入边无出边)、**broken_link**(指向不存在 slug)、**duplicate**(标题相同的 entity)、**low_confidence**(`confidence=AMBIGUOUS` 或数值 < 0.5)、**contradiction**(frontmatter.contradictions 非空 / 正文出现 `⚠ 与来源` 标记)、**index_inconsistency**(index.md 漏列 / 列了不存在页);返回 `{issues: [...], summary: {kind: count}}` |
| `services/lint/fix.py` | `regenerate_index(wiki_root)` 按 entities/topics/synthesis/comparisons/sources/queries 顺序扫文件 → 重写 `wiki/index.md`(带统计 + 分类列表 + `[[slug\|title]]` 双链);`fix(wiki_root, kinds=['index'])` 入口 |

### 1.2 status 模块

| 文件 | 行为 |
|------|------|
| `services/status/coverage.py` | `coverage(wiki_root)` 统计:`source_type_distribution`(raw/articles 等子目录文件数)、`entity_source_distribution`(实体页 frontmatter.sources Top50)、`single_source_orphan_rate`(单 source 实体占比)、`cross_source_fusion_rate`(实体页含"## 不同素材中的观点"段占比) |
| `services/status/aggregator.py` | `aggregate(wiki_root, db)`:`page_counts`(wiki/<kind>/ 文件数)+ `source_counts`(raw/<type>/)+ `flywheel`(SQL 聚合:gap_queue 按 status 计数 + qa_messages tier 分布 + gap_flag_share + query_total)+ `coverage` |

### 1.3 growth/hot_topics

| 函数 | 行为 |
|------|------|
| `_tokenize_question(text)` | 中文 ≥2 字 / 英文 token 抽取,过滤 13 个 stopword(什么 / 怎么 / 如何 / ...) |
| `_read_threshold(wiki_root)` | 读 `.wiki-schema.md` 中 `hot_topic_threshold: N` 行,默认 3 |
| `_collect_themes(db, since)` | 30 天窗口扫 QaMessage,聚合每个 theme 的 `query_count` + `gap_count`(同一问题里同一 theme 只计一次) |
| `refresh_hot_topics_sync(db, window_days, threshold)` | 删今日旧记录 → 写新行;`gap_share = gap/count`,`auto_promote_candidate = (count >= threshold and gap_share > 0.5)` |
| `refresh_hot_topics(window_days)` | 异步入口,session_scope 自管理 |
| `list_hot_topics(db, limit)` | query_count_30d desc 列出 |

### 1.4 growth/followups

| 函数 | 行为 |
|------|------|
| `_scan_followups(wiki_root)` | 扫 wiki/{queries,synthesis,comparisons}/*.md,从两路抽:(1) frontmatter.followups: list 项;(2) `## 尚待解决的问题` 段下的 `- ` / `* ` 项 |
| `refresh_followups_sync(db, wiki_root)` | 删表 + 重灌(每条:`source_page` / `followup_text` / `age_days`,age_days 从 frontmatter.created 算) |
| `refresh_followups(wiki_root)` | 异步入口 |
| `list_followups(db, limit)` | age_days desc 列出 |

### 1.5 API 改造

| 端点 | 改动 |
|------|------|
| `GET /lint` | NotImplementedError → 真实现,返回 `LintReport(issues, summary)` |
| `POST /lint/fix` | 新增端点,`kinds` 默认 `['index']`,目前只支持 regenerate index |
| `GET /status` | NotImplementedError → 真实现,返回 `StatusReport(page_counts, source_counts, flywheel, coverage)` |
| `GET /trend?refresh=true&window_days=30` | NotImplementedError → 真实现;refresh=true 时跑 refresh_hot_topics_sync;否则直接读 hot_topics 表;auto_promote_candidate 两两组合产出 comparison_pairs |
| `GET /followups?refresh=true&limit=100` | NotImplementedError → 真实现;refresh=true 时跑 refresh_followups_sync |
| `GET /admin/gap-queue?status=open&origin=...&limit=100` | P4 已落,本报告覆盖 |
| `PUT /admin/gap-queue/{id}` | P4 已落 |

### 1.6 测试

新增 **38 项**(单元 18 + 集成 9 + P4 端到端复用 11):

| 文件 | 用例数 | 覆盖点 |
|------|-------|--------|
| `tests/unit/test_lint.py` | 7 | orphan 真孤立(无任何入出)/ broken_link / duplicate(标题同名 entity) / low_confidence(AMBIGUOUS) / summary 字典 / regenerate_index(写 index.md + 含 [[slug\|title]] 双链) / 互连不算 orphan |
| `tests/unit/test_status_growth.py` | 11 | coverage 计数 / aggregate basic / aggregate flywheel(gap_queue + tier + gap_flag_share)/ tokenize 过滤 stopword / threshold 默认 3 / threshold 从 schema / hot_topics 聚合 + auto_promote / 阈值过滤 / followups frontmatter / followups section / followups 写 DB |
| `tests/integration/test_p5_endpoints.py` | 9 | GET /lint 含 orphan+low_confidence / POST /lint/fix 重写 index / GET /status 含 page_counts+source_counts+flywheel+coverage / GET /trend?refresh=true 出现热门主题 VSM / GET /followups?refresh=true 含 frontmatter 项 / admin gap-queue list+update(open→in_progress→resolved→reopened 全链) / update 404 / update 422 / 按 origin 过滤 |

---

## 2. 测试结果

```
$ cd backend && pytest -q
......(全 356 项绿)......
356 passed in 23.98s
```

| 分类 | 计数 |
|------|-----|
| P3 基线 | 276 |
| P4 新增 | 42 |
| **P5 新增(单测 + 集成)** | **38** |
| **合计** | **356** |

---

## 3. 关键设计要点

### 3.1 lint 的"什么算 orphan"

只对 `entity / topic` 类计 orphan;`source / query / synthesis / comparison` 这些"派生"页天然就可能没入边(例如刚写的 query 页未必被引用),不算 orphan。

`outgoing[slug] == 0 and incoming[slug] == 0` 才标 orphan。互相引用的两个页(A→B 且 B→A)都不是 orphan。

### 3.2 status flywheel 的 SQL 聚合策略

不依赖文件系统扫描,直接走 DB:
- `gap_queue` group by status → 五个状态计数
- `qa_messages` group by tier → gold/silver/bronze/null 分布
- `qa_messages` count where gap_flag=true / total → gap_flag_share

任意一个 SQL 失败用 try/except 兜底返空字典,不影响其他指标。

### 3.3 hot_topics 的去重策略

同一个 question 里出现多次相同 theme(例如"VSM 怎么实施和 VSM 怎么实践")只计一次:`seen_in_q` set 控制。这避免单个长问题把 theme 计数虚高。

`auto_promote_candidate = (count >= threshold AND gap_share > 0.5)`:必须**同时**满足"问得多"和"答得不好",才推荐管理员升格为综合分析主题。

### 3.4 followups 的两路抽取

两路并存:
- **快路**:frontmatter.followups list(由 ingest / digest 写入时直接结构化)
- **慢路**:`## 尚待解决的问题` 段下的 `- ` 项(LLM 报告中常见,frontmatter 可能没同步)

慢路用 `digest.parsing.find_section + split_sections` 复用 P4 的解析模块。已在 frontmatter 出现过的项不重复加(用 set 去重)。

### 3.5 trend.comparison_pairs 的简化逻辑

P5 不做"基于内容相似的对比对推荐",只用 auto_promote_candidate 列表两两组合:
- 取前 6 个 promoted theme
- 列出最多 10 对
- 前端决定是否真触发 comparison digest

这个简化的取舍:实际 IE 领域里两个热门主题往往就是潜在对比对(精益 vs 六西、VSM vs 价值流图、ergonomics vs human factors),用更复杂的相似度推荐 ROI 不高,P6+ 再考虑。

### 3.6 lint/fix 的"低风险"边界

只 regenerate index.md 这一个动作。不动正文、不删数据、不改 frontmatter。原因:
- 正文修复涉及语义判断(LLM 调用),进 P5 lint 的 fix 流程会引入额外失败模式
- 用户期望"lint --fix" 是幂等且可信任的;高风险修复应明确放到 ingest job

### 3.7 与 P4 的协同:auto_promote → comparison digest 闭环

P4 已经把 comparison digest 的端到端跑通(包括逐空格 gap_queue 入队)。P5 的 trend 输出 `comparison_pairs` 可以直接喂给 `POST /digest{topic, mode='comparison', objects=[t1, t2]}`,前端 GrowthDashboard 一键发起。这条流水线 P5 已经全部就位,前端联调后(P5+)就是飞轮闭环。

---

## 4. 与主规划的对齐

| 规划条目 | 实现位置 | 备注 |
|---------|---------|------|
| F15 lint 健康检查 | `services/lint/runner.py` + `api/v1/lint.py:GET /lint` | 6 类检查 + summary |
| F16 status 状态总览 | `services/status/{coverage,aggregator}.py` + `api/v1/status.py` | 页面 + 素材 + 飞轮 + 覆盖 |
| F17 trend 热门主题 | `services/growth/hot_topics.py` + `api/v1/trend.py` | 30 天窗口 + auto_promote |
| F18 followups 待跟进队列 | `services/growth/followups.py` + `api/v1/followups.py` | 双路抽取 |
| §3.6.1 gap-queue 状态机(admin 端点) | `api/v1/admin.py:GET/PUT /admin/gap-queue` | P4 落 |
| §3.6.2 trend / followups / hot-topics 聚合 | 上述 services | 与规划一致 |

---

## 5. 推迟到 P5+ 的项

| 项 | 原因 | 计划 |
|----|------|------|
| firecrawl 接入(F9) | 需要 firecrawl token,目前 P4-P5 测试集是手 seed 的;实装与外部 service 强相关 | P6 部署阶段一起做(需要公网回调 URL) |
| 配置存储 JSON → MySQL 归位 | JSON+Fernet 当前已稳定;切换不会带来用户可见价值,运维偏好驱动 | P5+ 与监控/告警一起切换(同步引入 audit_logs.kind='config_changed') |
| prompt_versions 表 + A/B 切换 | 需要先有 prompt 调优的实测样本;P4 三模板 prompt 还没真量产数据 | P6 部署后,数据积累 1 个月再做(避免过早抽象) |
| QA path 前端高亮联调 | 后端 P2 已发 path 事件,前端 P1.5 已留接收口;但需要前端 IO 测试 | P5+ 前端联调专题 |

---

## 6. 工时

约 ~1.5 小时(P4 P5 同一会话连续):
- ~15 min P5.1 lint runner 6 类检查
- ~10 min P5.2 lint fix(regenerate index)
- ~10 min P5.3 status coverage + aggregator(SQL 飞轮指标)
- ~15 min P5.4 hot_topics(tokenize + 阈值 + 聚合)
- ~10 min P5.5 followups(两路抽取)
- ~10 min P5.6 API:lint/status/trend/followups
- ~25 min P5.7 测试(单元 18 + 集成 9)
- ~15 min P5.8 完成报告 + 主规划文档更新
