# chess-vibe monorepo Makefile
# ────────────────────────────────────────────────────────────────────
# Targets: setup, test, lint, dev, dev-backend, dev-frontend, clean

PYTHON_COMPONENTS := shared sprt-runner backend
PYTHON_VERSION    := 3.13

.PHONY: help setup test test-integration test-all lint dev dev-backend dev-frontend clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Setup ───────────────────────────────────────────────────────────

setup: ## Install all dependencies (Python venvs + npm)
	@for component in $(PYTHON_COMPONENTS); do \
		echo "==> Setting up $$component ..."; \
		cd $(CURDIR)/$$component && uv venv --python $(PYTHON_VERSION) && uv sync; \
		echo "    ✓ $$component ready"; \
	done
	@echo "==> Building engines ..."
	@cd $(CURDIR)/engines/random-engine && uv venv .venv --python $(PYTHON_VERSION) && uv pip install --python .venv/bin/python -e .
	@echo "    ✓ engines built"
	@echo "==> Setting up frontend ..."
	@cd $(CURDIR)/frontend && npm ci
	@echo "    ✓ frontend ready"
	@echo ""
	@echo "All components bootstrapped successfully."

# ── Test ────────────────────────────────────────────────────────────

test: ## Run unit tests only (excludes integration tests)
	@for component in $(PYTHON_COMPONENTS); do \
		echo "==> Testing $$component ..."; \
		cd $(CURDIR)/$$component && uv run pytest -m "not integration" --cov=src --cov-report=term-missing \
			|| { ec=$$?; [ "$$ec" -eq 5 ] || exit "$$ec"; }; \
		echo "    ✓ $$component done"; \
	done
	@echo "==> Testing engines/random-engine ..."
	@cd $(CURDIR)/engines/random-engine && uv run pytest -m "not integration" --cov=src --cov-report=term-missing \
		|| { ec=$$?; [ "$$ec" -eq 5 ] || exit "$$ec"; }
	@echo "    ✓ engines/random-engine done"
	@echo "==> Testing frontend ..."
	@cd $(CURDIR)/frontend && npm run test:ci
	@echo "    ✓ frontend done"
	@echo ""
	@echo "All unit tests passed!"

test-integration: ## Run integration tests only
	@for component in $(PYTHON_COMPONENTS); do \
		echo "==> Integration testing $$component ..."; \
		cd $(CURDIR)/$$component && uv run pytest -m integration --cov=src --cov-report=term-missing \
			|| { ec=$$?; [ "$$ec" -eq 5 ] || exit "$$ec"; }; \
		echo "    ✓ $$component done"; \
	done
	@echo "==> Integration testing engines/random-engine ..."
	@cd $(CURDIR)/engines/random-engine && uv run pytest -m integration --cov=src --cov-report=term-missing \
		|| { ec=$$?; [ "$$ec" -eq 5 ] || exit "$$ec"; }
	@echo "    ✓ engines/random-engine done"
	@echo ""
	@echo "All integration tests passed!"

test-all: ## Run all tests (unit + integration)
	@for component in $(PYTHON_COMPONENTS); do \
		echo "==> Testing $$component ..."; \
		cd $(CURDIR)/$$component && uv run pytest --cov=src --cov-report=term-missing \
			|| { ec=$$?; [ "$$ec" -eq 5 ] || exit "$$ec"; }; \
		echo "    ✓ $$component done"; \
	done
	@echo "==> Testing engines/random-engine ..."
	@cd $(CURDIR)/engines/random-engine && uv run pytest --cov=src --cov-report=term-missing \
		|| { ec=$$?; [ "$$ec" -eq 5 ] || exit "$$ec"; }
	@echo "    ✓ engines/random-engine done"
	@echo "==> Testing frontend ..."
	@cd $(CURDIR)/frontend && npm run test:ci
	@echo "    ✓ frontend done"
	@echo ""
	@echo "All tests passed!"

# ── Lint ────────────────────────────────────────────────────────────

lint: ## Run all linters, formatters, and type-checkers
	@echo "==> Ruff lint ..."
	@cd $(CURDIR) && uvx ruff check .
	@echo "==> Ruff format check ..."
	@cd $(CURDIR) && uvx ruff format --check .
	@for component in $(PYTHON_COMPONENTS); do \
		echo "==> Pyright ($$component) ..."; \
		cd $(CURDIR)/$$component && uv run pyright || exit $$?; \
	done
	@echo "==> Pyright (engines/random-engine) ..."
	@cd $(CURDIR)/engines/random-engine && uv run pyright
	@echo "==> ESLint (frontend) ..."
	@cd $(CURDIR)/frontend && npx eslint src/
	@echo "==> Prettier check (frontend) ..."
	@cd $(CURDIR)/frontend && npx prettier --check src/
	@echo "==> tsc --noEmit (frontend) ..."
	@cd $(CURDIR)/frontend && npx tsc --noEmit
	@echo ""
	@echo "All lint and type-checks passed!"

# ── Dev servers ─────────────────────────────────────────────────────

dev-backend: ## Start the FastAPI backend on :8000
	@cd $(CURDIR)/backend && uv run uvicorn backend.main:create_app \
		--factory --host 127.0.0.1 --port 8000 --reload

dev-frontend: ## Start the Vite dev server on :5173
	@cd $(CURDIR)/frontend && npx vite --host 127.0.0.1 --port 5173

dev: ## Start backend + frontend (use Ctrl-C to stop both)
	@cleanup() { \
		kill $$BACKEND_PID $$FRONTEND_PID 2>/dev/null; \
		wait $$BACKEND_PID $$FRONTEND_PID 2>/dev/null; \
	}; \
	trap cleanup INT TERM EXIT; \
	cd $(CURDIR)/backend && uv run uvicorn backend.main:create_app \
		--factory --host 127.0.0.1 --port 8000 --reload & \
	BACKEND_PID=$$!; \
	cd $(CURDIR)/frontend && npx vite --host 127.0.0.1 --port 5173 & \
	FRONTEND_PID=$$!; \
	wait

# ── Clean ───────────────────────────────────────────────────────────

clean: ## Remove all venvs and node_modules
	@for component in $(PYTHON_COMPONENTS); do \
		echo "==> Cleaning $$component ..."; \
		rm -rf $(CURDIR)/$$component/.venv; \
	done
	@echo "==> Cleaning engines/random-engine ..."
	@rm -rf $(CURDIR)/engines/random-engine/.venv
	@rm -rf $(CURDIR)/frontend/node_modules
	@echo "Clean."
