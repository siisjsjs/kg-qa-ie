# 深度报告 digest prompt

按以下 5 个二级标题组织报告，每段都要有内容（缺哪段就标 `> 待补充：……`，会自动入 gap-queue）：

```
## 背景概述
## 核心观点
## 不同视角对比
## 知识脉络
## 尚待解决的问题
```

## 主题
{topic}

## 可用 context
{context_pages}

## 末尾追加

```
## 相关页面
- [[页面A]]
- [[页面B]]
```

`## 尚待解决的问题` 段每条问题都会写入 frontmatter.followups，由 followups 队列追踪。
