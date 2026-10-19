"""digest 服务（深度报告 / 对比表 / 时间线）。

子模块：
  router       — 按触发关键词路由 deep_dive / comparison / timeline
  deep_dive    — 5 个 H2 模板（背景概述 / 核心观点 / 不同视角对比 / 知识脉络 / 尚待解决的问题）
  comparison   — IE 模式 6 维表格（核心观点 / 适用场景 / 量化指标 / 实施成本 / 典型陷阱 / 来源素材）
  timeline     — Mermaid gantt 或纯文字时间线
  scoring      — sections_missing / fill_rate
"""
