# Frontend Blueprint

## Primary Route

- `/projects/[id]/editor` is the main operator surface for outline review, section generation, trace inspection, and asset preview.

## Core Components

- `SectionBlock.tsx`: review card, trace surface, markdown render, asset preview, citation source modal.
- `MarkdownArticle.tsx`: shared markdown/table renderer for review and preview.
- `sectionBlockHelpers.ts`: table-merge and asset-type helpers extracted from the main editor block.

## Contract

- The frontend expects section payloads to contain `validator_result.reuse_pack.retrieval_trace`.
- Trace UI renders `selected_sections`, `selected_blocks`, `selection_reason`, and query intent scores without deriving those fields client-side.
- Asset previews prefer section-bound reusable block content for tables before falling back to free-form preview text.

## Verification

- `npm run lint -- src/components/editor/SectionBlock.tsx src/lib/types.ts`
- `npm run build`
