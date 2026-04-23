# Proposal

## Layer 4 AI Wiki 知识编译层提案

本轮不是直接做一个通用 Obsidian 风格 wiki，而是先把现有历史方案库编译成一层稳定、可积累、可回写的领域知识资产。

第一版目标：

- 从现有 `outline_library.json` / `block_library.json` 提取结构化知识
- 生成本地 wiki 目录与索引
- 先沉淀四类核心资产：
  - 术语表
  - 设备卡
  - 接口卡
  - 章节模板

同时补一个最低限度的规范页：

- 禁用表述

约束：

- 本轮不引入数据库 schema 变更
- 本轮不依赖外部搜索引擎或 GraphRAG
- 本轮以本地 markdown + json manifest 为主，保证后续可被 LLM 和脚本双向消费
