#!/usr/bin/env bash
# Tests need a SEPARATE PostgreSQL database: it is wiped on every run.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${TEST_DATABASE_URL:?Задайте TEST_DATABASE_URL, например postgresql+asyncpg://spiritt:pass@localhost:5432/spiritt_test}"
.venv/bin/pip install -q -r requirements-dev.txt
.venv/bin/python -m pytest -q "$@"
