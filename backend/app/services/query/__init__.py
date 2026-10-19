"""query 服务：wiki 检索 + LLM 综合 + 评分。

子模块：
  alias_expand  — 用户问题关键词的别名展开
  grep          — 在 wiki/ 下全文搜
  page_reader   — 读候选页（>2000 字时只读 frontmatter + 前 500 字 + 命中段前后 3 行）
  composer      — 组装 prompt + LLM 流式综合 + 引用
  scoring       — answer-quality 评分 + tier + gap_flag
  persistor     — 重复检测 + 写 wiki/queries/{date}-{hash}.md
"""
