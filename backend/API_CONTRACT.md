# API Contract

Base path: `/api/v1`

## Projects

- `POST /projects`
- `GET /projects`
- `GET /projects/{project_id}`
- `PUT /projects/{project_id}`
- `DELETE /projects/{project_id}`

## Requirement / Outline / Section Artifacts

- `POST /projects/{project_id}/requirement-card`
- `GET /projects/{project_id}/requirement-card/latest`
- `POST /projects/{project_id}/clarifications/{item_id}/resolve`
- `POST /projects/{project_id}/evidence-bundles`
- `GET /projects/{project_id}/evidence-bundles/latest`
- `POST /projects/{project_id}/outlines`
- `GET /projects/{project_id}/outlines/latest`
- `POST /projects/{project_id}/outlines/{outline_id}/approve`
- `POST /projects/{project_id}/sections/generate`
- `GET /projects/{project_id}/sections`
- `POST /projects/{project_id}/sections/{section_id}/review`
- `POST /projects/{project_id}/sections/{section_id}/save`
- `GET /projects/{project_id}/validation/latest`
- `GET /projects/{project_id}/review-tasks`
- `POST /projects/{project_id}/review-tasks/{task_id}/resolve`
- `POST /projects/{project_id}/exports`
- `GET /projects/{project_id}/exports/latest`
- `GET /jobs/{job_id}`

## Documents / Assets

- `POST /projects/{project_id}/documents`
- `GET /projects/{project_id}/documents`
- `GET /documents/{document_id}`
- `POST /documents/{document_id}/reparse`
- `DELETE /documents/{document_id}`
- `GET /documents/{document_id}/chunks`
- `GET /documents/{document_id}/figure-assets`
- `GET /documents/{document_id}/table-assets`
- `GET /assets/{asset_id}/content`
- `POST /documents/{document_id}/table-assets/{asset_id}/reconstruct`
- `POST /projects/{project_id}/assets/search`

## Retrieval / Generation / Review

- `POST /retrieval/search`
- `POST /generation`
- `GET /generation/{task_id}`
- `GET /generation/{task_id}/stream`
- `POST /generation/{task_id}/approve`
- `POST /generation/{task_id}/retry`
- `GET /projects/{project_id}/review`
- `POST /projects/{project_id}/review/approve`
- `POST /projects/{project_id}/review/reject`

## Release Notes

- The `reuse-first` iteration depends on section-level retrieval traces being returned in section draft payloads.
- `POST /retrieval/search` now returns structured online retrieval diagnostics on each hit via `reason`, `reason_trace`, and `score_breakdown`, plus response-level `search_trace` with dense/sparse candidate counts.
- `POST /projects/{project_id}/assets/search` now returns structured asset retrieval diagnostics via result-level `reason_trace` and `score_breakdown`, plus response-level `search_trace` with visual-branch, ANN collection, and fallback information.
- Legacy `/generation/*` flows remain compatibility-only paths and are not the primary review surface.
