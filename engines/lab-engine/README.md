# Lab Engine

The baseline engine for the autoresearch loop in
[#190](https://github.com/ltsaprounis/chess-vibe/issues/190): a UCI chess
engine written in Python and compiled with [numba](https://numba.pydata.org/).
An agent improves it one change at a time, and the SPRT runner decides whether
each change helps.

It's deliberately plain, so the loop has room to improve it and each gain can
be traced to one change.

## What it does

- **Board:** a 10x12 mailbox, so moves walk off the board onto a border rather
  than needing bounds checks (`board.py`).
- **Moves:** pseudo-legal generation, with each move made and taken back to
  test whether it leaves the king in check (`movegen.py`). Perft matches the
  published counts for the six standard test positions.
- **Search:** iterative-deepening negamax alpha-beta. Each depth tries the
  previous depth's principal variation first, then captures by most valuable
  victim and least valuable attacker, then promotions, then quiet moves
  (`search.py`).
- **Evaluation:** material, plus piece-square bonuses built from simple rules:
  advanced and central pawns, centralised knights and bishops, rooks on the
  seventh, a king at home in a corner (`evaluate.py`).
- **Time:** a soft limit (start no new depth after it) and a hard one (abandon
  the search there), from the clock, the increment and `movestogo`, or from
  `movetime` (`timeman.py`).

## What it leaves out

On purpose, these are for the loop to add, one at a time:

- a transposition table;
- quiescence search, so captures at the horizon are misjudged;
- null-move pruning, and any reductions or extensions;
- any trained evaluation;
- draws by repetition or the fifty-move rule in the search (the runner still
  applies both rules to the game);
- separate middlegame and endgame tables, mobility, pawn structure and king
  safety;
- killer moves and history.

## Dependencies

Python 3.13, with `numba==0.68.1` and `numpy==2.5.4`. Both have Python 3.13
wheels for macOS arm64 and Linux x86_64, and numba 0.68.1 declares
`numpy<2.6,>=1.22`. #190 allows pytorch too; anything else needs a measured case
and the maintainer's approval, and `python-chess` isn't allowed in the engine.

## Build and run

`engines.json` registers it as `lab-engine`:

- **build:** `uv venv --clear --python 3.13 .venv && uv pip install --python .venv/bin/python -e . && .venv/bin/python -m lab_engine.warm`
- **run:** `.venv/bin/python -m lab_engine`

`make setup` runs that build, as it does every registered engine's. The build
names Python 3.13, so every build gets the same minor version; the
measurements below ran on 3.13.18.

The UCI commands are `uci`, `isready`, `ucinewgame`, `position`, `go`, `stop`
and `quit`; anything else is ignored. `go` takes `wtime`, `btime`, `winc`,
`binc`, `movestogo`, `movetime`, `depth`, `nodes`, `mate` and `infinite`.
`go infinite`, and `go` on its own, search until `stop` and hold `bestmove`
until then, even past a forced mate. `go mate n` searches 2n plies, the depth
at which a mate in n shows. After each completed depth it writes
`info depth D score cp S nodes N nps R time T pv ...`, with the score from its
own point of view (`score mate 3` and `score mate -3` for mates), as the
runner's adjudication expects.

## Compiling with numba

Every compiled function is declared with its numba signature, through
`lab_engine.jit.compiled`. numba then compiles it when its module is imported,
and refuses to compile anything after that. So the engine compiles nothing once
it has answered `readyok`, and its first `go` doesn't stop to compile. Before
reading its first command, it also runs one depth-1 search, which takes about
18 ms the first time and 0.1 ms after: numba's first call with the search's
arrays does one-time work in Python (it imports `numpy.ma`, for one).

The compiled code is cached on disk, in
`src/lab_engine/__pycache__/numba/<hash>/`, where the hash covers the package's
Python files and everything in `src/lab_engine/data/`, the folder for data the
compiled code reads (there's none yet). Hidden files don't count, and a visible
link to a folder anywhere in the package stops the engine. numba itself checks a
cached function only against the contents of the file it's in. A function
compiled with a call into another file keeps the old version of that call after
the other file changes, without any error: a scratch check of numba 0.68.1
showed a cached caller returning 12 while its edited callee returned 1200.
Global arrays are frozen into compiled code the same way. Keying the directory
by the package's code and data means code compiled from an older version isn't
loaded, by the engine or its tests. A file edited while the engine is compiling
would still slip through, so once every compiled module is imported, the engine
hashes the package again; if it changed, it deletes that cache and stops.

The build's last step, `python -m lab_engine.warm`, deletes every cache in that
folder and compiles the engine into a new one. That isn't a build cache in the
sense the repository rules out: every build starts it from nothing, and it
lives inside the engine directory, so a worktree's goes with the worktree.

## Threads

An SPRT run plays several games at once, one engine per core, so the engine
limits numpy's, numba's and torch's thread pools to one thread each. The
registry's `run` command can't set environment variables, so
`lab_engine/__init__.py` sets `NUMBA_NUM_THREADS`, `OMP_NUM_THREADS`,
`OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` and `VECLIB_MAXIMUM_THREADS` to 1
before any module imports numpy or numba, overriding inherited values. torch
reads `OMP_NUM_THREADS` for its operator threads; code that imports torch
should also call `torch.set_num_interop_threads(1)`.

## Measurements

Measured on an M4 Max (10 performance and 4 efficiency cores), on AC power
under `caffeinate -i`, with nothing else heavy running, on 2026-11-13.

**Time to `readyok`**, from starting `.venv/bin/python -m lab_engine` to
reading `readyok`, with `uci` and `isready` sent at once. The engine answers
`uciok` at the same moment, because it loads before reading commands.

| Case | Starts | Time to `readyok` |
|---|---:|---|
| One at a time, after the build | 10 | 0.74–0.78 s (median 0.76 s) |
| 9 at once, the first round after the build | 9 | 0.93 s |
| 9 at once, after the build | 45 (5 rounds) | 0.92–0.94 s |
| One alone, numba cache deleted after the build | 1 | 1.92 s |
| 9 at once, numba cache deleted after the build | 9 | 2.27–2.28 s |

The SPRT runner allows 30 s for each of `uci` and `isready`
(`--handshake-timeout-ms`), and the Play page 10 s, so the slowest start is
well inside both. Started nine at once under `/usr/bin/time`, with `uci`,
`isready` and `quit` sent to each at once, every engine exited 1.00–1.03 s
after it started, over three rounds, which agrees. The build itself took 6.5 s,
of which the warm step took 3.7 s. The last two rows deleted only numba's
cache, while the warm step also paid one-time costs such as writing numpy's and
numba's bytecode, so an engine built without the warm step would start more
slowly than they show.

**Nodes per second**, from the engine's own `info` lines after 10-second
searches (`go movetime 10000`), at the last completed depth. Every node counts,
leaves included.

| Position | Depth reached | One engine | Each of 9 at once |
|---|---:|---:|---:|
| Initial position | 7–8 | 6.5 M | 4.9–5.6 M |
| Kiwipete | 8 | 4.9 M | 4.2–4.3 M |
| Position 6 | 7 | 6.3 M | 5.7–5.8 M |
| Position 3 (rook endgame) | 11 | 7.9 M | 7.0 M |

Perft runs at about 20 M leaves per second.

**Time limits in play**, over 404 games against `random-engine`, 9 at a time,
at 10 s + 0.1 s and 2 s + 0.02 s: none was lost on time. No move ran more than
11 ms past the engine's hard limit, as the runner timed it, except 11 of the
4,087 at 2 s + 0.02 s, which ran 14–59 ms past. The hard limit is a timer
thread, and Python's timed waits woke 1–10 ms late on this machine, even at the
most urgent latency tier (`taskpolicy -l 0`). In one of the 11, the engine's
own clock showed a depth-1 search, normally well under a millisecond, taking
21 ms, so the process wasn't getting the CPU. The engine keeps 30 ms back from
every budget, and the runner allows 100 ms past the clock before a loss on time
(`--time-margin-ms`). The least the engine had left after a move was 24 ms, of
which 20 ms was the increment.

## Tests

```bash
uv run pytest -m "not integration"   # unit tests, about 4 s
uv run pytest -m integration         # deeper perft, the engine as a process, the build step
uv run pyright                       # strict
```

The perft counts are the Chess Programming Wiki's published ones
([Perft Results](https://www.chessprogramming.org/Perft_Results)); the unit
tests run each position up to a million leaves, and the integration tests up
to 200 million. numba ships no type information, so `typings/numba/` declares
the parts of its API the engine and its tests use, rather than loosening
pyright's strict mode.
