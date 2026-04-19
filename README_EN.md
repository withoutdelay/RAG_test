# Presale Copilot

Current version: `2.3.0`

## Super Dev Install And Entry Points

- `pip install -U super-dev`
- `uv tool install super-dev`
- `super-dev update`
- Slash trigger: `/super-dev`
- Message prefix: `super-dev:`

Recommended host prompt:

```text
super-dev: Start the Super Dev workflow for the current project. Begin with research, then write PRD, Architecture, and UIUX documents, and stop for confirmation.
```

## Project Positioning

Presale Copilot is a product-driven solution authoring system for industrial electrical presales teams. It connects requirement extraction, evidence reuse, solution design, outline generation, section drafting, validation, and export into one governed delivery loop.

## Delivery Contract

- Keep `research -> docs -> docs_confirm -> spec -> frontend -> preview_confirm -> backend -> quality -> delivery`.
- Record document review with `super-dev review docs`.
- Resume the same workflow with `super-dev run --resume`.
