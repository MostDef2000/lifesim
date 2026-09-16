#!/usr/bin/env bash
# Quality gate M1: lint + unit/интеграционные тесты без slow-прогонов.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check .
uv run pytest -m "not slow"
