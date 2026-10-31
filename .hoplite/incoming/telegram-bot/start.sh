#!/usr/bin/env bash
# Linux start: uses .venv if present, otherwise the system python3. Same as: python3 run.py
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
exec "$PY" run.py "$@"
