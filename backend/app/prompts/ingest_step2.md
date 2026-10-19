# Step2:页面生成 + 跨 source 合并 prompt

你已完成 Step1 结构化分析。下面把结果落到 wiki/ 目录里的具体页面。

## 输入

### Step1 产出(JSON)

```json
{step1_json}
```

### 原始素材

```
{raw_md}
```

### 现有相关 wiki 页(用于合并;可能为空)

{related_pages_block}

## 输出格式

为下列每一类页面生成完整 markdown(含 frontmatter)。**多份页面之间用以下分隔符**:

```
=====<file: wiki/sources/{slug}.md>=====
（该页面 markdown 全文,含 frontmatter)
=====<file: wiki/entities/{slug}.md>=====
（…）
```

- **`=====<file: ...>=====`** 单独一行,前后必须各空一行
- 每份页面以 frontmatter `---\n...\n---` 开头
- 不要在分隔符之外添加任何解释文字

需要生成的页面:

1. **source 摘要页**:`wiki/sources/{date}-{title-slug}.md`(本次必须输出)
2. **新建实体页**:Step1 `new_vs_existing.new_entity_names` 中每一项一份 `wiki/entities/{name}.md`
3. **更新已有实体页**:Step1 `new_vs_existing.matched_entity_pages` 中每一项 — **不要重写整页**,只输出"## 不同素材中的观点 / ### 来源:[[{source_slug}]]" 段的新增内容,frontmatter 仅含 slug 字段(后端会做合并)
4. **新建/更新主题页**:同上规则

## 跨 source 合并规则(对 matched_entity_pages 必须遵守)

- 不重写 frontmatter / 不动 `## 简介` / `## 核心要点` 等已有段
- 只追加 `## 不同素材中的观点 / ### 来源:[[{source_slug}]]\n\n{新主张}`
- 主张冲突时(对应 Step1 contradictions),用 `> ⚠ 与来源 [[{other_slug}]] 的主张可能冲突` blockquote 标注

## 引用规范

- 提到其他实体写 `[[实体名]]`(双链)
- **引用格式铁律**:每条 `> 引自来源:[[{source_slug}]]:...` **必须独占一行、行首顶格写 `>`**,绝不能接在正文句尾(如 `…结束。 > 引自来源` 是错的);引用块与上方正文之间、多条引用之间都**必须空一行**;source 名**必须用 `[[ ]]` 双链**,不能写裸 slug。
- **关系类型注释**(可选):在 `[[X]]` 后紧跟 `<!-- type: 实现 --> <!-- confidence: EXTRACTED -->`,后端会同步到 wiki_links 表

## frontmatter 字段(参考 templates/entity.md / topic.md / source.md)

实体页:
```yaml
---
title: 工效学
slug: 工效学
kind: entity
aliases: ["人因工程", "ergonomics"]
tags: []
domains: ["人因工程"]
sources: ["{source_slug}"]
version: 1
confidence: 0.8
---
```

主题页:
```yaml
---
title: 工效学的研究范畴
slug: 工效学的研究范畴
kind: topic
tags: []
domains: ["人因工程"]
sources: ["{source_slug}"]
related_entities: ["工效学"]
version: 1
---
```
