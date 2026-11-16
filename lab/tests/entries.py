"""Builders for ledger entries in tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from lab.ledger import AnchorEntry, AttemptEntry, MatchRecord, StageRecord

STARTED = datetime(2026, 11, 13, 15, 0, tzinfo=UTC)
FINISHED = datetime(2026, 11, 13, 16, 30, tzinfo=UTC)


def stage(name: str = "fast", **changes: Any) -> StageRecord:
    values: dict[str, Any] = {
        "name": name,
        "tc": "wtime=1000,btime=1000,winc=10,binc=10",
        "elo0": 0.0,
        "elo1": 10.0,
        "alpha": 0.05,
        "beta": 0.05,
        "max_games": 100,
        "max_time_losses": 0,
        "seed": 1234,
        "result": "H1",
        "games": 100,
        "wins": 50,
        "losses": 30,
        "draws": 20,
        "errors": 0,
        "llr": 2.95,
        "terminations": {"checkmate": 80, "draw_rule": 20, "timeout": 0, "engine_crash": 0},
        "elo": 70.4,
        "elo_lower": 20.1,
        "elo_upper": 122.7,
        "duration_s": 600.0,
        "outcome": "pass",
        "reasons": [],
        "message": "H1 after 100 games: W 50 L 30 D 20, LLR 2.95",
    }
    values.update(changes)
    return StageRecord(**values)


def attempt(entry_id: int = 1, **changes: Any) -> AttemptEntry:
    values: dict[str, Any] = {
        "kind": "attempt",
        "id": entry_id,
        "started_at": STARTED,
        "finished_at": FINISHED,
        "hypothesis": "Order captures by MVV-LVA",
        "base": "a" * 40,
        "candidate": "b" * 40,
        "engine_tree": "c" * 40,
        "harness": "d" * 40,
        "book": "books/test.epd",
        "book_sha256": "e" * 64,
        "concurrency": 2,
        "config_sha256": "c0" * 32,
        "correctness_commands": [["python3", "-c", "pass"]],
        "correctness_s": 61.5,
        "stages": [stage("fast"), stage("confirm")],
        "verdict": "accepted",
        "reasons": [],
        "message": "accepted",
    }
    values.update(changes)
    return AttemptEntry(**values)


def match(opponent: str = "random-engine", **changes: Any) -> MatchRecord:
    values: dict[str, Any] = {
        "opponent": opponent,
        "engine": f"random-engine:{'d' * 40}",
        "seed": 99,
        "games": 20,
        "wins": 19,
        "losses": 0,
        "draws": 1,
        "errors": 0,
        "terminations": {"checkmate": 19, "draw_rule": 1, "timeout": 0, "engine_crash": 0},
        "elo": 636.0,
        "elo_lower": 390.0,
        "elo_upper": None,
        "duration_s": 120.0,
        "outcome": "pass",
        "reasons": [],
        "message": "fixed after 20 games: W 19 L 0 D 1, LLR 1.10",
    }
    values.update(changes)
    return MatchRecord(**values)


def anchor(entry_id: int = 1, milestone: int | None = 1, **changes: Any) -> AnchorEntry:
    values: dict[str, Any] = {
        "kind": "anchor",
        "id": entry_id,
        "started_at": STARTED,
        "finished_at": FINISHED,
        "version": "f" * 40,
        "engine_tree": "9" * 40,
        "harness": "d" * 40,
        "book": "books/test.epd",
        "book_sha256": "e" * 64,
        "concurrency": 2,
        "tc": "wtime=1000,btime=1000,winc=10,binc=10",
        "games": 20,
        "max_time_losses": 0,
        "config_sha256": "c0" * 32,
        "matches": [match()],
        "previous": "random-engine",
        "outcome": "milestone" if milestone is not None else "flagged",
        "milestone": milestone,
        "reasons": [] if milestone is not None else ["not_clearly_stronger"],
        "message": "frozen as milestone 1" if milestone is not None else "flagged",
    }
    values.update(changes)
    return AnchorEntry(**values)
