# Copilot Instructions

## Project Overview

Chess engine development suite — monorepo with a Python chess engine, custom SPRT testing framework, FastAPI backend, and React/TypeScript frontend. Engines are opaque UCI subprocesses; no engine code is ever imported directly.

For architecture principles, component boundaries, and the high-level system diagram, see [`.github/prompts/architecture.md`](prompts/architecture.md).

## Ownership Boundary

- **`engines/my-engine/`** — Human-only. **Never generate, modify, or refactor any file under this path.**
- **Everything else** — Copilot scope. Build infrastructure that speaks UCI to engine binaries.

## Stack

| Component | Tech |
|---|---|
| Backend | Python 3.14+, FastAPI, uvicorn |
| SPRT Runner | Python 3.14+, asyncio + multiprocessing |
| Shared lib | Python 3.14+, python-chess |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS |
| Package mgmt | `uv` (Python), `npm` (frontend) |
| Testing | pytest (Python), Vitest + React Testing Library (frontend) |
| Linting | Ruff (Python), ESLint + Prettier (TypeScript) |
| Type checking | Pyright strict (Python), `tsc --noEmit` (TypeScript) |
| CI | GitHub Actions |

## Development Method — TDD

Follow strict **Test-Driven Development**:

1. **Red** — Write a failing test that defines the expected behaviour.
2. **Green** — Write the minimal code to make the test pass.
3. **Refactor** — Clean up while keeping tests green.

Never skip the test-first step. Every public function, route, and component must have tests before implementation. Tests are the specification.

## Testing

### Python (`shared/`, `sprt-runner/`, `backend/`)

```bash
# Run from the component directory with its venv active
cd <component> && uv run pytest

# With coverage
uv run pytest --cov=<package> --cov-report=term-missing

# Single test file
uv run pytest tests/test_uci_client.py -v
```

- Tests live in `tests/` directories alongside source code.
- Use `pytest` fixtures for setup/teardown.
- Mock external dependencies (subprocesses, filesystem, network) — never depend on real engines in unit tests.
- Use `pytest-asyncio` for async tests.

### Frontend (`frontend/`)

```bash
cd frontend && npm test          # Vitest in watch mode
npm run test:ci                  # Single run with coverage
```

- Use React Testing Library — test behaviour, not implementation.
- Mock WebSocket and API calls in tests.

### Top-level

```bash
scripts/test.sh                  # Runs all test suites across all components
```

## Linting & Formatting

### Python — Ruff

Configured in `ruff.toml` at repo root.

```bash
ruff check .                     # Lint
ruff check . --fix               # Lint with auto-fix
ruff format .                    # Format
ruff format . --check            # Format check (CI)
```

Rules: `E`, `F`, `W`, `I` (isort), `UP` (pyupgrade), `B` (bugbear), `SIM`, `RUF`. Line length: 100.

### Python — Pyright

Strict mode. Configured per component in `pyproject.toml` under `[tool.pyright]`.

```bash
pyright                          # Run from component directory
```

### TypeScript — ESLint + Prettier

```bash
cd frontend
npx eslint src/                  # Lint
npx prettier --check src/        # Format check
npx prettier --write src/        # Format
```

### Local pre-push check

```bash
scripts/lint.sh                  # Runs all lint + format + type-check
```

## Coding Conventions

### Python

- **Type hints everywhere.** All function signatures, return types, and variables where non-obvious. Pyright strict must pass.
- **Dataclasses or Pydantic models** for structured data — no raw dicts crossing function boundaries.
- **Async by default** for I/O-bound code (UCI subprocess communication, HTTP handlers, file operations in the backend).
- **f-strings** for string formatting.
- **`snake_case`** for functions, methods, variables; **`PascalCase`** for classes; **`UPPER_SNAKE_CASE`** for constants.
- **Docstrings** on all public functions and classes (Google style).
- **No wildcard imports.** Explicit imports only.
- **ABC for interfaces.** Storage, engine management — always define an abstract interface before implementation.
- **Structured logging** with `logging` module — no `print()` in production code.
- **Path handling** via `pathlib.Path`, not `os.path`.

### TypeScript

- **Strict mode** (`"strict": true` in `tsconfig.json`).
- **Functional components** with hooks — no class components.
- **Named exports** over default exports.
- **`camelCase`** for functions/variables; **`PascalCase`** for components/types; **`UPPER_SNAKE_CASE`** for constants.
- **Explicit return types** on exported functions.
- **Props interfaces** defined and exported alongside components.

### General

- **No commented-out code** in commits.
- **Small, focused functions** — each does one thing.
- **Errors are explicit** — raise/throw with descriptive messages, never silently swallow.
- **Dependencies** — prefer the standard library. Add third-party deps only when they provide significant value.
- **Commits** — conventional commits format (`feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`).
