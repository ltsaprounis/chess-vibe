"""Tests for lab_engine.board: the mailbox squares and their names."""

from __future__ import annotations

import pytest
from lab_engine import board


class TestSquares:
    """Squares are numbered on a 10x12 mailbox, a1 = 21 and h8 = 98."""

    def test_corners(self) -> None:
        assert board.square(0, 0) == 21
        assert board.square(7, 0) == 28
        assert board.square(0, 7) == 91
        assert board.square(7, 7) == 98

    def test_up_a_rank_adds_ten_and_right_a_file_adds_one(self) -> None:
        e4 = board.square(4, 3)
        assert board.square(4, 4) == e4 + 10
        assert board.square(5, 3) == e4 + 1

    def test_file_and_rank_invert_square(self) -> None:
        for file in range(8):
            for rank in range(8):
                sq = board.square(file, rank)
                assert (board.file_of(sq), board.rank_of(sq)) == (file, rank)

    def test_named_squares(self) -> None:
        assert (board.A1, board.E1, board.H1) == (21, 25, 28)
        assert (board.A8, board.E8, board.H8) == (91, 95, 98)

    def test_board_has_a_border_two_deep_above_and_below(self) -> None:
        # A knight on a corner jumps at most 21 squares, which must stay
        # inside the array.
        assert board.A1 - 21 >= 0
        assert board.H8 + 21 < board.BOARD_SIZE


class TestSquareNames:
    """Squares convert to and from algebraic names."""

    @pytest.mark.parametrize(
        ("name", "sq"), [("a1", 21), ("h1", 28), ("e4", 55), ("a8", 91), ("h8", 98)]
    )
    def test_known_names(self, name: str, sq: int) -> None:
        assert board.square_name(sq) == name
        assert board.parse_square(name) == sq

    def test_every_square_round_trips(self) -> None:
        for file in range(8):
            for rank in range(8):
                sq = board.square(file, rank)
                assert board.parse_square(board.square_name(sq)) == sq

    @pytest.mark.parametrize("name", ["", "e", "e44", "i1", "a0", "a9", "E4", "4e"])
    def test_rejects_names_that_are_not_squares(self, name: str) -> None:
        with pytest.raises(ValueError, match="square"):
            board.parse_square(name)


class TestPieces:
    """Pieces are signed: positive for White, negative for Black."""

    def test_piece_types_are_one_to_six(self) -> None:
        assert (board.PAWN, board.KNIGHT, board.BISHOP) == (1, 2, 3)
        assert (board.ROOK, board.QUEEN, board.KING) == (4, 5, 6)

    def test_sides_are_signs(self) -> None:
        assert (board.WHITE, board.BLACK) == (1, -1)

    def test_offboard_is_not_a_piece_of_either_side(self) -> None:
        pieces = {board.PAWN, board.KNIGHT, board.BISHOP, board.ROOK, board.QUEEN, board.KING}
        signed = {p * side for p in pieces for side in (board.WHITE, board.BLACK)}
        assert board.OFFBOARD not in signed
        assert board.EMPTY not in signed
