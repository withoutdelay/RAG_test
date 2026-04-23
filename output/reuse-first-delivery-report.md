# Reuse-First 迭代交付说明

更新时间：2026-04-20

## 1. 本轮交付目标

本轮不是做前端包装，而是把 `reuse_first` 从“基于裸 chunk 的章节生成”推进到“章节优先、块级补充、可追溯”的可交付状态。

本轮完成后，系统已经具备：

- 稳定章节真值层
- section-first 检索与组装
- 模式切换与回退理由输出
- 前端审阅面板可见化

## 2. 已完成能力

### 2.1 解析与真值层

- `docling_parser.py` 在结构提示中输出 `section_catalog`
- 章节结构中补齐 `document_title`
- 章节目录可作为后续 chunk / asset 挂接的真实锚点

### 2.2 入库与元数据挂接

- `documents.py` 为 chunk 元数据补写 `source_section_id`、`section_path`、`source_heading`
- figure / table 等资产回写同样的章节锚点
- preserved table metadata 会继承章节来源信息

### 2.3 检索与组装链路

- `CaseLibraryService.retrieve_sections` 改为标题优先的 hybrid 排序
- 标题 exact / alias / family 权重提高
- 已命中具体叶子节时，宽父节进一步压分，减少父子节冗余竞争
- `retrieve_blocks` 调整为“章节 shortlist 内二阶段选择”，不再和 section 并列竞争
- block 检索文本加入章节路径等上下文，避免裸 chunk 命中
- `SectionCompositionService` 拆分 `title_intent`、`detail_intent`、`context_intent`
- 支持 `section-pack mode` 与 `full-section mode` 的运行时切换和 token budget 控制

### 2.4 Trace 与前端审阅

- 后端输出 `selected_sections`、`selected_blocks`、`selection_reason`、`token_budget`
- `selection_reason` 明确记录 mode、top section、budget、top score、lead、runner-up、aligned blocks 与 reasons
- 前端 [SectionBlock.tsx](/Volumes/thunder/code/RAG_test/frontend/src/components/editor/SectionBlock.tsx) 已可查看：
  - query intents
  - section candidates
  - selected sections / selected blocks
  - selection decision

## 3. 验证结果

### 3.1 后端

以下无数据库回归已通过：

```bash
cd backend && ../.venv/bin/python -m unittest \
  tests.test_document_api_helpers \
  tests.test_parsing \
  tests.test_build_case_library \
  tests.test_case_library \
  tests.test_case_retrieval \
  tests.test_composition_helpers
```

结果：`199/199` 通过。

根目录治理 smoke 已通过：

```bash
cd /Volumes/thunder/code/RAG_test && pytest -q --maxfail=1
```

结果：`2/2` 通过。

补充说明：

- 新增 `test_docling_parser_structure_hints_include_section_catalog`
- 新增章节锚点解析相关 helper 测试
- 已更新 `tests.test_api_phase2.Phase2ApiTests.test_document_upload_persists_figure_assets` 的断言，覆盖 figure asset / chunk metadata 的章节锚点透传
- 该 API 集成用例当前未能在本机复跑，原因是本地 PostgreSQL `localhost:55432` 不可达

### 3.2 前端

以下检查已通过：

```bash
cd frontend && npm run lint -- src/components/editor/SectionBlock.tsx src/lib/types.ts
cd frontend && npm run build
```

结果：lint 通过，build 通过。

## 4. 治理产物

本轮已生成或更新以下治理产物：

- [spec.md](/Volumes/thunder/code/RAG_test/.super-dev/changes/reuse-first/specs/reuse-first/spec.md)
- [plan.md](/Volumes/thunder/code/RAG_test/.super-dev/changes/reuse-first/plan.md)
- [checklist.md](/Volumes/thunder/code/RAG_test/.super-dev/changes/reuse-first/checklist.md)
- [tasks.md](/Volumes/thunder/code/RAG_test/.super-dev/changes/reuse-first/tasks.md)
- [reuse-first-prd.md](/Volumes/thunder/code/RAG_test/output/reuse-first-prd.md)
- [reuse-first-architecture.md](/Volumes/thunder/code/RAG_test/output/reuse-first-architecture.md)
- [reuse-first-uiux.md](/Volumes/thunder/code/RAG_test/output/reuse-first-uiux.md)
- [my-project-quality-gate.md](/Volumes/thunder/code/RAG_test/output/my-project-quality-gate.md)
- [RAG_test-proof-pack.md](/Volumes/thunder/code/RAG_test/output/RAG_test-proof-pack.md)
- [RAG_test-release-readiness.md](/Volumes/thunder/code/RAG_test/output/RAG_test-release-readiness.md)
- [governance-report-20260420.md](/Volumes/thunder/code/RAG_test/output/governance-report-20260420.md)

## 5. 当前未闭环项

当前治理状态：

- `super-dev spec quality reuse-first`：`100/100`
- `super-dev quality -t code`：`82/100`，已通过
- `super-dev release proof-pack`：`21/26`
- `super-dev release readiness`：`21/100`
- `super-dev quality -t redteam`：`56/100`

以下问题不阻塞本轮代码实现，但仍阻塞 Super Dev 的全局 release readiness：

- 缺少通用产品治理文档，如 `docs/QUICKSTART.md`、`docs/HOST_USAGE_GUIDE.md`
- 本地数据库未就绪，无法完成依赖 PostgreSQL 的 API 集成复跑
- redteam 仍未达阈值
- delivery manifest 与 rehearsal 证据尚未生成
- release readiness 仍关注整个仓库的产品化完备度，而不仅是本轮 `reuse_first` 代码改动

## 6. 结论

`reuse_first` 本轮 1-10 项任务已经完成，核心链路已从 chunk-first 收敛到 section-first，并具备可审阅的 trace 输出。

从代码实现角度，本轮已达到“可以进入下一阶段真实数据验证和局部灰度”的状态；从 Super Dev 全局交付角度，仍需补齐仓库级通用治理文档与数据库环境验证。
