## Plan: Chess Engine Suite — Monorepo Architecture

A monorepo containing a Python chess engine (classical → NNUE), a custom SPRT testing framework, a FastAPI backend, and a React/TypeScript frontend. The engine exposes a UCI interface; all consumers — the backend, SPRT runner, and any future tooling — interact with every engine (internal and external) exclusively as UCI subprocesses via `shared/uci_client.py`. No engine code is ever imported directly. External engines (Stockfish, etc.) are integrated via git submodules. The SPRT runner orchestrates games via UCI subprocesses, enabling testing of any UCI-compatible engine. Evaluations are stored at game time for fast replay. The persistence layer uses flat files (PGN + JSON sidecars) behind an abstract repository interface, designed so a SQLite backend can be dropped in later without changing any callers.

**Ownership — who builds what:**

| Owner | Scope | Details |
|---|---|---|
| **Human (me)** | `engines/my-engine/` | 100% hand-written. Board representation, move generation, search, evaluation, UCI adapter, and all tests. Fully standalone — zero dependencies on the infrastructure. **Copilot must never generate, modify, or refactor any file under `engines/my-engine/`.** |
| **Copilot** | Everything else | `shared/`, `engines/random-engine/`, `engines/external/`, `sprt-runner/`, `backend/`, `frontend/`, `scripts/`, `data/`, and all project-level config. Copilot builds all infrastructure that drives engines as UCI subprocesses — it never reaches into any engine's internals. |

The boundary is the **UCI protocol**. Copilot builds infrastructure that speaks UCI to opaque engine binaries; the human builds the engine that answers UCI.

**Component boundaries:**

Every component communicates through a well-defined interface. No component reaches into another's internals.

| Boundary | From → To | Protocol / Mechanism | Data exchanged |
|---|---|---|---|
| **Frontend ↔ Backend** | React → FastAPI | HTTP REST + WebSocket | REST: JSON (game data, SPRT status, engine list, opening books). WS `/ws/play`: JSON messages (player moves → engine moves + eval). WS `/ws/sprt/{id}`: JSON-lines progress updates. |
| **Backend → Engines** | `engine_pool.py` → any engine | UCI over stdin/stdout subprocess | `position`, `go`, `bestmove`, `info` lines. Engine launched via command from `engines.json`. Backend never imports engine code. |
| **Backend → SPRT Runner** | `sprt_service.py` → `runner.py` | CLI subprocess (stdout) | Backend invokes the runner as `sprt-runner/.venv/bin/python -m sprt_runner run --base ENGINE[:COMMIT] --test ENGINE[:COMMIT] ...` via `asyncio.create_subprocess_exec`, using the SPRT runner's own venv interpreter (path resolved relative to repo root). Runner streams JSON-lines progress to stdout; backend reads and relays to WebSocket. |
| **SPRT Runner → Engines** | `game.py` → any engine | UCI over stdin/stdout subprocess via `shared/uci_client.py` | Same UCI protocol as backend. Runner manages two engine subprocesses per game. |
| **SPRT Runner → Storage** | `runner.py` → `shared/storage/` | Python API (repository ABC) | Domain model objects (`Game`, `SPRTTest`). Runner calls `save_game()`, `update_sprt_results()`, etc. |
| **Backend → Storage** | routes/services → `shared/storage/` | Python API (repository ABC) | Same domain models. Backend calls `get_game()`, `list_games()`, `save_game()`, etc. |
| **Engine Registry** | Backend + SPRT Runner → `engines.json` | JSON config file | Engine id, name, directory, build command, run command. Read at startup; adding a new engine requires only a new entry, no code changes. The `build` field makes this language-agnostic (Python, C++, Rust, etc.). |
| **Storage → Filesystem** | `file_store.py` → `data/` | Flat files (PGN + JSON sidecars) | Hidden behind the repository ABC. Callers never touch the filesystem directly. Swappable to SQLite later. |

