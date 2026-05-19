# Industrial Proposal Copilot

Industrial Proposal Copilot 是一个面向工业技术方案编写场景的 RAG 应用原型。它把需求文档、历史方案库、图表资产、章节大纲、LLM 受控生成和质量检查串成一条流程，目标是帮助工程团队更快产出可追溯、可编辑的技术方案草稿。

> 当前状态：MVP / experimental。该项目适合用于研发、演示和二次开发，不应在未完成安全、权限、数据治理和质量验收前直接用于生产交付。

## 核心能力

- 项目与需求管理：创建项目、上传 RFP/需求文档、抽取结构化需求卡。
- 历史方案库：导入历史技术方案，解析文本块、表格和图片资产，并建立可检索索引。
- 证据检索：结合向量检索、结构化过滤、章节意图和资产候选，生成 evidence bundle。
- 大纲生成：基于需求和证据生成方案大纲，并支持人工调整确认。
- 章节草稿：按章节生成 Markdown 草稿，保留引用、推荐图表资产和生成 trace。
- 质量检查：检查内部提示残留、旧项目痕迹、图表占位符、章节口径和基础一致性。
- 导出：导出 Word 文档，便于工程师继续编辑和交付。
- 审计入口：查看历史材料解析状态、chunk 数、图表资产数、处理状态和重解析任务。

## 设计原则

- Reuse first：优先复用历史方案中的可信工程内容，而不是让模型凭空发挥。
- Evidence grounded：生成内容应能追溯到需求、历史文本块、表格或图片资产。
- Human in the loop：大纲、草稿、材料解析状态和质量问题都允许人工审阅和修正。
- Data isolation：不同开发 worktree、测试环境和部署环境应使用隔离的数据库、向量库 collection、对象存储 bucket 和 Redis namespace。
- No bundled private corpus：公开仓库不包含真实客户文档、私有样本、运行期数据库或 API key。

## 架构概览

```text
Frontend (Next.js)
  Project / Documents / Requirement / Evidence / Outline / Draft / Export
        |
        v
Backend (FastAPI)
  Projects, Documents, Retrieval, Composition, Validation, Export
        |
        +-- PostgreSQL: business state, jobs, drafts, audit records
        +-- Qdrant: vector index
        +-- MinIO or local storage: uploaded files and extracted assets
        +-- Redis: optional queue/session/cache support
        +-- Gateway: optional masking/restoration service for LLM calls
        |
        v
External services
  LLM provider, embedding provider, optional document parsing provider
```

## Repository Layout

```text
backend/                 FastAPI backend and business services
backend/app/api/         API routes
backend/app/models/      SQLAlchemy models
backend/app/services/    parsing, retrieval, generation, validation and export
backend/data/domain_taxonomy/
                         versioned taxonomy/configuration used by retrieval and generation
frontend/                Next.js frontend
gateway/                 optional masking gateway
scripts/                 local development and deployment scripts
docs/                    developer documentation
docker-compose.yml       local development dependencies
docker-compose.prod.yml  containerized deployment template
```

Runtime directories such as `backend/data/uploads/`, `backend/data/case_library/`, `backend/data/knowledge_wiki/`, `output/`, `private_samples/real_proposals/` and local `.env` files are intentionally ignored.

## Quick Start

Requirements:

- Docker and Docker Compose
- Node.js 20+ for frontend-only local work
- Python 3.11+ for backend-only local work

Start the full local stack:

```bash
cp .env.example .env
make dev-up
```

Restart after changing backend/frontend code:

```bash
make dev-restart
```

Default local entry points:

- Frontend: `http://127.0.0.1:3000`
- Backend API: `http://127.0.0.1:8000/api/v1`
- API docs: `http://127.0.0.1:8000/docs`

## Configuration

Copy `.env.example` to `.env` and configure only the providers you need.

Important settings:

- `LLM_PROVIDER_BACKEND`: `mock` for local smoke tests, `live` for real model calls.
- `QWEN_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `DOUBAO_API_KEY`: optional LLM provider credentials.
- `EMBEDDING_BACKEND`: embedding provider selection.
- `EMBEDDING_API_KEY`: embedding provider credential.
- `PARSER_BACKEND`: local parser or optional cloud parser.
- `AUTH_ENABLED`: enable login protection for exposed deployments.
- `COMPOSE_PROJECT_NAME`: isolate Docker volumes and networks per environment.
- `QDRANT_COLLECTION`: isolate vector data per environment and embedding model.

Never commit `.env`, real API keys, customer files, runtime databases or extracted document assets.

## Historical Library Workflow

1. Put private source documents outside git, for example under `private_samples/real_proposals/`.
2. Upload or import documents through the application.
3. Review parsing quality in the historical materials page.
4. Reparse or route documents when needed.
5. Use approved materials as retrieval evidence for new projects.

The public repository contains only code and small configuration fixtures. It does not include a usable historical proposal corpus.

## Development Commands

```bash
make backend-install        # install backend package in a local venv
make backend-migrate        # run Alembic migrations
make backend-run            # run FastAPI backend
make gateway-run            # run optional masking gateway
make phasev2-test-api       # run a compact API pipeline smoke test
```

Frontend:

```bash
cd frontend
npm ci
npm run lint
npm run build
```

## Deployment Notes

For a public or customer-facing deployment:

- Set `AUTH_ENABLED=true`.
- Use strong `SECRET_KEY`, `AUTH_USERS` or password hash settings.
- Use non-default database, Redis, MinIO and object storage credentials.
- Keep `COMPOSE_PROJECT_NAME`, `QDRANT_COLLECTION` and storage buckets environment-specific.
- Keep parser workers bounded; document parsing can be CPU and memory intensive.
- Prefer external embedding services for small servers without GPU capacity.
- Back up PostgreSQL, object storage and any generated case library artifacts separately.

## Security And Data Hygiene

This repository should not contain:

- API keys or cloud access keys
- `.env` or deployment runtime files
- customer documents
- parsed customer text
- generated customer drafts
- local SQLite/PostgreSQL dumps
- vector database snapshots
- object storage buckets

If a real secret was committed to git history before this cleanup, rotate that secret and rewrite repository history before treating the public repository as clean.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
