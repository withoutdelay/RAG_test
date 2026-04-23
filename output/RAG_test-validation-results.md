# Validation Results

## Automated Checks

- Backend targeted tests: passed (`199/199`)
- Frontend lint: passed
- Frontend build: passed
- Redteam: passed (`70/100`)

## Notes

- Root `pytest` entrypoint was not available from `.venv/bin/pytest`; backend validation used `../.venv/bin/python -m unittest` for the no-DB suite instead.
- Release-governance checks are tracked separately in `proof-pack` and `release-readiness`.
