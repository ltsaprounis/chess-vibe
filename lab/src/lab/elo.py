"""Writing an Elo estimate and its 95% interval, as the runner reports them.

The runner gives an infinite value as null. Which infinity it is follows from
the tally: the estimate is infinite only when every game had the same result,
the lower end is +infinity only when every game was won, and the upper end is
-infinity only when every game was lost.
"""

from __future__ import annotations


def _number(value: float | None, positive_infinity: bool) -> str:
    if value is None:
        return "+inf" if positive_infinity else "-inf"
    return f"{value:+.1f}"


def describe_elo(
    wins: int,
    losses: int,
    draws: int,
    elo: float | None,
    lower: float | None,
    upper: float | None,
) -> str:
    """Write an estimate and its interval, such as ``+45.2 [+12.0, +78.4]``.

    Args:
        wins: Wins for the engine the estimate is for.
        losses: Its losses.
        draws: Drawn games.
        elo: The estimate, None where infinite.
        lower: The lower end of its 95% interval, None where infinite.
        upper: The upper end, None where infinite.

    Returns:
        The estimate with its interval, infinities as ``+inf`` and ``-inf``,
        or ``n/a`` with no games.
    """
    if wins + losses + draws == 0:
        return "n/a"
    all_won = losses == 0 and draws == 0
    all_lost = wins == 0 and draws == 0
    return f"{_number(elo, all_won)} [{_number(lower, all_won)}, {_number(upper, not all_lost)}]"
