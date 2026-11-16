# Lab

The keep-or-reject half of the autoresearch loop in
[#190](https://github.com/ltsaprounis/chess-vibe/issues/190): `lab attempt`,
`lab anchor` and `lab report`. The researcher agent
([#194](https://github.com/ltsaprounis/chess-vibe/issues/194)) proposes changes
to `engines/lab-engine/`; this CLI tests each one through the SPRT runner and
decides its fate, so the agent never judges its own results.

It drives the runner only as a CLI subprocess, with the runner's own
interpreter, from the repository root, and reads the runner's JSON lines. It
never imports the runner or any engine.

## Running it

`make setup` builds its venv with the other components. Then:

```bash
lab/.venv/bin/lab attempt --repo "$PWD" --base <lab/main's tip> --candidate <commit> --hypothesis "..."
lab/.venv/bin/lab anchor --repo "$PWD"
lab/.venv/bin/lab report --repo "$PWD"
```

`--repo` is required, and must be the repository's top-level directory: the lab
never falls back to the directory it runs from. `attempt` and `anchor` read
their settings from `lab/lab.toml` in that repository, or from `--config`.

Only one `attempt` or `anchor` runs at a time in a repository (a lock in its
git directory refuses a second). The runner builds every `ENGINE:COMMIT` in
`.worktrees/<commit>` and the first run to finish removes it, so don't start
SPRT tests from the web UI while the lab runs either. And don't change or commit
anything under `sprt-runner/`, `shared/` or `lab/` in that checkout while a
command runs: the harness is checked again before every SPRT and match, and a
change fails the run, which is recorded.

## lab/main, the ledger and the rejected refs

- **`lab/main`** is a local branch of accepted engine changes. The lab never
  creates it, and never pushes anything. Start it at the baseline with
  `git branch lab/main <commit>`.
- **The ledger** is `lab/ledger.jsonl` in lab/main's tree: one line of JSON per
  attempt and per anchor run, only ever appended to. Ids count up from 1
  across both kinds, so the first attempt after an anchor run is attempt 2.
  It's the lab's own file, not suite data, so it doesn't go through `data/`.
  Each line goes on in a commit of its own on lab/main, built with git plumbing
  without touching any checkout and never signed, and the branch moves only if
  it still points where it did when the command started.
- **`refs/lab/rejected/<id>`** keeps every candidate that reached the SPRT and
  wasn't accepted, so it stays reviewable.

lab/main mustn't be checked out anywhere, since the lab moves it without
updating a checkout. Build candidates on a detached HEAD instead, for example
in a worktree: `git worktree add --detach ../candidate lab/main`.

## `lab attempt`

The candidate is one commit whose only parent is `--base`, lab/main's tip. Each
step stops the attempt if it fails:

1. **Scope:** the candidate changes only files under `engines/lab-engine/`
   (renames count as a deletion and an addition, so nothing moves in from
   elsewhere), and changes something.
2. **No retries:** the tree of `engines/lab-engine/` at the candidate isn't in
   the ledger. The same engine code gets one attempt, whatever its verdict.
3. **Harness:** `sprt-runner/`, `shared/` and `lab/` (the lab's own code and
   config) have no uncommitted or untracked files. Their commit and the book's
   SHA-256 are recorded, and they're checked again before each SPRT.
4. **Correctness:** the `[correctness]` commands pass in `engines/lab-engine/`
   of a temporary worktree at the candidate: the engine's unit tests and its
   deep perft.
5. **Fast SPRT** at `[fast]`'s time control and bounds, with `--max-games`.
6. **Confirmation SPRT** at `[confirm]`'s longer time control, only after a
   pass at step 5.

A stage passes only on H1 with clean games. Any game ending in `engine_crash`
(a crash or an illegal move, by either engine), any game without a result
(`errors`), or more time losses than `max_time_losses` rejects the candidate,
even after H1. So do H0 and a run that reaches `max_games` undecided. A run that
doesn't count (the runner exits other than 0, ends without a `complete` line,
or runs past `timeout_minutes`) fails the attempt instead.

What's recorded:

- **A refusal**, by a check before step 1 or by steps 1 to 4, records
  nothing: nothing about the candidate's strength was measured.
- **Once the candidate reaches the SPRT**, the attempt is recorded whatever
  happens next, including a harness failure or a signal, so the same engine
  code can't be tested again in the hope of a pass. The one exception is a
  result the lab can't write (exit 5 with `check` set to `record`, for example
  when lab/main moved meanwhile): its JSON line holds the entry instead.
  - Accepted: lab/main moves to the candidate, with the ledger line on top.
  - Rejected or failed: the ledger line goes on top of the base, and the
    candidate is kept as `refs/lab/rejected/<id>`.

  Both refs move in one transaction: both or neither.

## `lab anchor`

Plays lab/main's tip against random-engine (built at the harness commit) and
then against every milestone in the ledger, oldest first, in fixed-length
matches of `[anchor] games` (the runner's `--max-games --no-early-stop`). The
tip is the runner's test engine, so every result and Elo is the tip's.

If every game was clean and the tip's 95% Elo interval against the newest
milestone (or random-engine, before there's one) lies above 0, the tip is
frozen as the next milestone, automatically. Otherwise it's flagged.

Each engine tree gets one anchor run, as each gets one attempt: once a run has
started its first match, it's recorded however it ends (a failed match, a
signal or a harness change included), and anchoring the same engine code again
is refused, so a flagged version can't be anchored again in the hope that it
freezes.

## `lab report`

Prints the ledger summed up: attempts by verdict and reason, the acceptance
rate, each attempt's SPRTs, the milestones with the Elo that froze them, the
latest anchor run's Elo against each opponent, and machine hours (wall-clock
hours from each command's start to its record).

## Output and exit codes

`attempt` and `anchor` log progress to stderr and print exactly one line on
stdout, a JSON object:

```json
{"outcome": "rejected", "check": null, "message": "attempt 7 rejected: ...", "entry": {"kind": "attempt", "id": 7, "...": "..."}}
```

`entry` is the ledger entry recorded, or null. `report` prints its text, or a
refusal's JSON line.

| Exit | Outcome | Recorded |
|---:|---|---|
| 0 | `accepted`, or `milestone` | yes |
| 1 | the lab itself crashed: a traceback, not an outcome | no |
| 2 | bad flags | no |
| 3 | `refused`: `check` says which check | no |
| 4 | `rejected`, or `flagged` | yes |
| 5 | `failed`: the harness failed, or a signal stopped the command | an attempt that reached the SPRT, or an anchor run that started a match, yes, unless `check` is `record`; otherwise no |

The checks a refusal can name: `repo`, `config`, `busy` (another lab command
holds the lock), `identity` (git can't commit), `lab-main` (missing, or without
the engine), `checked-out`, `unknown-commit`, `base` (not lab/main's tip),
`one-commit`, `ledger` (unreadable), `rejected-ref` (the next id's ref already
exists), `runner` (its venv isn't built), `scope`, `retry` (the engine tree has
had its attempt, or its anchor run), `harness`, `book` and `correctness`. A
`failed` outcome with `check` set to `record` means the result couldn't be
written; its `entry` holds what would have been recorded.

SIGTERM, SIGINT and SIGHUP stop a command cleanly: the runner is stopped, and
an attempt that had reached the SPRT, or an anchor run that had started a
match, is recorded as failed (`interrupted`). A command started under `nohup`
keeps ignoring SIGHUP. However a run ends, the lab doesn't leave the runner
running.

The lab, the runner it starts and step 4's checks all run without `GIT_DIR`
and the other variables that would point git at another repository, as a git
hook would set them.

## Config

`lab/lab.toml` holds every setting: `concurrency`, `book`, `[correctness]`
(`commands`, `timeout_minutes`), `[fast]` and `[confirm]` (`tc`, `elo0`,
`elo1`, `alpha`, `beta`, `max_games`, `max_time_losses`, `timeout_minutes`), and
`[anchor]` (`tc`, `games`, `max_time_losses`, `timeout_minutes`). Every key is
required, unknown keys are refused, and values are checked as the runner
checks its flags. Every ledger entry records the settings that decide its
verdict (time controls, bounds, game and time-loss limits, and for an attempt
the correctness commands) and the config file's SHA-256.

**The time controls, bounds and limits are provisional** until
[#193](https://github.com/ltsaprounis/chess-vibe/issues/193) calibrates them.

## Tests

```bash
uv run pytest -m "not integration"   # unit tests: fakes for git, the runner and the clock
uv run pytest -m integration         # real git in throwaway repos, and the real runner
uv run pyright                       # strict
```

The end-to-end tests build a repository under pytest's `tmp_path` with a
scripted UCI engine, and drive this checkout's runner through a shim, so they
need `make setup` to have built `sprt-runner/.venv`. Every test runs from an
empty directory outside any repository, so nothing can fall back to the
repository the tests live in.
