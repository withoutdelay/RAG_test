# Regression Guard

- Project: `RAG_test`
- Path: `/Volumes/thunder/code/RAG_test`
- Risk Level: `high`
- Change: Reuse-first retrieval hardening: parser emits section_catalog; retrieval auto-scopes blocks by shortlisted sections; frontend trace panel exposes query intents and selection reason
- Files: `backend/app/services/parsing/docling_parser.py`, `backend/app/api/documents.py`, `backend/app/services/retrieval/case_service.py`, `backend/app/services/composition/section_service.py`, `frontend/src/components/editor/SectionBlock.tsx`, `frontend/src/lib/types.ts`

Regression Guard converts the current `high`-risk change into an executable verification checklist. Focus on the high-priority checks first, then complete the medium and supporting checks before marking the change complete.


## High Priority Checks

- **项目回放快照回归**
  - Scope: 项目级章节生成、质量评分、历史基线对比
  - Reason: Project replay snapshot must prove the latest implementation does not regress the delivered draft.
  - Severity: `high`
  - Validation: 运行宿主级 release gate 或项目回放 gate，确认 snapshot health=`healthy`、章节 `generated/passed` 全通过，且 comparison.regression_count=`0`。
- **接口契约与路由回归**
  - Scope: 状态码、字段结构、关键路由
  - Reason: API contract and route-level regression checks
  - Severity: `high`
  - Validation: 核对接口字段、状态码、异常分支与关键路由是否保持兼容。
- **数据模型与持久层**
  - Scope: 写入、读取、迁移、兼容性
  - Reason: Data model, persistence, and migration regression checks
  - Severity: `high`
  - Validation: 验证读写兼容性、历史数据兼容性以及迁移/回滚安全性。

## Medium Priority Checks

- **关键 UI 路径与导航**
  - Scope: 主页面、主按钮、状态切换、导航跳转
  - Reason: Critical UI paths, navigation, and state transition checks
  - Severity: `medium`
  - Validation: 走一遍主界面、关键 CTA 和状态切换，确认没有视觉或交互回退。
- **邻近模块连带影响**
  - Scope: backend, frontend
  - Reason: 高置信度受影响模块需要额外检查相邻调用路径。
  - Severity: `medium`
  - Validation: 对相邻模块做最小回归检查，确认没有隐藏耦合回退。

## Supporting Checks

- **直接改动文件自检**
  - Scope: backend/app/services/parsing/docling_parser.py, backend/app/api/documents.py, backend/app/services/retrieval/case_service.py, backend/app/services/composition/section_service.py, frontend/src/components/editor/SectionBlock.tsx
  - Reason: 显式改动文件必须先完成最小自检，再扩大范围。
  - Severity: `supporting`
  - Validation: 逐个确认改动文件的输入、输出和异常分支没有被破坏。

## Recommended Commands

- `super-dev host release-gate --project-id 359acbab-0acd-4c19-aa31-7f6d50512a12`
- `super-dev host project-replay-gate --project-id 359acbab-0acd-4c19-aa31-7f6d50512a12`
- `super-dev impact --json`
- `super-dev quality --type all`
- `super-dev release proof-pack`
