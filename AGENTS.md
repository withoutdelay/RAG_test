<!-- BEGIN SUPER DEV CODEX -->
# Super Dev for Codex CLI

When a user message starts with `super-dev:` or `super-dev：`, enter Super Dev pipeline mode immediately.

If the repository already contains active Super Dev workflow context, the first natural-language requirement in a new session must also continue Super Dev rather than normal chat.

## Direct Activation Rule
- Do not spend a turn saying you will read the skill first, explain the skill, or decide whether to enter the workflow.
- Treat the current trigger as already authorized to execute the full Super Dev pipeline.
- If a compatibility skill under `~/.codex/skills/` is loaded, treat it as the same Super Dev contract, not a fallback mode.

## Required execution
1. First reply: state that Super Dev pipeline mode is active and the current phase is `research`.
2. Read `knowledge/` and `output/knowledge-cache/*-knowledge-bundle.json` when available.
3. Use Codex native web/search/edit/terminal capabilities to perform similar-product research and write `output/*-research.md` into the repository workspace.
4. Draft `output/*-prd.md`, `output/*-architecture.md`, and `output/*-uiux.md` in the same Codex session and save them as actual project files.
5. Stop after the three core documents, summarize them, and wait for explicit confirmation.
6. Only after confirmation, create `.super-dev/changes/*/proposal.md` and `.super-dev/changes/*/tasks.md`, then continue with frontend-first implementation.

## Constraints
- Do not start coding directly after `super-dev:` or `super-dev：`.
- Do not create Spec before document confirmation.
- If the user requests architecture changes, first update `output/*-architecture.md`, then realign Spec/tasks and implementation.
- If the user requests quality or security remediation, first fix the issues, rerun quality gate and `super-dev release proof-pack`, and only then continue.
- 开始任何 UI 实现前，必须先锁定 `output/*-uiux.md` 中冻结的图标库、字体系统、design token system、组件生态和页面骨架。
- Before any UI implementation, first lock the icon library, typography, design token system, component ecosystem, and page skeleton from `output/*-uiux.md`.
- Do not use emoji as functional icons or placeholders.
- For non-conversational AI products, avoid Claude / ChatGPT-style sidebar chat shells unless the UI plan explicitly justifies them.
- Keep using the component ecosystem and design token direction defined in `output/*-uiux.md` rather than switching ad hoc.
- If a required artifact is only described in chat and not written into the repository, treat the step as incomplete.
- Codex remains the execution host; Super Dev is the local governance workflow.
- Use local `super-dev` CLI only for governance actions such as doctor, review, quality, release readiness, or update; do not outsource the main coding workflow to the CLI.

## Conversation Continuity Contract
- If `.super-dev/SESSION_BRIEF.md` exists, read it before responding and treat it as the active workflow state.
- If the workflow is waiting for docs confirmation, preview confirmation, UI revision, architecture revision, or quality revision, then user replies like `修改`, `补充`, `继续改`, `确认`, `通过`, `继续`, or detailed feedback remain inside the current Super Dev stage.
- After each requested revision inside a gate, stay in the same stage, update the required artifacts, summarize what changed, and wait again for explicit confirmation.
- Do not silently exit Super Dev mode because the user asked for several edits, follow-up questions, or extra constraints.
- Only leave the current Super Dev workflow if the user explicitly says to cancel the workflow, restart from scratch, or switch back to normal chat.

## Super Dev System Flow Contract
- SUPER_DEV_FLOW_CONTRACT_V1
- PHASE_CHAIN: research>docs>docs_confirm>spec>frontend>preview_confirm>backend>quality>delivery
- DOC_CONFIRM_GATE: required
- PREVIEW_CONFIRM_GATE: required
- HOST_PARITY: required
<!-- END SUPER DEV CODEX -->

## Worktree Data Isolation

- This worktree must not share the primary `rag_test` Docker Compose project, Postgres volume/database, Qdrant collection, MinIO bucket, or Redis namespace/DB.
- Set a unique `COMPOSE_PROJECT_NAME`, host port set, `DATABASE_URL`, `QDRANT_COLLECTION`, `MINIO_BUCKET`, and `REDIS_URL` before starting services.
- Do not run destructive tests, database resets, Qdrant collection deletes, or sample re-ingestion against another worktree's dev database.
- The primary worktree `/Volumes/thunder/code/RAG_test` is the only worktree allowed to use `COMPOSE_PROJECT_NAME=rag_test`.
