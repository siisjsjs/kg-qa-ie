---
title: 知识库主题
slug: schema
kind: schema
---

# .wiki-schema.md

## 主题
工业工程知识库

## 语言
中文

## 别名词表

> 在前端 SchemaEditor 中维护；同义词组之间不传递。

- 工效学 = 人因工程 = ergonomics = human factors
- 精益生产 = lean = lean production = TPS = 丰田生产方式
- 六西格玛 = 6 sigma = Six Sigma
- 价值流图 = VSM = value stream mapping
- 标准时间 = standard time = ST
- 作业测定 = work measurement
- 动作分析 = motion study = motion analysis
- 5S = 5S 管理 = 整理整顿清扫清洁素养
- DMAIC = define-measure-analyze-improve-control
- MRP = material requirements planning = 物料需求计划
- ERP = enterprise resource planning
- 工业工程 = IE = industrial engineering

## 关系类型词汇表

> 管理员可在前端扩展。Step1 抽连接时只允许使用本表中的类型。

- 实现：A 是 B 的具体方法（method-of）
- 依赖：A 需要 B 才能成立（depends-on）
- 对比：A 和 B 处于同一抽象层，可比较（compare-with）
- 矛盾：A 与 B 主张相反（contradicts）
- 衍生：A 是从 B 发展而来（derived-from）

## 素材类型路由

- articles → raw/articles/
- pdfs → raw/pdfs/
- tweets → raw/tweets/
- wechat → raw/wechat/
- xiaohongshu → raw/xiaohongshu/
- zhihu → raw/zhihu/
- plain_text → raw/plain_text/

## 成长配置

```yaml
hot_topic_threshold: 3            # 30 天内被问到 ≥N 次进入热门
answer_quality_gap_cutoff: 0.5    # score 低于此值入 gap-queue
auto_merge_cosine_threshold: 0.92 # 自动合并实体阈值
suggest_merge_cosine_threshold: 0.75  # 候选合并入 gap-queue 阈值
deep_dive_required_sections: 5    # 深度报告期望 H2 数
domain_ie: true                   # 启用 IE 领域风格调整
```
