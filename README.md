# presale-copilot

Phase 2 backend plus Phase 3 gateway scaffold for the presales AI system described in `spec.md`.

## Current Scope

- Docker Compose for local infrastructure
- FastAPI backend with project CRUD
- Document upload, parsing, chunking, vector indexing, and retrieval APIs
- Phase 3 gateway for reversible masking and restoration
- Phase 4 backend generation workflow, review APIs, and pluggable LLM providers
- SQLAlchemy models and Alembic migration
- Local/object-storage fallback and Qdrant in-memory test mode
- Unit and API integration tests for the Phase 2 path
- Gateway detector/masker/restorer/API tests for the Phase 3 path
- Optional heavy parsing and embedding backends behind explicit install/config flags

## Quick Start

1. Create a virtualenv and install backend deps:
   `python3 -m venv .venv && .venv/bin/pip install -e ./backend`
   For real Docling and sentence-transformers support:
   `.venv/bin/pip install -e './backend[full]'`
2. Copy `.env.example` to `.env`.
3. Start services:
   `docker compose up -d postgres qdrant minio`
4. Apply migrations:
   `cd backend && ../.venv/bin/alembic upgrade head`
5. Run the backend:
   `cd backend && ../.venv/bin/uvicorn app.main:app --reload`
6. Open `http://localhost:8000/docs`.
7. Open `http://localhost:8001/docs` for the masking gateway.

## Phase 2 Modes

- `PARSER_BACKEND=auto|docling|fallback`
  `auto` uses Docling for non-Markdown files when available.
- `EMBEDDING_BACKEND=auto|sentence-transformers|fallback`
  `fallback` is the default lightweight mode for local dev and tests.
- `QDRANT_LOCATION=:memory:`
  uses in-memory Qdrant for fast tests without a running Qdrant container.
- `BACKEND_EXTRAS=full docker compose up --build backend`
  installs the optional heavy backends inside the backend container image.

## Phase 3 Gateway Modes

- `REDIS_ENABLED=false`
  uses the in-memory session store for local dev and tests.
- `REDIS_ENABLED=true`
  enables Redis-backed session persistence for Docker/local integration runs.
- `make gateway-run`
  runs the gateway locally on port `8001` with the in-memory store.
- The gateway now includes a stream-safe restoration helper for future SSE output handling and structured audit logging through the service logger.
- The gateway container uses Python `3.11`, which is the supported runtime for Presidio in this repo.
- In a shared local `.venv`, Presidio-heavy installs may conflict with optional Docling extras; Docker is the safest way to validate the full gateway stack.

## Phase 4 LLM Provider Modes

- `LLM_PROVIDER_BACKEND=mock|live`
  `mock` is the default for local tests; `live` enables real DeepSeek/Qwen/Azure/OpenAI-compatible chat-completions calls.
- DeepSeek uses `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL`.
- Qwen uses `QWEN_API_KEY`, `QWEN_BASE_URL`, and `QWEN_MODEL`.
- Azure OpenAI uses `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, and `AZURE_OPENAI_API_VERSION`.
- OpenAI-compatible relay endpoints use `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL`.
- For development with a GPT-style proxy, point `OPENAI_BASE_URL` at the relay's `/v1` root and keep `LLM_PROVIDER_BACKEND=live`.
- The backend currently targets each provider's chat-completions interface and keeps the masking gateway in front of outbound prompts.

## Test Commands

- Service-level suite:
  `cd backend && env PYTHONPYCACHEPREFIX=/tmp/pycache ../.venv/bin/python -m unittest tests.test_parsing tests.test_retrieval tests.test_storage`
- API integration suite:
  `cd backend && env DATABASE_URL=postgresql+asyncpg://copilot:copilot@localhost:55432/copilot_db QDRANT_LOCATION=:memory: EMBEDDING_DIMENSION=16 EMBEDDING_BACKEND=fallback PYTHONPYCACHEPREFIX=/tmp/pycache ../.venv/bin/python -m unittest tests.test_api_phase2`
- Shortcut targets:
  `make backend-install`, `make backend-install-full`, `make phase2-test`, `make phase2-test-api`
- Gateway suite:
  `cd gateway && env REDIS_ENABLED=false PYTHONPYCACHEPREFIX=/tmp/pycache ../.venv/bin/python -m unittest tests.test_detector tests.test_masker tests.test_restorer tests.test_gateway`
- Gateway shortcut target:
  `make phase3-test`
- Phase 4 shortcut target:
  `make phase4-test`

## Notes

- `docling` and `sentence-transformers` are installed but not used by default in tests.
- Real embedding mode may need a pre-fetched model or network access on first run.
- Gateway defaults to an in-memory mapping store unless `REDIS_ENABLED=true` is set.