```
                    ┌──────────────┐
                    │   Frontend   │
                    │   (React)    │
                    └──────┬───────┘
                           │ HTTP / WebSocket
                           ▼
┌──────────────────┐  CLI subprocess  ┌──────────────────┐
│     Backend      │─────────────────►│   SPRT Runner    │
│    (FastAPI)     │   JSON-lines     │     (CLI)        │
├──────────────────┤                  ├──────────────────┤
│  engine_pool     │                  │  runner.py       │
│  game_manager    │                  │  game.py         │
│  sprt_service    │                  │  worktree.py     │
└────────┬─────────┘                  └────────┬─────────┘
         │ Python API               Python API │
         │                                     │
         ▼                                     ▼
┌──────────────────────────────────────────────────────────┐
│                         Shared                           │
│  uci_client · storage/ (repository ABC) · time_control   │
└──────┬──────────────────────────┬────────────────────────┘
       │                          │
       │ UCI subprocess           │ Flat files
       │ stdin/stdout             ▼
       │                   data/ (PGN + JSON)
       ▼
┌──────────────────┐
│     Engines      │
│    (UCI bins)    │
├──────────────────┤
│  my-engine       │
│  random-eng      │
│  stockfish       │
└──────────────────┘
```

Key invariants:
- **Engines are black boxes.** Every consumer talks UCI over subprocess stdin/stdout. No engine code is imported. An engine can be Python, C++, Rust — doesn't matter.
- **Storage is accessed only through the repository ABC.** No component reads/writes `data/` directly. This makes the FileStore → SQLite swap possible without touching callers.
- **The SPRT runner is a standalone CLI.** The backend invokes it as a subprocess, not a library call. This keeps them independently testable and avoids coupling their async runtimes.
- **The frontend knows only HTTP/WebSocket.** It never talks to engines or storage directly — all interactions go through the backend's REST/WS API.

**Monorepo structure:**

```
chess-app-prototype/
├── shared/                   # Shared Python package (installed by sprt-runner & backend)
│   ├── pyproject.toml        # [tool.setuptools.packages.find] namespaces under src/
│   └── src/
│       └── shared/           # Python package: `import shared`
│           ├── __init__.py
│           ├── uci_client.py     # Async UCI subprocess wrapper (used by SPRT runner & backend)
│           ├── storage/
│           │   ├── __init__.py
│           │   ├── models.py     # Domain models & structured filter dataclasses
│           │   ├── repository.py # Abstract persistence interface (ABC)
│           │   ├── file_store.py # PGN/JSON flat file implementation
│           │   ├── pgn_export.py # PGN export utility (for sharing games externally)
│           │   └── sqlite_store.py  # (future) SQLite drop-in replacement
│           └── time_control.py   # Time control models (used by SPRT runner & backend)
├── engines/
│   ├── random-engine/        # Minimal UCI engine — picks legal moves at random
│   │   ├── pyproject.toml    # Depends on python-chess for legal move gen
│   │   └── src/
│   │       └── random_engine/    # Python package: `python -m random_engine`
│   │           ├── __init__.py
│   │           ├── __main__.py   # Entry point (enables `python -m random_engine`)
│   │           ├── uci.py        # UCI I/O parsing (reusable pattern)
│   │           └── engine.py     # Engine logic + state (swap this to build a real engine)
│   ├── my-engine/            # HUMAN-ONLY — Copilot must not touch. Standalone, no infra dependencies.
│   └── external/             # Git submodules + wrapper scripts
│       └── stockfish/
│           └── stockfish/    # Submodule → official repo
├── sprt-runner/              # Custom SPRT framework (Python, own venv, depends on shared/)
│   ├── pyproject.toml
│   └── src/
│       └── sprt_runner/      # Python package: `python -m sprt_runner`
│           ├── __init__.py
│           ├── __main__.py   # CLI entry point (enables `python -m sprt_runner`)
│           ├── runner.py     # Match orchestrator
│           ├── sprt.py       # LLR calculation, stopping conditions
│           ├── worktree.py   # Git worktree management
│           ├── game.py       # Single game loop (enforces rules + adjudication)
│           ├── adjudication.py   # Win/draw/tablebase adjudication rules
│           ├── openings.py   # Opening book loader & pair generator
│           └── tests/
├── backend/                  # FastAPI server (depends on shared/)
│   ├── pyproject.toml
│   └── src/
│       └── backend/          # Python package: `python -m backend`
│           ├── __init__.py
│           ├── main.py
│           ├── ws/               # WebSocket handlers
│           │   ├── __init__.py
│           │   └── play.py       # Play-vs-engine WS endpoint
│           ├── routes/
│           │   ├── __init__.py
│           │   ├── sprt.py       # SPRT test CRUD + status
│           │   ├── games.py      # Game replay, history
│           │   └── engines.py    # Engine management
│           ├── services/
│           │   ├── __init__.py
│           │   ├── game_manager.py   # Manages active games
│           │   ├── sprt_service.py   # Invokes SPRT runner via CLI subprocess
│           │   └── engine_pool.py    # Engine process lifecycle
│           └── tests/
├── engines.json              # Engine registry — declares how to build & launch each engine
├── data/                     # Top-level data directory (shared across components)
│   └── books/                # Opening book files (EPD, PGN)
│       └── default.epd       # ~50 quiet opening positions, shipped with the repo
├── frontend/                 # React + TypeScript
│   ├── package.json
│   ├── src/
│   │   ├── components/
│   │   │   ├── Board/        # Interactive chessboard
│   │   │   ├── EvalBar/      # Evaluation display
│   │   │   ├── MoveList/     # Move history + navigation
│   │   │   └── SPRTDashboard/ # Test status, Elo, LLR
│   │   ├── hooks/
│   │   │   ├── useChessGame.ts
│   │   │   └── useWebSocket.ts
│   │   ├── pages/
│   │   │   ├── Play.tsx
│   │   │   ├── SPRTTests.tsx
│   │   │   └── GameReplay.tsx
│   │   └── services/
│   │       └── api.ts
│   └── tsconfig.json
├── .github/
│   └── workflows/
│       └── ci.yml            # Lint + type-check + test on every push & PR
└── scripts/                  # Dev tooling, build, venv setup
```

