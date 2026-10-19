# Ingest 分类 / 虚空链接根治改造方案

> 日期:2026-06-06
> 目标:让 MD 文档导入后,**每一个实体节点都被正确分类到主题**、**零虚空链接**、**可持续投料丰富内容**,从源头根治反复出现的「无主题分组」「虚空链接」「孤儿实体」问题。

---

## 一、背景与问题现象

知识库结构:`书(book) → 章节(source) → 主题(topic) → 实体(entity)`。
topic / entity 不在页面上存 book,运行时从 `frontmatter.sources[0]` 对应的 source 继承。

### 本轮触发的具体问题(doc7《工业工程概述.md》实测)

- step1 抽出 **41 实体 + 6 主题 + 25 关系**,这 41 个实体全部被 6 个主题覆盖,**零孤儿**。
- 但落盘后 `wiki/entities/` 实际有 **53 个** .md,多出 **12 个**:
  `ECRS、ISO 9001、VDA6.3、传统工厂、动作分析、大批量生产、敏捷制造、柔性制造技术(FMT)、柔性制造系统、现场管理、系统思维、鱼骨图`
- 这 12 个:**没有 sources 字段**、**未被任何 topic 的 related_entities 覆盖** → 既归不进章节、也进不了主题 → 掉进空的「未分类」书。
- 本质:这是「无主题分组」问题的新变种,只是换了个地方冒出来。

---

## 二、根因分析(读码确认)

### 根因 1:step2 prompt 允许「清单外引用」→ 制造虚空链接

- `prompts/ingest_step2_entity.md:87-89` 让 LLM 写 `[[相关概念1]]`、`[[相关概念2]]`,可自由发挥到 step1 清单之外。
- `prompts/ingest_step2_topic.md:88` 让 LLM 写 `[[相关主题1]]`,同样不受清单约束。
- LLM 写出 `[[ECRS]]`、`[[鱼骨图]]` 等清单外双链 → 这些目标页不存在 → 虚空链接。

### 根因 2:虚空链接兜底建页(`fill_void_entity`)漏写 sources、不走 merger

- `services/lint/void_link_cleaner.py:219-284` 的 `fill_void_entity`:LLM 凭上下文建实体页。
- 注入 frontmatter 时(:270-277)只写 `slug/title/kind/auto_filled/confidence/version/created/updated`,**唯独漏了 `sources`** → 这是 12 个孤儿无 source 的直接根因。
- 且**没走 merger 判定**:不检查是否该合并到已有实体,直接新建 → 可能造重复孤儿。

### 根因 3:孤儿分类口径「按 source 找」,扫不到无 source 实体

- `services/wiki/orphan_topic.py` 的 `collect_orphan_work` **按 source 章节遍历**,只能看到「引用了某 source 的实体」。
- 那 12 个连 source 都没有 → 根本不在遍历范围内 → 漏分类。
- 且现有 `classify_orphan_entities` 在 pipeline 里跑在 `ensure_zero_void_links` **之前**(`pipeline.py:1124` 早于 `:1151`),void_fill 新造的实体分类时还不存在。

### 根因 4(跨书场景):`slug2book` 取 `sources[0]`,多源实体归错/漏书

- `services/wiki/book_tree.py:108` 实体的 book 取 `fm.sources[0]` 对应 source 的 book。
- **书内目录树**用的是 `source_to_refs`(遍历 sources **全集**反查),已支持多章节露出,**无偏见**。
- **跨书归属/统计**(`build_book_index` 的 `slug2book`)取 `sources[0]` 单值 → 被多本书引用的实体只算第一本,其他书的目录树漏了它。

---

## 三、底层数据已支持多源(无需新建机制)

读码确认两个利好,解法可「利用现有机制」而非「另起炉灶」:

1. `sources` 是**数组**,不是单值。
2. 跨素材合并时 `frontmatter_io.merge_frontmatter`(:39 + :114-120)对 `sources` 等字段做 **union 去重累加**,后到的 source 追加而非覆盖。
3. `merge_or_create_entity`(`ingestion/merger.py:101`)已实现完整分支:①字面 slug 命中 → 合并 ②schema 别名命中(含同义词) → 合并 ③embedding 余弦相似 → 合并/建议 ④否则新建。step2 正式实体已走此路;只有 `fill_void_entity` 没走。

**结论**:多源数据结构早已就位,病在「消费端取 sources[0]」和「兜底建页绕过既有机制」。

---

## 四、改造方案(三道防线 + 跨书修正)

设计哲学:从「事后打扫」转为 **源头约束(防线1) + 命中即合并(防线2) + 全量兜底分类(防线3)**,任何环节冒出来的实体都跑不掉。

> 重要认知:**「零虚空」最终靠 `ensure_zero_void_links` 这道确定性兜底保证**(要么 fill 成页、要么 strip 成纯文本)。源头约束(防线1)是把待兜底数量从几十压到个位数,**不能单独依赖**。

### 关键决策(已与用户确认)

