"""Deeper perft runs against the same published counts.

These take seconds each (about 40 s in all on an M4 Max), so they run with
the integration tests. Every count up to 200 million is covered.
"""

from __future__ import annotations

import pytest
from lab_engine.position import Position

from tests.perft_table import PERFT_POSITIONS
from tests.test_movegen import UNIT_PERFT_MAX_NODES

pytestmark = pytest.mark.integration

INTEGRATION_PERFT_MAX_NODES = 200_000_000


@pytest.mark.parametrize(
    ("fen", "depth", "expected"),
    [
        pytest.param(p.fen, depth, count, id=f"{p.name}-{depth}")
        for p in PERFT_POSITIONS
        for depth, count in enumerate(p.counts, start=1)
        if UNIT_PERFT_MAX_NODES < count <= INTEGRATION_PERFT_MAX_NODES
    ],
)
def test_published_counts(fen: str, depth: int, expected: int) -> None:
    assert Position.from_fen(fen).perft(depth) == expected
