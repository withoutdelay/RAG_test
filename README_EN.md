# Industrial Proposal Copilot

Industrial Proposal Copilot is an experimental RAG application for drafting industrial technical proposals. It connects requirement documents, a historical proposal library, evidence retrieval, outline generation, controlled LLM drafting, quality checks, and Word export into one reviewable workflow.

The repository is intended for research, demos, and further product development. It does not include private customer documents, a production-ready case library, API keys, or runtime databases.

## Highlights

- Project and RFP intake
- Historical proposal material import and audit
- Text, table, and figure asset indexing
- Evidence bundle retrieval
- Human-reviewable outline generation
- Section-level draft generation with citations and asset traces
- Quality checks for internal residue, stale project references, and asset placeholders
- Word export for downstream editing

## Architecture

```text
Frontend: Next.js
Backend: FastAPI
Storage: PostgreSQL, Qdrant, MinIO/local files, optional Redis
Optional gateway: reversible masking and restoration for LLM calls
External providers: LLM, embedding, optional document parsing service
```

## Quick Start

```bash
cp .env.example .env
make dev-up
```

Local URLs:

- Frontend: `http://127.0.0.1:3000`
- Backend API: `http://127.0.0.1:8000/api/v1`
- API docs: `http://127.0.0.1:8000/docs`

## Configuration

Use `.env.example` as the template. Configure only the providers you need:

- `LLM_PROVIDER_BACKEND`
- `QWEN_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `DOUBAO_API_KEY`
- `EMBEDDING_BACKEND`
- `EMBEDDING_API_KEY`
- `PARSER_BACKEND`
- `AUTH_ENABLED`
- `COMPOSE_PROJECT_NAME`
- `QDRANT_COLLECTION`

Do not commit `.env`, real credentials, customer files, parsed customer text, generated drafts, local databases, vector snapshots, or object storage data.

## Development

```bash
make backend-install
make backend-migrate
make backend-run
```

```bash
cd frontend
npm ci
npm run lint
npm run build
```

## Security Note

This cleanup removes runtime data from the current repository state. If real secrets were ever committed to git history, rotate them and rewrite repository history before relying on the public repository as clean.

## License

No license has been selected yet.
