#!/usr/bin/env bash
# Personal OS — End-to-End Smoke Test CLI wrapper.
# Executes deploy/scripts/smoke_test_e2e.py using repository virtualenv or system python.
#
# Usage:
#   ./deploy/scripts/e2e-smoke-test.sh [options]
#   ./deploy/scripts/e2e-smoke-test.sh --url http://127.0.0.1:8000 --skip-auth
#   ./deploy/scripts/e2e-smoke-test.sh --token <JWT>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [[ -f "${REPO_ROOT}/.venv/bin/python" ]]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
elif command -v uv >/dev/null 2>&1; then
  PYTHON="uv run python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  echo "Python 3 is required to run e2e-smoke-test." >&2
  exit 1
fi

exec $PYTHON "${SCRIPT_DIR}/smoke_test_e2e.py" "$@"
