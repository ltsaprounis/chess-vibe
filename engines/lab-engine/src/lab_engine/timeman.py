"""Time management: how long to think about a move.

A budget has two limits. The search starts no new depth after the soft
limit, and abandons the depth it's in at the hard limit. Each depth takes
several times as long as the one before, so stopping between depths wastes
less of the clock than running into the hard limit.

The rules are simple on purpose:

- with ``movetime``, both limits are the movetime;
- on a clock, the soft limit is the time left divided by the moves to go
  (``movestogo``, or 30 if the GUI doesn't say), plus three quarters of the
  increment, and the hard limit is three times that;
- nothing is planned past the time left on the clock.

Every limit leaves ``MOVE_OVERHEAD_MS`` for time the SPRT runner counts
against the engine's clock but the engine doesn't see: the round trip of
``go`` and ``bestmove``, and reading the ``position`` sent just before ``go``,
which replays the game's moves (about 2.4 ms for 300 plies, measured).
"""

from __future__ import annotations

from dataclasses import dataclass

MOVE_OVERHEAD_MS = 30
"""Kept back from every budget for the round trip to the GUI."""

DEFAULT_MOVES_TO_GO = 30
"""How many more moves a clock has to last, when the GUI doesn't say."""


@dataclass(frozen=True)
class TimeBudget:
    """How long to search.

    Attributes:
        soft_ms: Start no new depth after this many milliseconds.
        hard_ms: Abandon the search after this many milliseconds.
    """

    soft_ms: int
    hard_ms: int


def movetime_budget(movetime_ms: int) -> TimeBudget:
    """Return the budget for ``go movetime``.

    Args:
        movetime_ms: The time for this move, in milliseconds.
    """
    limit = max(movetime_ms - MOVE_OVERHEAD_MS, 0)
    return TimeBudget(soft_ms=limit, hard_ms=limit)


def clock_budget(remaining_ms: int, increment_ms: int, moves_to_go: int | None) -> TimeBudget:
    """Return the budget for a move on a clock.

    Args:
        remaining_ms: The mover's time left, in milliseconds.
        increment_ms: The mover's increment, added after the move.
        moves_to_go: Moves until the next time control, or ``None``. A value
            below 1 is ignored.
    """
    available = max(remaining_ms - MOVE_OVERHEAD_MS, 0)
    moves = moves_to_go if moves_to_go is not None and moves_to_go >= 1 else DEFAULT_MOVES_TO_GO
    soft = min(available, remaining_ms // moves + increment_ms * 3 // 4)
    return TimeBudget(soft_ms=soft, hard_ms=min(available, 3 * soft))
