#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[install] upgrading super-dev"
python3 -m pip install -U super-dev

echo "[install] creating local virtualenv"
python3 -m venv "${ROOT_DIR}/.venv"
"${ROOT_DIR}/.venv/bin/pip" install --upgrade pip
"${ROOT_DIR}/.venv/bin/pip" install -e "${ROOT_DIR}/backend"

echo "[install] installing frontend dependencies"
npm --prefix "${ROOT_DIR}/frontend" install

echo "[install] done"
