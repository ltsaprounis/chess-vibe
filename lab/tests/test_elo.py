"""Tests for writing an Elo estimate and its interval."""

from __future__ import annotations

import pytest
from lab.elo import describe_elo


@pytest.mark.parametrize(
    ("tally", "estimate", "expected"),
    [
        pytest.param((60, 20, 20), (141.0, 70.04, 220.5), "+141.0 [+70.0, +220.5]", id="finite"),
        pytest.param(
            (20, 60, 20), (-141.0, -220.5, -70.0), "-141.0 [-220.5, -70.0]", id="negative"
        ),
        pytest.param((10, 10, 0), (0.0, -150.2, 150.2), "+0.0 [-150.2, +150.2]", id="even"),
        pytest.param((20, 0, 0), (None, None, None), "+inf [+inf, +inf]", id="every-game-won"),
        pytest.param((0, 20, 0), (None, None, None), "-inf [-inf, -inf]", id="every-game-lost"),
        pytest.param(
            (19, 0, 1), (636.0, 390.0, None), "+636.0 [+390.0, +inf]", id="upper-infinite"
        ),
        pytest.param((1, 1, 0), (0.0, None, None), "+0.0 [-inf, +inf]", id="both-ends-infinite"),
        pytest.param((2, 0, 1), (200.0, None, None), "+200.0 [-inf, +inf]", id="a-draw-no-loss"),
        pytest.param((0, 0, 0), (None, None, None), "n/a", id="no-games"),
    ],
)
def test_describe_elo(
    tally: tuple[int, int, int],
    estimate: tuple[float | None, float | None, float | None],
    expected: str,
) -> None:
    wins, losses, draws = tally
    elo, lower, upper = estimate

    assert describe_elo(wins, losses, draws, elo, lower, upper) == expected
