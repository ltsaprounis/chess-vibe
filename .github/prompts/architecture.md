# Architecture

Architecture principles, ownership boundaries, and high-level component design for the chess-vibe monorepo.

## Principles

1. **Engines are black boxes.** Every consumer talks UCI over subprocess stdin/stdout. No engine code is ever imported directly. An engine can be Python, C++, Rust — doesn't matter. Copilot builds infrastructure that speaks UCI to opaque engine binaries; the human builds the engine that answers UCI.
2. **Storage is accessed only through the repository ABC.** No component reads or writes `data/` directly. This makes the `FileStore` → `SQLiteStore` swap possible without touching callers.
3. **The SPRT runner is a standalone CLI.** The backend invokes it as a subprocess, not a library call. This keeps them independently testable and avoids coupling their async runtimes.
4. **The frontend knows only HTTP/WebSocket.** It never talks to engines or storage directly — all interactions go through the backend's REST/WS API.
5. **Language-agnostic engine registry.** `engines.json` declares how to build and launch each engine. Adding a new engine requires only a new entry — no code changes.

## Ownership Boundary

| Owner | Scope | Details |
|---|---|---|
| **Human** | `engines/my-engine/` | 100% hand-written. Board representation, move generation, search, evaluation, UCI adapter, and all tests. Fully standalone — zero dependencies on the infrastructure. **Never generate, modify, or refactor any file under this path.** |
| **Copilot** | Everything else | `shared/`, `engines/random-engine/`, `engines/external/`, `sprt-runner/`, `backend/`, `frontend/`, `scripts/`, `data/`, and all project-level config. Builds all infrastructure that drives engines as UCI subprocesses. |

