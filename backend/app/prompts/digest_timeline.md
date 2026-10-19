# 时间线 digest prompt

## 主题
{topic}

## 可用 context
{context_pages}

## 输出结构

```
## 时间线（{topic}）

```mermaid
gantt
    title {topic} 演进
    dateFormat  YYYY-MM-DD
    section 主线
    {事件A} :a1, {开始时间}, {结束时间}
    ...
```

## 事件说明
- {年份} {事件}：{背景与影响}（来源：[[页面X]]）
- ...

## 相关页面
- [[页面A]]
```

如果 context 没有 3 个以上事件，明确写 `> 时间线节点不足，建议补充以下方向：……` 触发 gap_flag。
