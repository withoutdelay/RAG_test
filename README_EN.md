# Presale Copilot

Industrial presales solution generation system with a `reuse-first` workflow.

Current version: `2.3.0`

## Super Dev Entry Points

This repository is wired for Super Dev and is primarily used from Codex CLI.

Install and update:

- `pip install -U super-dev`
- `uv tool install super-dev`
- `super-dev update`

Host triggers:

- `/super-dev "your request"` in slash-enabled hosts
- `super-dev: your request` in Codex CLI

The repository also exposes local host-side release and quality gates:

- `super-dev host release-gate`
- `super-dev host project-replay-gate`
- `super-dev host quality-smoke`

Running `super-dev host release-gate` persists the latest gate evidence into `output/`:

- `RAG_test-release-gate.md`
- `RAG_test-release-gate.json`

When `output/RAG_test-project-replay-eval.json` already exists, `release-gate` and `project-replay-gate` reuse the stored `project_id` automatically. You can still pass it explicitly:

- `super-dev host release-gate --project-id <uuid>`

You can also begin from an idea with:

- `super-dev start --idea "your request"`

Documentation:

- [Quickstart](docs/QUICKSTART.md)
- [Host Usage Guide](docs/HOST_USAGE_GUIDE.md)
- [Workflow Guide EN](docs/WORKFLOW_GUIDE_EN.md)
- [Product Audit](docs/PRODUCT_AUDIT.md)
