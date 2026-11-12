"""Tests for the vendored default opening book, which the CLI plays from without ``--book`` (#12).

These read the real file, so they fail if it's edited, truncated or lost.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import chess
import pytest
from sprt_runner.openings import load_openings, shuffle_openings
from sprt_runner.runner import DEFAULT_BOOK

# The runner resolves DEFAULT_BOOK from its working directory, the repo root,
# which is two levels up from this file.
_BOOK = Path(__file__).resolve().parents[2] / DEFAULT_BOOK

# The ``sri`` that upstream's ``books.json`` records for 2moves_v1.epd at
# official-stockfish/books d2356e2: the base64 SHA-384 of the unzipped file.
_UPSTREAM_SRI = "3KqrVhD252BCXrqwqVmFeYnLRp+ckuubLFo+9OFLic3iTuzrfV563rNMUB+vovpz"

# Enough positions for the longest planned run to repeat none: the 90th
# percentile of [0,5] runs on a patch worth nothing is 32,955 games (#189),
# and each position gives two.
_MIN_POSITIONS = 16_500


@pytest.fixture(scope="module")
def book_lines() -> list[str]:
    """The book's lines, read as the strict ASCII the file must be."""
    assert _BOOK.is_file(), f"the default book is missing: {_BOOK}"
    return _BOOK.read_text(encoding="ascii").splitlines()


class TestDefaultBook:
    """The default book is upstream's file, unmodified, and every line is a playable start."""

    def test_is_upstreams_file_unmodified(self) -> None:
        """Its SHA-384 is the one upstream records, so not a byte has changed."""
        assert _BOOK.is_file(), f"the default book is missing: {_BOOK}"
        digest = hashlib.sha384(_BOOK.read_bytes()).digest()
        assert base64.b64encode(digest).decode() == _UPSTREAM_SRI

    def test_the_runner_reads_every_line_as_it_is(self, book_lines: list[str]) -> None:
        """No line is skipped as unparseable, and none changes on the way in."""
        assert load_openings(_BOOK) == book_lines

    def test_has_enough_positions_for_the_longest_planned_run(self, book_lines: list[str]) -> None:
        assert len(book_lines) >= _MIN_POSITIONS

    def test_every_position_is_valid_canonical_and_unfinished(self, book_lines: list[str]) -> None:
        """python-chess, the runner's referee, accepts each one as a game to play on from."""
        for line in book_lines:
            board = chess.Board(line)
            assert board.status() == chess.STATUS_VALID, line
            assert board.fen() == line, line
            assert any(board.generate_legal_moves()), line

    def test_seed_12_still_gives_the_order_it_gave_when_recorded(self) -> None:
        """The order a run plays this book in for a recorded seed, as the runner loads it.

        If this fails, seeds recorded before no longer replay their runs. It also
        sees changes to the order that a few one-letter positions can't, such as
        sorting the positions by length before shuffling them.
        """
        assert shuffle_openings(load_openings(_BOOK), seed=12)[:3] == [
            "r1bqkbnr/ppppppp1/2n5/7p/2P5/3P4/PP2PPPP/RNBQKBNR w KQkq - 0 3",
            "rnbqkbnr/p1pp1ppp/4p3/1p6/1P6/7N/P1PPPPPP/RNBQKB1R w KQkq - 0 3",
            "rnbqkb1r/pp1ppppp/5n2/2p5/5P2/6P1/PPPPP2P/RNBQKBNR w KQkq - 0 3",
        ]

    def test_no_position_repeats(self, book_lines: list[str]) -> None:
        """Positions are compared on their first four FEN fields, without the move counters.

        The lines are canonical FENs, so two that differ there are different positions.
        """
        positions = [" ".join(line.split()[:4]) for line in book_lines]
        assert len(set(positions)) == len(positions)
