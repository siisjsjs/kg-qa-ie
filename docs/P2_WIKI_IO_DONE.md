# P2.1 wiki 基础 I/O 完成报告

> 完成日期:2026-05-19
> 阶段:P2 主链路第一块
> 上一份报告:[P1_DONE.md](./P1_DONE.md) / [FRONTEND_REUSE_DONE.md](./FRONTEND_REUSE_DONE.md)
> 主规划:[工业工程知识库问答系统 — 技术规划文档.md](./工业工程知识库问答系统%20—%20技术规划文档.md) v1.3

## 摘要

P2 启动后第一块增量。把 ingestion / query / graph / delete 共同依赖的 wiki 基础 I/O 四个模块从骨架填实,并补齐对应单元测试。

**填实模块**:
- `services/wiki/fs_io.py` — safe_join 防越界 / slugify(中文保留 + ASCII 优化) / atomic_write 原子写
- `services/wiki/frontmatter_io.py` — parse_file / serialize 字段顺序稳定 / merge_frontmatter list union + version+1
- `services/wiki/cache.py` — sha256 hash / record / is_hit / evict / evict_by_source_slug
- `services/wiki/link_resolver.py` — extract_links + 关系/置信度注释解析 / rebuild_links_index / find_backlinks / find_broken_links / autolink_entities / aggregate_edges

**测试**:`pytest tests/unit/` 共 **58 项全绿**(P1 既有 26 项 + 新增 32 项),`<2 秒`完成。

---

## 1. 实际交付清单

### 1.1 `services/wiki/fs_io.py` ✅

| 函数 | 行为 |
|------|------|
| `safe_join(root, *parts)` | 拼路径并 `.resolve()` 后校验不出 root;拒绝绝对路径 part;违规抛 `ValueError` |
| `slugify(title, max_len=80)` | NFC 归一化 → 去禁用字符(`\x00-\x1f<>:"/\|?*`) → 空白折叠为 `-` → 纯 ASCII 走 `python-slugify`,中文保留;空串抛 `ValueError` |
| `atomic_write(path, content)` | tmp + `os.replace` 原子写,父目录自动创建,UTF-8 + LF;`os.fsync` 确保落盘;失败清理 tmp |
| `read_text(path)` | UTF-8 文本读 |

### 1.2 `services/wiki/frontmatter_io.py` ✅

| 函数 | 行为 |
|------|------|
| `parse_file(path) / parse_text(text)` | python-frontmatter 包装,返回 `(dict, body)` |
| `serialize(fm, body)` | YAML 字段按 `_PREFERRED_ORDER`(title→slug→kind→...→updated)+ 字典序;body 末尾恰好一个 `\n` |
| `merge_frontmatter(old, new, *, bump_version=True)` | list 字段(aliases/tags/domains/sources/followups)去重 union;scalar 字段 new 优先;version+1;updated 总刷新;created 仅老的为空才填 |

### 1.3 `services/wiki/cache.py` ✅

| 函数 | 行为 |
|------|------|
| `compute_hash(bytes\|str)` | sha256 hex(64 字符) |
| `is_hit(wiki_root, hash)` | 返回旧记录或 `None` |
| `record(...)` | 写入 `.wiki-cache.json`(原子保存,带 `ingested_at` 时间戳) |
| `evict(wiki_root, hash)` | 单条删除,返回是否实际删除 |
| `evict_by_source_slug(wiki_root, slug)` | 批量删除指向某 source_slug 的所有缓存条目;为 delete 流程铺路 |

文件级 `threading.Lock` 保证并发安全;`json.JSONDecodeError` 时降级为空缓存(不阻塞主流程)。

### 1.4 `services/wiki/link_resolver.py` ✅

| 函数 | 行为 |
|------|------|
| `extract_links(body)` | `[[X]]` / `[[X\|alias]]` → `[(target, alias_or_None), ...]` |
| `extract_links_with_meta(body)` | 同上 + 紧随其后的 `<!-- type: 实现 -->` / `<!-- confidence: EXTRACTED -->` 注释解析;同 src 内重复引用合并 occurrences |
| `iter_wiki_pages(wiki_root)` | 枚举 `wiki/**/*.md`(不含 raw/、不含元文件) |
| `rebuild_links_index(wiki_root)` | 全量扫,产出 `[{src_slug, dst_slug, link_type, occurrences, confidence}, ...]`(供 MySQL `wiki_links` 同步) |
| `find_backlinks(slug, wiki_root)` | 反查引用本 slug 的源页 slug 列表(去重保序) |
| `find_broken_links(wiki_root)` | 找出指向不存在页面的边 |
| `autolink_entities(body, *, alias_groups)` | 把首次出现的实体名包成 `[[canonical]]`;长串优先 + 跳过已有 `[[...]]`(用 `\x00WL{n}\x00` placeholder 占位) |
| `slugs_referenced_by_source(source_slug, ...)` | 找出 frontmatter.sources 含该 slug 的所有页面;为 delete 级联清理铺路 |
| `aggregate_edges(edges)` | 按 `(src, dst, link_type)` 聚合,occurrences 求和,首次 confidence 优先 |

---

## 2. 单元测试结果