- **清单外概念只写纯文本**:step2 双链 `[[X]]` 的 X 必须来自给定清单,清单外一律纯文本、不加 `[[]]`(最严格)。
- **兜底实体继承引用方 source**:`sources` 用 union 追加引用它的页面的 source,记全集。
- **多源实体每个章节都露出**:不做「选一个」的妥协;`slug2book` 改多值,实体在每本引用它的书的对应章节都露出。

---

## 五、改造任务清单(7 项,按序执行)

### Task 1 — step2 prompt 加白名单约束
- 文件:`prompts/ingest_step2_entity.md`、`prompts/ingest_step2_topic.md`
- 改:双链 `[[X]]` 的 X **必须**来自给定实体名/主题名清单;清单外概念**只写纯文本,不加 `[[]]`**。
- entity prompt 的 `:87-89`、topic prompt 的 `:88` 改成「只能从清单选,选不到就纯文本」。

### Task 2 — 给 entity prompt 注入可引用实体白名单
- topic prompt 已有 `entity_names_block`(`:23`);entity prompt 缺。
- 改:`step2_compose.py` 的 step2b 调用处把 `all_entity_names` 也传给 entity prompt,`ingest_step2_entity.md` 加占位符 `{entity_names_block}`。

### Task 3 — `fill_void_entity` 走 merger + 写 sources
- 文件:`services/lint/void_link_cleaner.py:219-284`
- 改:(1) 建页前先过 `merge_or_create_entity` 判定,命中已有实体则不新建、改为归到已命中实体;(2) 真要新建时**强制写 `sources`**,值继承「引用此 slug 的页面」的 source(用 union)。

### Task 4 — 新增全量「按 topic 覆盖」的兜底分类
- 文件:`services/wiki/orphan_topic.py`
- 改:新增 `classify_all_unclassified_entities`,遍历 `wiki/entities/` **所有**实体页,凡未被任何 topic 的 `related_entities` 覆盖的 → 待分类。复用三层逻辑(LLM 归入已有主题 → 合成新主题 → 相似度兜底)。判定不依赖 sources,无论实体从哪冒出来都能抓到。分类时给无 source 实体补 `sources`。

### Task 5 — 调整 pipeline 尾段执行顺序
- 文件:`services/ingestion/pipeline.py:1078-`(`_post_post_process`)
- 改:把全量分类挪到 `ensure_zero_void_links` **之后**,保证分类时实体全集已定型。
- 新顺序:别名归一化 → `ensure_zero_void_links` → `classify_all_unclassified_entities` → 重建 `graph-data.json`。

### Task 6 — `slug2book` 多值化(跨书露出)
- 文件:`services/wiki/book_tree.py`
- 改:`_slug_to_book_map` 的 value 从单 `book` 改成 `books` 集合(遍历实体 `sources` 全集,收每个 source 对应的 book);`build_book_index` 统计时实体对所属每本书各计一次。书内 `build_book_tree` 已用 `source_to_refs` 全集反查,无需改输出结构、不碰前端。

### Task 7 — 清库重跑端到端验证
- 改完先 import 自检 + 跑相关单测。
- 清库(`scripts/wipe_all_ingested.py`)+ 重传《工业工程概述.md》跑完整 ingest。
- **此步须等用户确认后执行,不擅自清库。**

---

## 六、验收标准(逐项核对)

| # | 验收项 | 期望 |
|---|--------|------|
| 1 | ingest 全阶段状态 | 全部 `succeeded` |
| 2 | book_tree `orphan_entities` | 每章节 = 0 |
| 3 | `wiki/entities/` 每个实体 | 都被某 topic 覆盖 + 都有 `sources` 字段 |
| 4 | 「未分类」空书 / 「未归类·综合」垃圾页 | 不存在 |
| 5 | 虚空链接(`scan_void_links`) | after = 0 |
| 6 | 多源实体跨书露出 | 被多书引用的实体在每本书目录树都出现 |
| 7 | 可持续投料 | 同名实体再次 ingest 时叠加观点,不重建、不覆盖 |

---

## 七、已知遗留 / 接受的代价

- **兜底分类「分对」无强保证**:相似度兜底(字符 bigram Jaccard)只保证「不留孤儿」,LLM 失败时可能挂错主题且沉默。auto_filled 页需人工抽检。
- **成本与耗时上升**:void_fill 走 merger 增加 embedding 调用;全量分类多一轮 LLM。文档越大越慢。
- **auto_filled 实体质量偏低**:`confidence: low`,凭上下文猜写,数量涨但质量参差,需后续投料补充。
- **方案性质**:工程上的「趋零 + 兜底」,非理论意义的「零」。天花板在 LLM 分类准确率,不在流程结构。

---

## 八、不改动的部分(确认保留)

- `auto_fill_from_step1`(填 `.wiki-schema.md` 别名 / 关系总览)—— 现有功能正常(本轮 `new_aliases:35`),保留。
- 跨素材叠加(`apply_observation_block` / `distill_observation`)—— 持续投料丰富内容的核心,保留。
- 前端展示、`build_book_tree` 输出结构、orphan 判定基础逻辑 —— 不动。
