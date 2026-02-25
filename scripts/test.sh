#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

PYTHON_COMPONENTS=(
    "shared"
    "engines/random-engine"
    "sprt-runner"
    "backend"
)

for component in "${PYTHON_COMPONENTS[@]}"; do
    echo "==> Testing ${component} ..."
    cd "${REPO_ROOT}/${component}"
    uv run pytest --cov=src --cov-report=term-missing || { ec=$?; [ "$ec" -eq 5 ] || exit "$ec"; }
    echo "    ✓ ${component} done"
done

echo "==> Testing frontend ..."
cd "${REPO_ROOT}/frontend"
npm run test:ci
echo "    ✓ frontend done"

echo ""
echo "All tests passed!"