## High-Level Architecture

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
│  random-engine   │
│  stockfish       │
└──────────────────┘
```

## Component Boundaries

Every component communicates through a well-defined interface. No component reaches into another's internals.

| Boundary | From → To | Protocol / Mechanism | Data Exchanged |
|---|---|---|---|
| **Frontend ↔ Backend** | React → FastAPI | HTTP REST + WebSocket | REST: JSON (game data, SPRT status, engine list, opening books). WS `/ws/play`: JSON messages (player moves → engine moves + eval). WS `/ws/sprt/{id}`: JSON-lines progress updates. |
| **Backend → Engines** | `engine_pool.py` → any engine | UCI over stdin/stdout subprocess | `position`, `go`, `bestmove`, `info` lines. Engine launched via command from `engines.json`. Backend never imports engine code. |
| **Backend → SPRT Runner** | `sprt_service.py` → `runner.py` | CLI subprocess (stdout) | Backend invokes the runner as `sprt-runner/.venv/bin/python -m sprt_runner run --base ENGINE[:COMMIT] --test ENGINE[:COMMIT] ...` via `asyncio.create_subprocess_exec`. Runner streams JSON-lines progress to stdout; backend reads and relays to WebSocket. |
| **SPRT Runner → Engines** | `game.py` → any engine | UCI over stdin/stdout subprocess via `shared/uci_client.py` | Same UCI protocol as backend. Runner manages two engine subprocesses per game. |
| **SPRT Runner → Storage** | `runner.py` → `shared/storage/` | Python API (repository ABC) | Domain model objects (`Game`, `SPRTTest`). Runner calls `save_game()`, `update_sprt_results()`, etc. |
| **Backend → Storage** | routes/services → `shared/storage/` | Python API (repository ABC) | Same domain models. Backend calls `get_game()`, `list_games()`, `save_game()`, etc. |
| **Engine Registry** | Backend + SPRT Runner → `engines.json` | JSON config file | Engine id, name, directory, build command, run command. Read at startup; adding a new engine requires only a new entry. |
| **Storage → Filesystem** | `file_store.py` → `data/` | Flat files (PGN + JSON sidecars) | Hidden behind the repository ABC. Callers never touch the filesystem directly. Swappable to SQLite later. |

## Monorepo Structure

```
chess-vibe/
├── shared/                   # Shared Python package (installed by sprt-runner & backend)
│   └── src/shared/
│       ├── uci_client.py         # Async UCI subprocess wrapper
│       ├── time_control.py       # Time control models
│       └── storage/
│           ├── models.py         # Domain models (Game, Move, SPRTTest, etc.)
│           ├── repository.py     # Abstract persistence interface (ABC)
│           ├── file_store.py     # PGN/JSON flat file implementation
│           └── pgn_export.py     # PGN export utility
├── engines/
│   ├── random-engine/            # Minimal UCI engine (random legal moves)
│   ├── my-engine/                # HUMAN-ONLY — never touch
│   └── external/                 # Git submodules (e.g., Stockfish)
├── sprt-runner/                  # Custom SPRT testing framework (CLI)
│   └── src/sprt_runner/
│       ├── runner.py             # Match orchestrator
│       ├── sprt.py               # LLR calculation, stopping conditions
│       ├── game.py               # Single game loop
│       ├── adjudication.py       # Win/draw/tablebase adjudication
│       ├── worktree.py           # Git worktree management
│       └── openings.py           # Opening book loader
├── backend/                      # FastAPI server
│   └── src/backend/
│       ├── main.py
│       ├── ws/                   # WebSocket handlers (play, SPRT streaming)
│       ├── routes/               # REST endpoints (games, SPRT, engines)
│       └── services/             # Business logic (engine_pool, game_manager, sprt_service)
├── frontend/                     # React + TypeScript + Vite + Tailwind
│   └── src/
│       ├── components/           # Board, EvalBar, MoveList, SPRTDashboard
│       ├── hooks/                # useChessGame, useWebSocket
│       ├── pages/                # Play, SPRTTests, GameReplay
│       └── services/             # API client
├── engines.json                  # Engine registry (build & run commands)
├── data/                         # Persistent storage (PGN + JSON sidecars)
│   └── books/                    # Opening book files (EPD, PGN)
└── scripts/                      # Dev tooling (setup, lint, test)
```

## Key Design Decisions

- **UCI subprocess for everything** — Following the Stockfish/Fishtest/cutechess-cli convention. One code path, language agnosticism, process isolation (engine crash doesn't take down the server), and uniform behaviour between SPRT testing and play.
- **Custom SPRT over cutechess-cli** — Full control over orchestration, progress reporting, and integration with storage/frontend.
- **Hybrid concurrency for SPRT** — `multiprocessing` for game-level parallelism, `asyncio` inside each worker for non-blocking UCI I/O. Failure isolation and stable timing under load.
- **Monotonic deadline timing** — All clock accounting uses `time.monotonic_ns()` with deadline checks. Never wall-clock time for adjudication or time-loss decisions.
- **Git worktrees for SPRT version testing** — The SPRT runner uses git worktrees to build and test engines at specific commits. Each `ENGINE:COMMIT` spec creates a worktree at that commit, reads `engines.json` for build/run commands, builds the engine, and returns the binary path. Supports both regression testing (same engine, two commits) and cross-engine comparison with a single CLI.
- **Evaluations stored at game time** — Both during play (WebSocket) and SPRT (game runner). Enables instant replay without re-computation.
- **Isolated venvs per component** — Each Python component and engine has its own venv to prevent dependency conflicts. Cross-component subprocess calls use the target component's interpreter explicitly.
- **Flat files first, SQLite later** — Start with `FileStore` (PGN + JSON sidecars). Structured filter dataclasses (`GameFilter`, `SPRTTestFilter`) ensure no filesystem assumptions leak to callers and map cleanly to SQL `WHERE` clauses.
- **Monorepo with submodules for external engines** — Single repo for project code, git submodules for third-party engines (e.g., Stockfish) with thin wrappers.
- **Openings owned by infrastructure, not engines** — The engine is a pure searcher. Opening book selection and book-pair generation are the responsibility of the SPRT runner and backend.
