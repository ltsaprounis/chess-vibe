# Chess Vibe

Development suite for building and testing chess engines — play games
in the browser, run SPRT tests, track progress. The defining split:
`engines/my-engine/` is 100% human-written and off-limits to agents;
everything else is agent scope. Engines are opaque UCI subprocesses —
infrastructure never imports engine code.

## Documentation map

- [.github/prompts/architecture.md](../.github/prompts/architecture.md)
  — principles, ownership boundary, component boundaries, monorepo
  layout, key design decisions. Read this first.
- [.github/copilot-instructions.md](../.github/copilot-instructions.md)
  — binding toolchain, TDD method, style, and testing rules. Written
  for Copilot, applies to Claude identically.
- [README.md](../README.md) — test and CI quickstart.
- [engines.json](../engines.json) /
  [engines.schema.json](../engines.schema.json) — the engine
  registry: id, name, dir, build, run per engine. The only mechanism
  for adding an engine; no code changes.

## Stack (see copilot-instructions.md for detail)

- Python 3.13 components: `shared/` (python-chess, UCI client,
  repository ABC storage), `sprt-runner/` (asyncio + multiprocessing
  CLI), `backend/` (FastAPI + uvicorn). Tooling: uv, ruff (pinned,
  see Makefile `RUFF_VERSION`), pyright strict, pytest.
- Frontend (`frontend/` only): React 19 + TypeScript + Vite +
  Tailwind — npm, ESLint, Prettier, Vitest + React Testing Library.
- Each component has its own venv; cross-component calls go through
  subprocesses with the target's interpreter, never shared imports.

## Dependency policy

Lockfiles are per-machine and gitignored. Every direct dependency —
runtime and dev — is pinned exactly (`==` / no `^`) in the component
`pyproject.toml` or `frontend/package.json`. Adding or updating a
dependency means updating the exact pin; CI resolves fresh from the
manifests, so an unpinned or range dependency can break main without
any code change (starlette is pinned in `backend/` for exactly this
reason — fastapi's own range is loose).

## Component sub-agents

`.claude/agents/` defines one sub-agent per component, all pinned to
Sonnet 5 (`claude-sonnet-5`). Each knows its component's scope,
boundary rules, and verification gates:

| Agent           | Owns                                                                           |
|-----------------|--------------------------------------------------------------------------------|
| shared-dev      | `shared/` — UCI client, storage, registry                                      |
| sprt-runner-dev | `sprt-runner/` — SPRT CLI + orchestration                                      |
| backend-dev     | `backend/` — FastAPI REST/WS + services                                        |
| frontend-dev    | `frontend/` — React UI                                                         |
| engines-dev     | `engines/random-engine/`, `engines/external/`, `engines.json` — never my-engine |

One cross-cutting agent sits apart from the table:
`boundary-reviewer` (model: inherit, read-only) reviews a diff
against architecture.md, copilot-instructions.md, and the hard rules
below — ownership, boundaries, typing, tests, dependency policy.
Run it before committing, especially after component agents worked
in parallel. It only reports; fixes go to the owning component agent
or the main session.

When to delegate:
- A task scoped to a single component goes to its agent. Give it a
  self-contained prompt: the goal, affected files, and acceptance
  criteria — agents start cold and read the docs themselves.
- Independent single-component tasks may run as parallel agents;
  they touch disjoint directories by construction.
- Work spanning components stays in the main session: decide the
  contract change first (shared storage models, the backend REST/WS
  surface, the runner's CLI flags and JSON-lines schema,
  `engines.json` schema), then hand each component its slice. An API
  shape change usually means backend-dev first, then frontend-dev to
  update `frontend/src/types/api.ts` in lockstep.
- Agents must not edit outside their component; if one reports a
  needed contract change, resolve it in the main session rather
  than relaunching the agent with broader scope.
- No agent owns `engines/my-engine/`. Tasks touching it do not get
  delegated, worked on, or reviewed — see the hard rules.

## Hard rules

- `engines/my-engine/` is human-only. Never create, modify,
  refactor, review, or advise on anything under this path — not
  even when asked to "just take a quick look". The engine is the
  human's project; the suite's job is to drive it over UCI.
- Engines are black boxes: every consumer speaks UCI over subprocess
  stdin/stdout via `shared/uci_client.py`. No component ever imports
  engine code, and engines never import infrastructure.
- Storage goes through the repository ABC (`shared/storage/`) only.
  No component reads or writes `data/` paths directly.
- The SPRT runner is a standalone CLI. The backend invokes it as a
  subprocess and reads JSON-lines from stdout — never as a library.
- The frontend knows only the backend's HTTP/WS API. API responses
  never expose filesystem paths or shell commands.
- Strict TDD (red → green → refactor); pyright strict and ruff must
  pass. `make lint` and `make test` are the pre-commit gates.
- Commit only when asked — conventional commits, messages explain
  the why. Never `git push` unless explicitly asked.
