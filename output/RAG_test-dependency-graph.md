# Dependency Graph

- Project: `RAG_test`
- Path: `/Volumes/thunder/code/RAG_test`
- Nodes: `209`
- Edges: `2`

The dependency graph highlights `209` source nodes and `2` internal import edges. Use the critical nodes and paths first when you need to understand blast radius, debug high-risk flows, or decide where to place regression coverage.

## Critical Nodes

- **frontend/src/components/layout/AppLayout.tsx**: inbound=0, outbound=2, score=4, role=internal
- **frontend/src/components/layout/Header.tsx**: inbound=1, outbound=0, score=3, role=internal
- **frontend/src/components/layout/Sidebar.tsx**: inbound=1, outbound=0, score=3, role=internal

## Critical Paths

- None

## Sample Edges

- `frontend/src/components/layout/AppLayout.tsx` -> `frontend/src/components/layout/Header.tsx` (module-import)
- `frontend/src/components/layout/AppLayout.tsx` -> `frontend/src/components/layout/Sidebar.tsx` (module-import)
