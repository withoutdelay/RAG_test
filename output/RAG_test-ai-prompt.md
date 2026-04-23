# AI Prompt Notes

## Retrieval Intent Construction

- Split user intent into `title_intent`, `detail_intent`, and `context_intent`.
- Use section candidates as the first-stage decision boundary.
- Use block retrieval only inside selected sections.

## Composition Mode

- Prefer `reuse_first` when section candidates and reusable blocks are available.
- Preserve section-bound evidence and trace outputs in the final draft payload.
- Emit `selection_reason`, `token_budget`, and selected section/block evidence for review tooling.

## Guardrails

- Do not let generic parent sections outrank a clearly matched leaf section.
- When a leaf section hits strongly, demote broad parent sections by one more tier.
- Keep synonym expansion deterministic and domain-grounded; do not rely on online LLM synonym generation during retrieval scoring.
