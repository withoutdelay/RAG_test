# Workflow Guide

The workflow stays on:

`research -> docs -> docs_confirm -> spec -> frontend -> preview_confirm -> backend -> quality -> delivery`

Key commands:

- `super-dev review docs`
- `super-dev run --resume`
- `super-dev review quality`

Rules:

- Do not implement before docs are confirmed.
- Update `output/*-uiux.md` first when UI direction changes.
- Re-run proof-pack and release readiness after quality fixes.
