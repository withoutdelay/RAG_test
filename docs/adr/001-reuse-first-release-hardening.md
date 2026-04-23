# ADR 001: Reuse-First Retrieval Hardening and Release Governance

- Status: accepted
- Date: 2026-04-20

## Context

The current iteration moved retrieval from chunk-first competition to section-first selection with traceable block selection. Release governance also required project-named artifacts, CI markers, rehearsal documents, and explicit runtime boundaries before proof-pack could be considered complete.

## Decision

1. Keep `reuse-first` as the primary section composition mode.
2. Treat section retrieval and block retrieval as two staged decisions rather than parallel competitors.
3. Persist section trace, parent/child demotion signals, and section-bound block scope in backend artifacts.
4. Extract helper logic from oversized parsing and frontend files to keep redteam large-file findings below the threshold.
5. Add repository-local release governance artifacts so `super-dev` can evaluate this repository using project-named outputs.

## Consequences

- Retrieval precision improves without introducing online synonym generation.
- Debug and review surfaces expose section candidates and selection reasons.
- Release readiness becomes repeatable because required artifacts are generated under stable names.
- The repository carries a small amount of governance-only packaging metadata at the root.
