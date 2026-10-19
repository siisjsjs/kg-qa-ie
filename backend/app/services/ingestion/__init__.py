"""ingestion 流水线（5 阶段）。

子模块：
  md_parser      — 仅解析 frontmatter + 段落（不切章节）
  privacy_check  — 隐私自查清单
  source_writer  — 写 wiki/sources/{date}-{slug}.md
  step1_extract  — Step1：原文 → entities/topics/connections JSON
  step2_compose  — Step2：JSON → 页面（含跨 source 合并）
  merger         — 跨 source 节点合并算法
  validator      — Step1 JSON schema 校验
  triple_filter  — （可选）三元组黑白名单清洗
  pipeline       — 顶层 orchestrator
"""
