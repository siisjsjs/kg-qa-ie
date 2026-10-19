# 结晶化 prompt

把用户提供的笔记/对话/灵感片段整理为一份结构化的 synthesis 页面。

## 用户文本
```
{text}
```

## 目标主题（可空）
{target_topic}

## 现有相关 wiki 节点（用于合并 / 引用）
{related_pages_summary}

## 输出格式

```markdown
---
title: {生成的标题}
slug: {生成的 slug}
kind: synthesis
tags: []
domains: []
sources: ["user_input"]
version: 1
created: {now}
updated: {now}
---

## 核心观点
（凝练的几条要点，每条带"出处：用户原文片段")

## 与已有页面的关系
- [[页面A]]：本笔记补充了 …
- [[页面B]]：本笔记可能与之矛盾 …

## 衍生问题
- 待跟进：……
- 待跟进：……
```

衍生问题会进入 frontmatter.followups。
