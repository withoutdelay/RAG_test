# Tasks

## 1. Architecture

- [x] 1. 补齐 Layer 4 架构附录，明确 raw case library -> compiled wiki assets 的编译 contract

## 2. Backend

- [x] 2. 新增知识编译服务，基于 case library 构建术语表、设备卡、接口卡、章节模板和禁用表述
- [x] 3. 新增本地构建脚本，把 AI wiki 产物写入仓库工作区
- [x] 4. 输出 index / manifest / log，形成可持续维护的 wiki 基座

## 3. Testing

- [x] 5. 补充单测，覆盖知识页编译和索引生成

## 4. Verification

- [x] 6. 运行知识编译脚本并验证生成结果

## 5. Runtime Integration

- [x] 7. 新增 AI Wiki 上下文读取器，把 glossary / template / policy 组织成可注入写作提示的上下文块
- [x] 8. 把 AI Wiki 编译上下文接入 `SectionDraftService` 主写作链路，覆盖批量生成与单章节重生成
- [x] 9. 补充单测，验证知识上下文注入和缺失场景降级

## 6. Review Integration

- [x] 10. 把 AI Wiki 禁用表述和术语统一约束接入 `SectionQualityGateService`
- [x] 11. 在质检重写上下文和质检 LLM prompt 中注入 AI Wiki 约束
- [x] 12. 补充单测，验证禁用表述回扫、术语混用回扫和 rewrite 上下文注入

## 7. Retrieval Integration

- [x] 13. 把 AI Wiki glossary 命中的术语组接入 reuse retrieval query expansion
- [x] 14. 把 glossary 扩展词接入 reusable block 二次筛选与打分
- [x] 15. 补充单测，验证 query expansion 命中与主链路 trace

## 8. Ingestion Integration

- [x] 16. 历史方案文档上传、重解析、删除后，后台自动重建 case library 并刷新 AI Wiki
- [x] 17. 刷新时过滤旧的 uploaded_documents 条目，避免删除或重传后的脏数据残留在 case library / AI Wiki 中
- [x] 18. Documents 页面增加显式的 Historical Library / RFP 模式，并支持历史方案多文件批量导入
- [x] 19. 补充测试，验证历史方案刷新触发条件与 uploaded baseline 过滤逻辑
- [x] 20. 为历史方案库刷新补充状态文件、只读状态接口和 Documents 页可见提示

## 9. Product / Module Cards

- [x] 21. 在 AI Wiki 编译层新增产品族卡，把历史方案按方案族/产品族聚合成稳定知识页
- [x] 22. 在 AI Wiki 编译层新增模块卡，从 block library 中抽取高频模块与柜体部件知识
- [x] 23. 把产品族卡和模块卡接入 `KnowledgeWikiContextProvider`，让章节生成可直接消费
- [x] 24. 补充单测，验证产品族卡 / 模块卡编译结果与写作上下文注入

## 10. Retrieval Prior Integration

- [x] 25. 把产品族卡 / 模块卡输出为 retrieval prior bundle，而不只用于写作 prompt
- [x] 26. 在 reusable block 二次筛选与打分阶段加入产品族卡 / 模块卡先验特征
- [x] 27. 在 case library retrieval trace 中记录命中的产品族卡 / 模块卡，便于观察排序变化
- [x] 28. 补充单测，验证 retrieval prior bundle 与复用排序提升

## 11. Retrieval Prior Observability

- [x] 29. 为 selected prompt blocks 补充 AI Wiki prior score breakdown，显式记录每个先验加了多少分
- [x] 30. 在 composition trace / generation summary 中汇总 prior 命中块数与总 boost，便于后续调权重
- [x] 31. 在前端 trace 面板展示 AI Wiki terms/cards 与 prompt block prior boost

## 12. Holdout Eval

- [x] 32. 新增 holdout_eval 对比评测脚本，固定 `pilot_main` shortlist，对比开/关 AI Wiki prior 的 block-stage 排序差异
- [x] 33. 输出 markdown/json 报告，汇总 Top1/Top3 section_type 与 equipment_type 命中变化
- [x] 34. 补充单测，覆盖 holdout section 选择、查询构造、prior 排名统计与汇总