**Steps — Copilot track**

These steps are fully independent of the human track. Copilot can build, test, and verify the entire infrastructure using `random-engine` (and optionally Stockfish) — no dependency on `my-engine` at any point. Once `my-engine` is ready, it plugs in as just another UCI binary with zero infrastructure changes.

1. **Project Scaffolding & Venv Isolation** — Set up the monorepo structure, `pyproject.toml` for each Python component (`shared/`, `engines/random-engine/`, `sprt-runner/`, `backend/`), and a top-level `scripts/setup.sh` that bootstraps all venvs with `uv` (for example, `uv venv .venv && uv sync`). Both `sprt-runner` and `backend` declare `shared` as a path dependency (e.g., `shared = {path = "../shared"}`). The SPRT runner's `worktree.py` creates temporary venvs for each engine version under test (via `uv venv`). Because each component has its own venv, cross-component subprocess calls must use the target component's interpreter explicitly. The backend invokes the SPRT runner via `sprt-runner/.venv/bin/python -m sprt_runner ...` (path resolved relative to repo root, stored in a config constant). `scripts/setup.sh` ensures all venvs are created and dependencies installed so these paths are valid after setup.

2. **CI Pipeline & Code Quality** — Set up immediately after scaffolding so every subsequent step is gated by lint, type-check, and test from the first PR. Lives in `.github/workflows/ci.yml`, triggers on every push and pull request.

   **GitHub Actions workflow** with three job groups:
   - **Lint & Format** (single job): Runs **Ruff** (`ruff check` + `ruff format --check`) across all Python components. Runs **ESLint** + **Prettier** (`--check`) on the frontend. Configured at the repo root: `ruff.toml` for Python, `.eslintrc.cjs` + `.prettierrc` for TypeScript. Ruff is declared as a dev dependency in each Python component's `pyproject.toml`.
   - **Type-check** (single job): Runs **Pyright** (strict mode) across `shared/`, `sprt-runner/`, and `backend/`. Each component configures Pyright via `[tool.pyright]` in its own `pyproject.toml`. Runs `tsc --noEmit` on the frontend. Each Python component's `pyproject.toml` declares `pyright` as a dev dependency.
   - **Test** (matrix job — one per Python component + frontend): Bootstraps each component's venv with `uv`, runs `pytest` with coverage reporting. Frontend job runs `vitest run --coverage`. Uses `uv` for fast dependency installation. The matrix covers `shared`, `random-engine`, `sprt-runner`, and `backend` as separate entries so failures are isolated and clearly attributed.

   **Local developer experience**: Add a `scripts/lint.sh` that runs the same lint + format + type-check commands locally (so developers can fix issues before pushing). Consider adding a **pre-commit** config (`.pre-commit-config.yaml`) with Ruff and Prettier hooks for auto-formatting on commit, but keep it optional — CI is the enforcer.

   The pipeline starts with mostly no-op test jobs (since code hasn't been written yet) but lint and type-check are enforced from day one. As components are built in subsequent steps, tests accumulate and the CI safety net grows automatically.

3. **Shared Package: UCI Client & Time Control** — Build the foundational shared utilities:
   - `uci_client.py`: Async wrapper around a UCI engine subprocess. Sends commands, parses responses, handles timeouts and crashes. Shared by the SPRT runner and the backend's engine pool.
   - `time_control.py`: Models for fixed-time, increment, and depth/nodes time controls. Shared so both the SPRT runner and backend use identical TC representations.

4. **Build the Random Engine** — A minimal but well-structured UCI-compatible engine in `engines/random-engine/`. Uses `python-chess` for board state and legal move generation. On `go`, picks a random legal move, reports `info score cp 0 depth 0`, and returns `bestmove`. Supports the full UCI handshake (`uci`, `isready`, `position`, `go`, `quit`). Serves as: (a) an end-to-end smoke test for the SPRT runner and backend before the real engine exists, (b) a baseline opponent for early SPRT testing, (c) **a clean, readable template for building more complex engines later**. Design priorities: clear separation between UCI I/O parsing, engine logic, and state management; well-named functions and types; thorough docstrings and inline comments explaining the UCI protocol and why each piece exists; easy to copy and extend (a developer should be able to fork this into a new engine directory and start replacing the random move selection with real search/eval without restructuring anything).

5. **Engine Registry** — Add an `engines.json` config file at the repo root that declares available engines. Each entry specifies how to build and launch the engine, making the registry **language-agnostic** — works for Python, C/C++, Rust, or any language that produces a UCI-speaking binary. The backend reads this to populate the engine selection dropdown and spawn UCI subprocesses. The SPRT runner's `worktree.py` reads it to know how to build and launch engines from any commit.

   ```json
   [
     {
       "id": "random",
       "name": "Random Engine",
       "dir": "engines/random-engine",
       "build": "uv venv .venv && uv pip install --python .venv/bin/python -e .",
       "run": ".venv/bin/python -m random_engine"
     },
     {
       "id": "stockfish",
       "name": "Stockfish 17",
       "dir": "engines/external/stockfish/stockfish",
       "build": "cd src && make -j build ARCH=x86-64-modern",
       "run": "./src/stockfish"
     }
   ]
   ```

   Fields:
   - `id`: Unique identifier used in SPRT tests and game records
   - `name`: Human-readable display name (shown in frontend)
   - `dir`: Path to the engine directory (relative to repo root)
   - `build`: Shell command to build the engine (run from `dir`). For Python: `uv venv .venv && uv pip install --python .venv/bin/python -e .`. For C++: `cmake -B build && cmake --build build`. Can be `null` for pre-built binaries.
   - `run`: Shell command to launch the UCI binary (run from `dir`)

   When `my-engine` is ready, the human simply adds an entry — no code changes needed anywhere:
   ```json
   {
     "id": "my-engine",
     "name": "My Engine",
     "dir": "engines/my-engine",
     "build": "uv venv .venv && uv pip install --python .venv/bin/python -e .",
     "run": ".venv/bin/python -m my_engine"
   }
   ```

6. **External Engine Integration** — Add Stockfish as a git submodule under `engines/external/stockfish/stockfish/`. Register it in `engines.json`. The backend and SPRT runner interact with external engines exclusively via `shared/uci_client.py`, driving them as UCI subprocesses. The backend's `engine_pool.py` manages external engine subprocesses directly.

7. **Persistence Abstraction** — Lives in `shared/src/shared/storage/`, installed as a dependency by both `sprt-runner` and `backend`. The storage layer has three parts:
   - `models.py`: Domain dataclasses — `Game`, `Move` (with eval fields: `score_cp`, `score_mate`, `depth`, `seldepth`, `pv`, `nodes`, `time_ms`, `clock_white_ms`, `clock_black_ms`), `SPRTTest`, `Engine`, `OpeningBook`. Also **structured filter dataclasses** — `GameFilter(sprt_test_id, result, engine_id, opening_name, ...)` and `SPRTTestFilter(status, engine_id, ...)`. These are storage-agnostic: callers build a filter object, the repository maps it to its native query mechanism. This ensures no filesystem assumptions leak through the interface and the filters map cleanly to SQL `WHERE` clauses later.
   - `repository.py`: Abstract base class (ABC) defining `GameRepository` (`save_game(game)`, `get_game(id) → Game`, `list_games(GameFilter) → list[Game]`) and `SPRTTestRepository` (`save_sprt_test(test)`, `get_sprt_test(id)`, `list_sprt_tests(SPRTTestFilter)`, `update_sprt_results(id, wins, losses, draws, llr)`). Methods accept and return domain models only — no dicts, no file paths, no SQL.
   - `file_store.py`: Initial implementation using flat files. Directory layout:

     ```
     data/
     ├── sprt-tests/
     │   └── {test-id}/
     │       ├── meta.json                  # SPRT test metadata (engines, TC, Elo bounds, status, W/D/L)
     │       └── games/
     │           ├── {game-id}.pgn          # Game in PGN format
     │           └── {game-id}.eval.json    # Per-move evaluations sidecar
     └── play/
         ├── {game-id}.pgn                  # Casual play game
         └── {game-id}.eval.json            # Per-move evaluations sidecar
     ```

     SPRT games are nested under their test directory; casual play games live under `data/play/`. Filtering scans directories and loads JSON metadata — acceptable at small scale (hundreds of tests, thousands of games). When this becomes a bottleneck, drop in `sqlite_store.py` implementing the same ABCs. **Concurrent write safety**: Since the SPRT runner uses `multiprocessing`, multiple workers may call `save_game()` simultaneously. `FileStore` uses atomic writes (write to a temporary file in the same directory, then `os.rename()` to the final path) and generates game IDs with UUIDs to prevent collisions. Directory creation uses `os.makedirs(exist_ok=True)`. No file locking needed since each game writes to unique file paths.
   - `pgn_export.py`: Utility to export any `Game` domain object to standard PGN format (with eval annotations in comments). Used for sharing games with external tools regardless of storage backend.

8. **Custom SPRT Runner** — The most complex component:
   - `game.py`: Plays a single game between two `UciClient`s. Manages the game loop: alternate moves, enforce time control, detect termination (checkmate, stalemate, draw rules, timeout, crash). Delegates to `adjudication.py` after each move to check for early termination. Records all moves and per-move evaluations.
   - `adjudication.py`: Configurable adjudication rules to end games early and speed up SPRT testing. **Win adjudication**: declare a win if both engines agree the eval exceeds a threshold (e.g., ±1000cp) for N consecutive moves. **Draw adjudication**: declare a draw if both evals are near zero (e.g., |eval| < 10cp) for N consecutive moves. **Tablebase adjudication**: declare the result from Syzygy tablebases when piece count drops below the TB threshold. All thresholds are configurable per SPRT test.
   - `sprt.py`: Implements the Sequential Probability Ratio Test. Tracks W/D/L counts, calculates Log-Likelihood Ratio (LLR) against Elo bounds (e.g., `[0, 5]`), determines when to stop (accept H1, accept H0, or continue). Uses the BayesElo or logistic model.
  - `worktree.py`: Creates git worktrees for specified commits/branches. Receives an `engine_id[:commit]` spec — when a commit is present, creates a worktree at that commit, reads `engines.json` from the worktree for the target engine's `build` and `run` commands, executes the build step, and returns the path to the built engine binary. When no commit is given, uses the engine from the current working tree. Language-agnostic — works for Python (`uv venv .venv && uv pip install --python .venv/bin/python -e .`), C++ (`cmake && make`), or any build system. **Edge-case handling**: If `engines.json` does not exist in the worktree (e.g., the commit predates the registry), fall back to reading `engines.json` from the current working tree and log a warning. If the engine entry is missing or the schema differs, abort with a clear error message identifying the commit and expected engine id. For cross-engine tests (e.g., `--base random --test my-engine:abc123`), each side independently resolves its own `engines.json` — the non-commit side reads from the current tree, the commit-pinned side reads from its worktree (with the fallback described above).
   - `openings.py`: Loads opening books (EPD files with FEN positions, or PGN files with move sequences). Generates **book pairs** — each opening is played twice with colors swapped to eliminate opening bias. Tracks which openings have been used per test to avoid repeats. Supports random selection and sequential iteration.
   - `runner.py`: Orchestrates a full SPRT test. Exposes a **CLI entry point** with `--base` and `--test` flags that accept `ENGINE` or `ENGINE:COMMIT` format:

     ```bash
     # Same engine, two commits (Fishtest-style regression test)
     python -m sprt_runner run --base my-engine:abc123 --test my-engine:def456 \
       --tc "3+0.2" --elo0 0 --elo1 5 --book noob_3moves --concurrency 4 \
       --adjudicate-win 1000,5 --adjudicate-draw 10,8

     # Two different engines at current HEAD (cross-engine comparison)
     python -m sprt_runner run --base random --test my-engine \
       --tc "3+0.2" --elo0 0 --elo1 5 --book noob_3moves --concurrency 4
     ```

     Each side is parsed as `engine_id[:commit]`. When a commit is given, `worktree.py` creates a git worktree at that commit, looks up the engine's `build`/`run` commands from `engines.json`, builds, and returns the binary path. When no commit is given, the engine is used from the current working tree. This supports both regression testing (same engine, two commits) and cross-engine comparison (two different engines) with a single, uniform CLI.

     **JSON-lines protocol**: All runner output to stdout follows a structured JSON-lines format. Every line is a JSON object with a `type` field: `game_result` (completed game with W/D/L and game id), `progress` (current W/D/L counts, LLR, games played), `error` (build failure, engine crash, worktree failure — includes `engine_id`, `message`, and `severity`: `fatal` aborts the test, `game` skips/replays that game), `complete` (final result with accept/reject decision). Unstructured errors (unexpected exceptions) are written to **stderr** only, never mixed into the JSON-lines stream. The backend parses `type` to route messages appropriately (relay progress to WebSocket, surface errors in the UI, update storage on completion).

    Takes: base/test engine specs, time control, Elo bounds, opening book, adjudication config, concurrency. Creates worktrees (if commits specified), builds engines, draws opening pairs from the book, runs games with a **hybrid concurrency model**: process-level parallelism via `multiprocessing` (one worker process per game) and async UCI subprocess I/O inside each worker via `asyncio`. **IPC**: Workers report completed game results and progress updates back to the coordinator process via a `multiprocessing.Queue`. Each message is a typed dict with a `type` field (`game_result`, `progress`, `error`). The coordinator drains the queue, aggregates SPRT statistics (keeping updates deterministic — single-threaded aggregation), and writes JSON-lines to stdout for the backend to consume. Uses strict timing based on `time.monotonic_ns()` deadlines (never wall clock), per-move watchdog timeouts, and explicit timeout/crash adjudication outcomes. Streams progress to stdout (JSON lines). The backend's `sprt_service.py` invokes this CLI as a subprocess, reading JSON-lines progress from stdout to relay via WebSocket.

9. **FastAPI Backend** — The central server:
   - **WebSocket `/ws/play`**: Manages a live game session. On connect, spawns an engine as a UCI subprocess via `shared/uci_client.py` — looks up the engine command from `engines.json`. The engine is an opaque binary; the backend never imports engine code directly. Client sends moves, server relays to the engine via UCI, responds with engine moves + eval. Stores completed games with evaluations.
   - **REST `POST /sprt/tests`**: Starts an SPRT test. Accepts: base and test engine specs (each as `engine_id` or `engine_id:commit`), time control, Elo bounds, opening book selection, adjudication config. The backend invokes the SPRT runner CLI as a background subprocess (via `asyncio.create_subprocess_exec` with `--base`/`--test` flags), reading JSON-lines progress from stdout. Returns test ID.
   - **REST `GET /openings/books`**: Lists available opening books. **REST `POST /openings/books`**: Upload a new opening book (EPD/PGN).
   - **REST `GET /sprt/tests/{id}`**: Returns test status (LLR, W/D/L, Elo estimate, games played, running/complete).
   - **WebSocket `/ws/sprt/{id}`**: Streams live SPRT progress updates.
   - **REST `GET /games/{id}`**: Returns a completed game with per-move evaluations for replay.
   - **REST `GET /games`**: Lists/filters games (by SPRT test, date, engine, etc.).
   - **REST `GET /engines`**: Returns the list of registered engines from `engines.json`.
   - `engine_pool.py`: Manages engine subprocess lifecycles, prevents resource exhaustion.

   **SPRT test recovery**: The backend tracks running SPRT tests in storage with a `status` field (`running`, `completed`, `failed`, `cancelled`). On startup, the backend scans for tests with `status=running` and marks them as `failed` (since the subprocess handle is lost on restart). The frontend surfaces these as "interrupted" tests that can be re-launched. The runner itself is stateless and idempotent — re-running a test creates a new test entry. For graceful cancellation, the backend sends `SIGTERM` to the runner subprocess; the runner catches it, writes a `complete` JSON-line with `result: cancelled`, and exits cleanly.

10. **React Frontend** — Built with **Vite** (React + TypeScript template). Uses **Tailwind CSS** for styling. No global state manager — local component state + React context for the active game session is sufficient at this scale. Vite dev server proxies `/api` and `/ws` to the FastAPI backend (configured in `vite.config.ts`) to avoid CORS issues during development. The FastAPI backend also configures **CORS middleware** (`fastapi.middleware.cors.CORSMiddleware`) allowing the Vite dev server origin, for cases where the proxy isn't used. Three main pages:
   - **Play**: Interactive chessboard (use `react-chessboard` + `chess.js`), engine selection dropdown (populated from `GET /engines`), optional opening position picker, eval bar, move list with eval annotations. Communicates via WebSocket.
   - **SPRT Tests**: Dashboard showing active/completed tests. Create new test form (branch/commit selectors, time control, Elo bounds, opening book selector). Live LLR chart and W/D/L stats via WebSocket.
   - **Game Replay**: Load any game (from SPRT or play). Step through moves with eval bar, PV display, and eval graph over time. Use stored evaluations.

11. **Testing Infrastructure** — Each component includes automated tests:
    - **Python components** (`shared/`, `sprt-runner/`, `backend/`): Use **pytest** as the test framework. Each component's `pyproject.toml` declares `pytest` as a dev dependency. Tests live alongside the code (e.g., `sprt-runner/src/sprt_runner/tests/`, `backend/src/backend/tests/`). Key test areas: UCI client (mock subprocess, verify protocol parsing), storage (round-trip save/load, filter correctness), SPRT math (known LLR values), adjudication logic, game loop (mock engines with scripted responses), backend routes (FastAPI `TestClient`), and engine pool lifecycle.
    - **Frontend**: Use **Vitest** (Vite-native) + **React Testing Library**. Test hooks, WebSocket reconnection logic, and component rendering. E2E tests are out of scope for now.
    - **`scripts/test.sh`**: Top-level script that runs all test suites (activates each venv, runs `pytest`, then runs `vitest`).

12. **Default Opening Book** — Include a minimal opening book in `data/books/` so the SPRT runner is functional out of the box. Ship a `default.epd` file containing ~50 quiet, well-known opening positions (e.g., from the UHO or Noomen test suites — publicly available EPD collections). The `--book` CLI flag defaults to `default` if not specified. Document in the README how to add custom books (drop an EPD or PGN file into `data/books/` and reference it by filename stem).

**Copilot verification** (all using `random-engine` — no dependency on `my-engine`):

- UCI client: Spawn `random-engine` as a subprocess, send `uci`/`isready`/`position startpos`/`go movetime 100`/`quit`, verify valid responses
- SPRT: Run a short SPRT test (`--base random --test random`, or `--base random:HEAD~1 --test random:HEAD` for commit-based), verify games complete, LLR is calculated, and results are stored with evals
- Play: Open frontend, select `random-engine`, play a game, verify moves + eval display via WebSocket
- Replay: Open a completed game, step through moves, verify stored evals render correctly
- External engine: Register Stockfish in `engines.json`, play against it via the frontend
- Tests: Run `scripts/test.sh` — all pytest and vitest suites pass
- CI: Push a branch, verify the GitHub Actions workflow runs lint, type-check, and test jobs; confirm a deliberately broken lint rule fails the pipeline
- Opening book: Verify `data/books/default.epd` exists and SPRT runner accepts `--book default`

---

**Steps — Human track**

These steps are done independently by the human whenever ready. The infrastructure (Copilot track) is fully functional without them — it works end-to-end with `random-engine`.

1. **Build the Engine Core** — Implement board representation, move generation, and a basic alpha-beta search with HCE in `engines/my-engine/`. The engine has its own board representation and move generator — it does **not** depend on `python-chess` (which is used by the infrastructure). The core has zero I/O — it's a pure computation layer.

2. **UCI Adapter** — Implement UCI protocol in `engines/my-engine/`. Reads stdin, writes stdout. Supports `uci`, `isready`, `position`, `go` (with all time control variants: `movetime`, `wtime/btime/winc/binc`, `depth`, `nodes`), `stop`, `quit`. This makes the engine a standalone UCI binary.

3. **Register the Engine** — Add an entry for `my-engine` in `engines.json`. The engine is now available in the frontend dropdown, the SPRT runner, and all other infrastructure — zero code changes needed.

**Human verification**:

- Run the UCI adapter manually (`echo "uci\nisready\nposition startpos\ngo movetime 1000\nquit" | python -m my_engine`) and verify valid output
- Play against `my-engine` via the frontend
- Run an SPRT test: `--base random --test my-engine` (cross-engine) or `--base my-engine:abc123 --test my-engine:def456` (regression)

**Decisions**

- **UCI subprocess for everything**: Following the Stockfish/Fishtest/cutechess-cli convention, all engines are driven as opaque UCI subprocesses — no direct code imports. This gives: (a) one code path instead of two, (b) language agnosticism (Python engine today, C++ tomorrow — nothing changes), (c) process isolation (engine crash doesn't take down the server), (d) uniform behaviour between SPRT testing and play. The latency of a local UCI subprocess is negligible for human play.
- **Custom SPRT over cutechess-cli**: More work but full control over orchestration, progress reporting, and integration with the storage/frontend
- **Hybrid concurrency for strict timing**: Use `multiprocessing` for game-level parallelism and `asyncio` inside each worker for non-blocking UCI stdin/stdout handling. This gives failure isolation and stable timing under load while keeping UCI communication efficient.
- **Monotonic deadline timing model**: All clock accounting and move deadlines use `time.monotonic_ns()` with deadline checks and per-move watchdogs. Never use wall-clock time for adjudication/time-loss decisions.
- **Flat files first, SQLite later**: Start with `FileStore` (PGN + JSON sidecars) for simplicity and debuggability. The repository ABC with structured filter dataclasses (`GameFilter`, `SPRTTestFilter`) ensures no filesystem assumptions leak to callers. When querying across thousands of games becomes slow, implement `SQLiteStore` as a drop-in replacement — the structured filters map directly to SQL `WHERE` clauses. A `pgn_export.py` utility ensures games can always be exported to standard PGN regardless of backend
- **Evaluations stored at game time**: Both during play (from WebSocket) and SPRT (from game runner). Enables instant replay without re-computation
- **Isolated venvs per engine**: Prevents dependency conflicts, especially important when NNUE brings in PyTorch/ONNX later
- **Monorepo with submodules for external engines**: Single repo for your code, submodules for third-party engines with thin wrappers
- **Openings owned by infrastructure, not engines**: The engine is a pure searcher — it receives a position and returns a move. Opening book selection and book-pair generation are the responsibility of the SPRT runner (for testing) and the backend/GUI (for play). This ensures fair SPRT testing (both engines face identical openings with colors swapped) and keeps the engine's concerns minimal

**Future Improvements**

- **C/C++ engine support**: Already handled by the `engines.json` registry — each engine declares its own `build` and `run` commands, so the SPRT runner and backend work identically for Python, C++, Rust, or any language that produces a UCI binary. Adding a C++ engine requires only a new `engines.json` entry and source directory.
- **SQLite storage backend**: Implement `SQLiteStore` as a drop-in replacement for `FileStore` when flat-file querying becomes a bottleneck across thousands of SPRT games. The structured filter dataclasses (`GameFilter`, `SPRTTestFilter`) map directly to SQL `WHERE` clauses.
- **OpenTelemetry observability**: Cross-cutting tracing and metrics across the backend and SPRT runner. The engine is never instrumented — measured from the outside via spans around `go()` calls. See [otel-strategy.md](otel-strategy.md) for full strategy, including cross-process context propagation, exporter phases, and what to measure.
