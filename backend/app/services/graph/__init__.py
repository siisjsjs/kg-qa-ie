"""graph 服务：双链扫描 + 社区发现 + insights + Neo4j 镜像。

子模块：
  builder       — 扫 [[X]] 双链 → 边表 → Louvain 社区
  insights      — 规则 insights：surprising_link / bridge_node / knowledge_gap / sparse_community
  neo4j_client  — Neo4j 驱动封装（限流 + 重试）
"""
