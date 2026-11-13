"""Tests for lab_engine.evaluate: material plus piece-square bonuses."""

from __future__ import annotations

import pytest
from lab_engine import board as b
from lab_engine.evaluate import PIECE_VALUES, evaluate
from lab_engine.position import START_FEN, Position

from tests.fen_tools import mirror_fen
from tests.perft_table import PERFT_POSITIONS


def _score(fen: str) -> int:
    """Evaluate ``fen`` for its side to move."""
    position = Position.from_fen(fen)
    return evaluate(position.board, int(position.state[b.SIDE]))


def _white_score(fen: str) -> int:
    """Evaluate ``fen`` from White's point of view."""
    return evaluate(Position.from_fen(fen).board, b.WHITE)


class TestPointOfView:
    """Scores are from the point of view of the side asked about."""

    def test_the_initial_position_is_level(self) -> None:
        assert _score(START_FEN) == 0

    @pytest.mark.parametrize("fen", [p.fen for p in PERFT_POSITIONS])
    def test_the_two_sides_see_opposite_scores(self, fen: str) -> None:
        board = Position.from_fen(fen).board
        assert evaluate(board, b.BLACK) == -evaluate(board, b.WHITE)

    @pytest.mark.parametrize("fen", [p.fen for p in PERFT_POSITIONS])
    def test_a_mirrored_position_scores_the_same_for_its_side_to_move(self, fen: str) -> None:
        assert _score(mirror_fen(fen)) == _score(fen)

    def test_the_side_with_more_material_is_ahead(self) -> None:
        # Black has an extra queen: good for Black, bad for White.
        fen = "3qk3/8/8/8/8/8/8/4K3 b - - 0 1"
        assert _score(fen) > 0
        assert _white_score(fen) < 0


class TestMaterial:
    """Material dominates, in the usual order."""

    def test_piece_values_rise_from_pawn_to_queen(self) -> None:
        values = [PIECE_VALUES[p] for p in (b.PAWN, b.KNIGHT, b.BISHOP, b.ROOK, b.QUEEN)]
        assert values == sorted(values)
        assert values[0] > 0
        assert len(set(values[2:])) == 3

    @pytest.mark.parametrize("piece", ["P", "N", "B", "R", "Q"])
    def test_an_extra_piece_is_worth_about_its_value(self, piece: str) -> None:
        bare = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
        extra = f"4k3/8/8/8/3{piece}4/8/8/4K3 w - - 0 1"
        gain = _white_score(extra) - _white_score(bare)
        value = int(PIECE_VALUES[" PNBRQ".index(piece)])
        assert abs(gain - value) <= 50

    def test_bare_kings_on_matching_squares_are_level(self) -> None:
        assert _white_score("4k3/8/8/8/8/8/8/4K3 w - - 0 1") == 0


class TestPieceSquares:
    """Each piece is worth more on squares where it does more."""

    def test_a_knight_is_better_in_the_centre_than_on_the_rim(self) -> None:
        assert _white_score("4k3/8/8/8/3N4/8/8/4K3 w - - 0 1") > _white_score(
            "4k3/8/8/8/N7/8/8/4K3 w - - 0 1"
        )

    def test_a_knight_is_worst_in_a_corner(self) -> None:
        assert _white_score("4k3/8/8/8/N7/8/8/4K3 w - - 0 1") > _white_score(
            "4k3/8/8/8/8/8/8/N3K3 w - - 0 1"
        )

    def test_a_bishop_is_better_in_the_centre(self) -> None:
        assert _white_score("4k3/8/8/8/3B4/8/8/4K3 w - - 0 1") > _white_score(
            "4k3/8/8/8/8/8/8/B3K3 w - - 0 1"
        )

    def test_pawns_gain_as_they_advance(self) -> None:
        scores = [
            _white_score(f"4k3/{row}/4K3 w - - 0 1")
            for row in (
                "8/8/8/8/8/P7",
                "8/8/8/8/P7/8",
                "8/8/8/P7/8/8",
                "8/8/P7/8/8/8",
                "8/P7/8/8/8/8",
                "P7/8/8/8/8/8",
            )
        ]
        assert scores == sorted(scores)
        assert len(set(scores)) == len(scores)

    def test_centre_pawns_are_worth_more_advanced_to_the_centre(self) -> None:
        assert _white_score("4k3/8/8/8/4P3/8/8/4K3 w - - 0 1") > _white_score(
            "4k3/8/8/8/P7/8/8/4K3 w - - 0 1"
        )

    def test_a_queen_is_a_little_better_in_the_centre(self) -> None:
        centre = _white_score("4k3/8/8/8/3Q4/8/8/4K3 w - - 0 1")
        corner = _white_score("4k3/8/8/8/8/8/8/Q3K3 w - - 0 1")
        assert 0 < centre - corner < 50

    def test_a_rook_likes_the_seventh_rank(self) -> None:
        assert _white_score("4k3/R7/8/8/8/8/8/4K3 w - - 0 1") > _white_score(
            "4k3/8/8/8/R7/8/8/4K3 w - - 0 1"
        )

    def test_the_king_is_safer_castled_than_in_the_centre(self) -> None:
        assert _white_score("4k3/8/8/8/8/8/8/6K1 w - - 0 1") > _white_score(
            "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
        )

    def test_the_king_is_safer_at_home_than_up_the_board(self) -> None:
        assert _white_score("4k3/8/8/8/8/8/8/4K3 w - - 0 1") > _white_score(
            "4k3/8/8/8/4K3/8/8/8 w - - 0 1"
        )
