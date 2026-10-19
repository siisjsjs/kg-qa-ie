"""wiki 文件树管理。

子模块：
  init           — 初始化目录树 + .wiki-schema.md + purpose.md + index.md
  fs_io          — 原子读写、slug 命名规范
  frontmatter    — YAML frontmatter 解析与回写
  link_resolver  — [[X]] 双链解析、反链表构建、slug-redirect
  cache          — 内容 hash 缓存 (.wiki-cache.json)
  delete         — 删除素材级联清理
"""
