# Repository Guidelines

## Project Structure & Module Organization

This repository now contains a working Phase 2 backend plus planning documents. Key paths:

- `spec.md`, `项目开发计划.md`, `智能系统技术方案设计.md`: product and architecture references
- `backend/app/`: FastAPI API layer, models, schemas, services, and utilities
- `backend/alembic/`: database migration config and revisions
- `backend/tests/`: parsing, retrieval, storage, and API integration tests
- `gateway/`, `frontend/`: placeholders for later phases

Keep new backend logic in `backend/app/services/` and API contracts in `backend/app/schemas/`.

## Build, Test, and Development Commands

Use a local virtual environment for backend work:

- `make backend-install`: install the lightweight backend
- `make backend-install-full`: install optional Docling and sentence-transformers support
- `docker compose up -d postgres qdrant minio`: start local dependencies
- `make backend-migrate`: apply Alembic migrations
- `make backend-run`: run FastAPI with reload
- `make phase2-test`: run service-level tests
- `make phase2-test-api`: run the Postgres-backed Phase 2 API integration test

## Coding Style & Naming Conventions

Use Python 3.11+ style, 4-space indentation, `snake_case` modules, and type hints on public functions. Keep FastAPI route handlers thin and move parsing, storage, and retrieval logic into services. Use `PascalCase` for classes and Pydantic models.

Environment-driven backend switches should stay explicit and predictable, for example `PARSER_BACKEND=fallback` and `EMBEDDING_BACKEND=fallback`.

## Testing Guidelines

Add tests under `backend/tests/test_*.py`. Prefer fast unit tests for service logic, then targeted integration tests for API flows. For retrieval and parsing work, cover both fallback mode and explicit backend-selection behavior.

When tests need infrastructure, prefer isolated local resources such as `QDRANT_LOCATION=:memory:` and a disposable PostgreSQL container.

## Commit & Pull Request Guidelines

Use short imperative commit subjects, for example `backend: add phase2 api integration test`. PRs should summarize behavior changes, config changes, test coverage, and any required local services or environment variables.
