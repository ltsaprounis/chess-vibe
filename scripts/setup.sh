#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

COMPONENTS=(
    "shared"
    "engines/random-engine"
    "sprt-runner"
    "backend"
)

for component in "${COMPONENTS[@]}"; do
    echo "==> Setting up ${component} ..."
    cd "${REPO_ROOT}/${component}"
    uv venv .venv
    uv sync
    echo "    ✓ ${component} ready"
done

# Frontend
echo "==> Setting up frontend ..."
cd "${REPO_ROOT}/frontend"
npm install
echo "    ✓ frontend ready"

echo ""
echo "All components bootstrapped successfully."
