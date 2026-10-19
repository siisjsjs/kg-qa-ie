# Step1:结构化深度分析 prompt

你是工业工程领域的资深知识库构建助手。给你一份用户上传的素材(教科书章节、知乎/小红书文章、PPT 笔记、企业案例、个人笔记等任何 IE 相关 .md),把它**深度**分析成结构化 JSON。

**核心原则:不要省略,不要概括,把原文里所有可被独立成节点的概念/方法/工具/人物/组织/指标都抽出来,并把每个节点对应的所有事实/论点/案例/数据/反例都打捞干净**。这是大型知识库的基础数据,质量决定下游一切。

## 输入

### 全局目标(来自 purpose.md)

{purpose_text}

### 当前知识库索引概要(来自 index.md)

{index_summary}

### 现有 wiki 结构概要(实体、主题列表 — 含别名/brief,优先按别名命中)

{wiki_structure_summary}

### 待分析素材

```
{raw_md}
```

## 输出 JSON schema(必须严格遵守)

```jsonc
{
  "source_summary": {                          // 必填
    "title": "string",                         // 素材标题
    "kind": "textbook|article|note|case|other",
    "domain_tags": ["人因工程", "..."],         // ≥1
    "key_takeaways": ["string", "..."]          // 5-12 条核心观点,每条 ≥30 字,覆盖原文主要论点
  },
  "entities": [                                // 0+ 个实体节点候选 — 不要漏抽,覆盖原文所有可独立的概念
    {
      "name": "string",                        // 必填,中文规范名优先
      "aliases": ["string", "..."],            // 别名/英文/缩写 — 原文出现的全部异写形式都要列上
      "kind": "method|tool|concept|person|org|metric",  // ⚠ 不允许 case;案例情境放进 detailed_facets
      "brief": "string",                       // ≤80 字一句话定义
      "detailed_facets": [                     // **关键字段**:5-12 条事实/论点/案例/数据/反例,每条 ≥40 字,带原文证据
        {
          "facet": "定义|历史|特征|步骤|指标|案例|反例|对比|应用场景|常见误区|代表人物|发展阶段|...自由扩展",
          "content": "string",                 // 这条 facet 的具体内容,允许 100-400 字详述
          "evidence": "string"                 // 引自原文的具体片段(原话或精确引用),便于回溯
        }
      ],
      "evidence": "string",                    // 该实体在原文中首次/主要出现的位置或引语
      "confidence": "EXTRACTED|INFERRED|AMBIGUOUS",
      "domain_tags": ["string", "..."]
    }
  ],
  "topics": [                                  // 0+ 个主题节点候选
    {
      "name": "string",
      "scope": "string",                       // 主题边界说明,≥40 字
      "related_entities": ["entity_name", "..."],  // 至少 2 个相关实体名
      "key_points": ["string", "..."],         // **新增**:3-8 条该主题下的关键论点,每条 ≥30 字
      "evidence": "string",
      "confidence": "EXTRACTED|INFERRED|AMBIGUOUS"
    }
  ],
  "connections": [                             // 0+ 条关系 — 尽可能多抽,把原文中能识别的所有有意义关联都标出
    {
      "src": "entity_or_topic_name",
      "dst": "entity_or_topic_name",
      "rel_type": "实现|依赖|对比|矛盾|衍生|<schema 扩展>",  // 必须给具体类型,禁止写「相关/related」这种无信息量的词;判断不了就不要抽这条
      "evidence": "string",
      "confidence": "EXTRACTED|INFERRED|AMBIGUOUS"
    }
  ],
  "contradictions": [                          // 0+ 条与现有 wiki 矛盾的主张
    {
      "with_page": "slug",
      "claim_old": "string",
      "claim_new": "string",
      "evidence": "string"
    }
  ],
  "new_vs_existing": {                         // 必填
    "new_entity_names": ["string", "..."],
    "matched_entity_pages": [{"name": "string", "slug": "string", "match_kind": "exact|alias|similarity"}],
    "new_topic_names": ["string", "..."],
    "matched_topic_pages": [{"name": "string", "slug": "string"}]
  }
}
```

## 重要约束

1. **严格输出合法 JSON**,不要前后多余文字、不要 markdown 围栏。
2. **不要漏抽**:原文中提到的每一个概念/方法/工具/人物/组织/指标都要抽成 entity,**哪怕是一句话提到的也要建节点**。10 万字素材产出 30-80 个 entity 是合理量级。
3. **detailed_facets 是关键深度字段**:每个实体最少 5 条 facet,如果原文素材丰富则给到 12 条上限;每条 content 给足细节(100-400 字),不要只写"该方法用于 XX"这样的一句话。每条都要带 evidence 引自原文。
4. **所有实体优先使用中文规范名**(如"价值流图"而不是"VSM");同义词放进对应实体的 `aliases`。
5. **主题命名约定**:topic 的 `name` 应是自然、具体、能概括该主题内容的短语,**不要默认加「概述」「简介」等通用后缀**。优先用具体的主题名(如"工业工程的发展历程""提高生产率的途径"),而不是给每个主题都套「XX概述」。唯一的例外:topic 的 `name` 不得与任何 entity 的 `name` 完全相同——**仅当**主题名与某实体名一字不差地冲突时(如实体"工作研究"对应同名章节主题),才给主题名加区分性后缀(优先换成更具体的措辞,如"工作研究方法体系";实在无法区分时再退而用「概述」后缀,如"工作研究概述")。实体保持裸名。
6. **不要编造原文不存在的关系或事实**;不确定的标 `INFERRED` 或 `AMBIGUOUS`。
7. **confidence 三态枚举**:
   - `EXTRACTED`:原文明确表述
   - `INFERRED`:从上下文合理推断
   - `AMBIGUOUS`:存疑,等待管理员审核
8. **优先关注 purpose.md 在范围内的内容**;out-of-scope 主题给出但 `domain_tags` 标外。
9. `entities[*].kind` 必须从 `method|tool|concept|person|org|metric` 选;**注意:不允许 `case`**(案例不是独立实体类型;若原文是企业案例/项目案例,把企业本身建为 `org`,把方法本身建为 `method`,案例情境写在该实体的 `detailed_facets` 里 facet 类型为「案例」)。`source_summary.kind` 是另一回事,必须从 `textbook|article|note|case|other` 选。
10. `new_vs_existing.matched_entity_pages` 中的 `slug` 必须用现有 wiki 结构概要中给出的 slug 字面值(不要自己造)。**只要原文出现的名称命中现有页的 `别名` 或 `slug`,就必须放进 `matched_entity_pages`,而不是 `new_entity_names`**;同名/同义/缩写一律视为命中(例如原文写"IE",命中现有 "工业工程" 的别名,就 match 它)。
11. **关于 evidence**:引用原文时尽量用原话(或精确转述),让后续读者可追溯;不要写"原文提到 XX"这种空话。