```
$ pytest tests/unit/ -x -q
..........................................................     [100%]
58 passed in 1.14s
```

| 文件 | 用例数 | 覆盖点 |
|------|-------|------|
| `test_fs_io.py`(新) | 9 | safe_join 越界/绝对路径拒绝 / slugify 中文/ASCII/禁用字符/空串/max_len / atomic_write 同步异步往返 + 覆盖写 |
| `test_frontmatter_io.py`(新) | 7 | parse 往返 / 字段顺序稳定 / 中文 unicode 不损 / merge list union + 不 bump |
| `test_cache.py`(新) | 5 | hash 一致性 / 增删查 / by_source_slug 批量 / 损坏 JSON 降级 |
| `test_link_resolver.py`(新) | 11 | 简单/带 meta 抽取 / occurrences 聚合 / index 全量 / backlinks / broken_links / autolink 首次/避重/长串优先 / 按 source 找页 / aggregate |
| `test_admin_config.py` 等 P1 既有 | 26 | P1 验收基线,无回归 |
| **合计** | **58** | |

---

## 3. 设计要点

### 3.1 slugify 的中英文双轨
中文标题(如"价值流图")保持原样,只去禁用字符 + 折叠空白;ASCII 标题走 python-slugify 得到规范化的 dash 风格(如 "Value Stream Mapping" → "Value-Stream-Mapping")。这样 `wiki/entities/` 下的中英文文件名都自然可读。

### 3.2 frontmatter 字段顺序稳定
`_PREFERRED_ORDER` 把核心字段(title / slug / kind / tags / sources / version / created / updated 等)排前,其他按字典序排后。同一个 wiki 页 ingest N 次后 git diff 不会有"字段乱序"的噪声。

### 3.3 autolink 的 placeholder 占位法
为了不重复包 `[[[[X]]]]`,先把已存在的 `[[...]]` 替换成 `\x00WL{n}\x00`,做完自动补链后再还原。比正则前瞻更可靠,且对嵌套 [] 友好。长串优先排序避免"价值流图"被"价值"先匹配。

### 3.4 link_resolver 与 ingest 的关系
`rebuild_links_index` 产出的边数据是 MySQL `wiki_links` 表的来源,也是 Neo4j 同步的源头。`extract_links_with_meta` 的注释格式与 ingest Step2 模板里写出 `[[X]] <!-- type: 实现 -->` 的约定对齐。

### 3.5 缓存的并发与降级
`threading.Lock` 保护 JSON 读写;损坏的 cache 文件不抛异常,降级为空缓存(因为 cache 命中只是性能优化,失败时多调一次 LLM 不影响正确性)。

---

## 4. 与主规划的对齐

| 规划条目 | 实现位置 | 备注 |
|---------|---------|------|
| §2.3.1 文件系统结构 | `init.py`(P1) + `fs_io.safe_join` | 所有写操作都过 safe_join |
| §3.1 ingest 流水线"缓存检查在最前" | `cache.is_hit / record` | hash 命中跳过 LLM |
| §3.1.2 Step2 输出契约 | `frontmatter_io.merge_frontmatter` | list union + version+1 |
| §3.2 跨素材源合并 | `frontmatter_io.merge_frontmatter` | sources 字段 union |
| §3.3 query 流程 step 4 grep | `link_resolver.iter_wiki_pages` | 枚举 wiki 页面 |
| §3.5 graph 流程 step 1 扫 [[X]] | `link_resolver.rebuild_links_index` | 边数据来源 |
| `wiki_links` 表 | `link_resolver.aggregate_edges` | 同步前的去重聚合 |
| R14 文件并发锁 | `cache.py` 内 `threading.Lock` | 单进程内并发安全;P3 起加文件级 advisory lock |

---

## 5. 未交付项(归到下一轮)

P2 任务清单中,除本份外其余仍待做:

- `services/wiki/delete.py` 真实现(依赖本份的 `slugs_referenced_by_source` + `cache.evict_by_source_slug`)— 可放到 P3 与 batch-ingest 一起做,也可现在做
- `services/ingestion/*.py` 五阶段流水线全链路(下一阶段重头戏)
- `services/query/*.py` SSE 主链路
- `services/graph/builder.py` + Neo4j client
- `services/admin/{schema_editor, purpose_editor}.py`
- `auth.py` 真实现 + RBAC 启用
- 三个 LLM provider(OpenAI 兼容 / Ollama / Anthropic) — 按用户决策,**P2 优先 OpenAI 兼容,Ollama / Anthropic 推到 P3+**
- 四个 Embedding provider — 按用户决策,**P2 优先 OpenAI 兼容 embedding(硅基流动),其他推到 P3+**
- MySQL 容器实启验证

---

## 6. 验证命令

```bash
cd D:/kg-qa-ie/backend
pytest tests/unit/ -x -q
# 期望:58 passed in <2s
```

新增依赖检查:`pyproject.toml` 中已声明 `python-slugify>=8.0.4`,本机环境若缺失:
```bash
pip install python-slugify
```

---

## 7. 工时

约 1 小时:4 个模块真实现 + 4 个测试文件 + 跑通修复(只有 1 处依赖未装)。
