#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Ruff lint ..."
cd "$REPO_ROOT"
uvx ruff check .

echo "==> Ruff format check ..."
uvx ruff format --check .

echo "==> Pyright (shared) ..."
cd "$REPO_ROOT/shared"
uv run pyright

echo "==> Pyright (sprt-runner) ..."
cd "$REPO_ROOT/sprt-runner"
uv run pyright

echo "==> Pyright (backend) ..."
cd "$REPO_ROOT/backend"
uv run pyright

echo "==> ESLint (frontend) ..."
cd "$REPO_ROOT/frontend"
npx eslint src/

echo "==> Prettier check (frontend) ..."
npx prettier --check src/

echo "==> tsc --noEmit (frontend) ..."
npx tsc --noEmit

echo ""
echo "All lint and type-checks passed!"
