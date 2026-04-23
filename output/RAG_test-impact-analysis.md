# Change Impact Analysis

- Project: `RAG_test`
- Path: `/Volumes/thunder/code/RAG_test`
- Risk Level: `high`
- Change: Reuse-first retrieval hardening: parser emits section_catalog; retrieval auto-scopes blocks by shortlisted sections; frontend trace panel exposes query intents and selection reason
- Files: `backend/app/services/parsing/docling_parser.py`, `backend/app/api/documents.py`, `backend/app/services/retrieval/case_service.py`, `backend/app/services/composition/section_service.py`, `frontend/src/components/editor/SectionBlock.tsx`, `frontend/src/lib/types.ts`

The requested change `Reuse-first retrieval hardening: parser emits section_catalog; retrieval auto-scopes blocks by shortlisted sections; frontend trace panel exposes query intents and selection reason` is assessed as `high` risk. The strongest signals point to 2 affected modules, 0 entry points, and 5 integration surfaces that should be reviewed before implementation.


## Affected Modules

- **backend**: `backend`
  - direct file/path overlap (confidence=0.95)
- **frontend**: `frontend`
  - direct file/path overlap; keyword overlap: frontend (confidence=0.95)

## Affected Entry Points

- None

## Affected Integration Surfaces

- **components**: `frontend/src/components`
  - direct file/path overlap; same top-level module as changed file (confidence=0.95)
- **api**: `backend/app/api`
  - same top-level module as changed file; direct file/path overlap (confidence=0.95)
- **services**: `backend/app/services`
  - direct file/path overlap; same top-level module as changed file (confidence=0.95)
- **stores**: `frontend/src/stores`
  - same top-level module as changed file (confidence=0.82)
- **models**: `backend/app/models`
  - same top-level module as changed file (confidence=0.82)

## Regression Focus

- API contract and route-level regression checks
- Critical UI paths, navigation, and state transition checks
- Data model, persistence, and migration regression checks

## Recommended Steps

- Read the repo map first to confirm the likely entry points and module boundaries.
- Limit edits to the highest-confidence modules before expanding the scope.
- Freeze the affected surface in PRD / Architecture / UIUX or patch docs before coding.
- Re-test the affected integration surfaces before declaring the change complete.
- Use the changed file list as the minimum review set, then inspect adjacent modules only if impact expands.
- Rerun bugfix/runtime/quality validation after implementation.
