"""Tests for lab_engine.movegen: attacks, make and unmake, and perft."""

from __future__ import annotations

import numpy as np
import pytest
from lab_engine import board as b
from lab_engine import movegen
from lab_engine.position import Position, move_to_uci

from tests.perft_table import PERFT_POSITIONS

UNIT_PERFT_MAX_NODES = 1_000_000
"""The largest perft count the unit tests run; deeper ones are integration tests."""


def _attacked(fen: str, name: str, by_side: int) -> bool:
    position = Position.from_fen(fen)
    return movegen.is_attacked(position.board, b.parse_square(name), by_side)


class TestMoveEncoding:
    """A move packs its squares, promotion and kind into one integer."""

    @pytest.mark.parametrize(
        ("from_square", "to_square", "promotion", "kind"),
        [
            (b.E1, b.G1, 0, movegen.CASTLE),
            (b.parse_square("e2"), b.parse_square("e4"), 0, movegen.DOUBLE_PUSH),
            (b.parse_square("e5"), b.parse_square("d6"), 0, movegen.EN_PASSANT_CAPTURE),
            (b.parse_square("a7"), b.A8, b.KNIGHT, movegen.NORMAL),
            (b.H8, b.A1, b.QUEEN, movegen.NORMAL),
        ],
    )
    def test_fields_round_trip(
        self, from_square: int, to_square: int, promotion: int, kind: int
    ) -> None:
        move = movegen.encode_move(from_square, to_square, promotion, kind)
        assert movegen.move_from(move) == from_square
        assert movegen.move_to(move) == to_square
        assert movegen.move_promotion(move) == promotion
        assert movegen.move_kind(move) == kind

    def test_no_move_is_not_a_move(self) -> None:
        assert movegen.move_from(movegen.NO_MOVE) == 0
        assert movegen.move_to(movegen.NO_MOVE) == 0


class TestIsAttacked:
    """``is_attacked`` sees every piece's attacks, and blocking pieces."""

    def test_pawns_attack_diagonally_forward(self) -> None:
        fen = "4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1"
        assert _attacked(fen, "d5", b.WHITE)
        assert _attacked(fen, "f5", b.WHITE)
        assert not _attacked(fen, "e5", b.WHITE)
        assert not _attacked(fen, "d3", b.WHITE)
        assert _attacked(fen, "c4", b.BLACK)
        assert _attacked(fen, "e4", b.BLACK)
        assert not _attacked(fen, "d4", b.BLACK)
        assert not _attacked(fen, "e6", b.BLACK)

    def test_knights(self) -> None:
        fen = "4k3/8/8/8/3N4/8/8/4K3 w - - 0 1"
        for name in ("b3", "b5", "c2", "c6", "e2", "e6", "f3", "f5"):
            assert _attacked(fen, name, b.WHITE), name
        assert not _attacked(fen, "d5", b.WHITE)
        assert not _attacked(fen, "a1", b.WHITE)

    def test_knights_on_the_rim_stay_on_the_board(self) -> None:
        fen = "N3k3/8/8/8/8/8/8/4K2N w - - 0 1"
        assert _attacked(fen, "b6", b.WHITE)
        assert _attacked(fen, "g3", b.WHITE)
        assert not _attacked(fen, "h6", b.WHITE)

    def test_kings(self) -> None:
        fen = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
        assert _attacked(fen, "d2", b.WHITE)
        assert _attacked(fen, "f1", b.WHITE)
        assert not _attacked(fen, "e3", b.WHITE)
        assert _attacked(fen, "d7", b.BLACK)

    def test_rooks_along_lines_until_blocked(self) -> None:
        fen = "4k3/8/8/8/R2p4/8/8/4K3 w - - 0 1"
        assert _attacked(fen, "a8", b.WHITE)
        assert _attacked(fen, "d4", b.WHITE)
        assert not _attacked(fen, "e4", b.WHITE)

    def test_queens_along_lines_and_diagonals(self) -> None:
        fen = "4k3/8/8/8/8/8/8/4K2Q w - - 0 1"
        assert _attacked(fen, "h8", b.WHITE)
        assert _attacked(fen, "a8", b.WHITE)
        assert not _attacked(fen, "g3", b.WHITE)

    def test_bishops_and_queens_along_diagonals_until_blocked(self) -> None:
        fen = "4k3/8/8/8/8/2p5/8/B3K2q w - - 0 1"
        assert _attacked(fen, "b2", b.WHITE)
        assert _attacked(fen, "c3", b.WHITE)
        assert not _attacked(fen, "d4", b.WHITE)
        assert _attacked(fen, "a8", b.BLACK)
        assert not _attacked(fen, "a1", b.BLACK)

    def test_a_rook_does_not_attack_diagonally(self) -> None:
        assert not _attacked("4k3/8/8/8/3R4/8/8/4K3 w - - 0 1", "e5", b.WHITE)


class TestInconsistentFen:
    """A FEN can claim more than its pieces allow; moves follow the pieces."""

    def _moves(self, fen: str) -> set[str]:
        return {move_to_uci(m) for m in Position.from_fen(fen).legal_moves()}

    def test_an_en_passant_square_with_no_pawn_to_take_gives_no_capture(self) -> None:
        assert "d5e6" not in self._moves("4k3/8/8/3P4/8/8/8/4K3 w - e6 0 1")

    def test_a_castling_right_without_its_rook_gives_no_castling(self) -> None:
        moves = self._moves("4k3/8/8/8/8/8/8/4K3 w KQ - 0 1")
        assert not {"e1g1", "e1c1"} & moves

    def test_a_castling_right_with_the_king_away_gives_no_castling(self) -> None:
        moves = self._moves("4k3/8/8/8/8/8/8/R2K3R w KQ - 0 1")
        assert not {"d1f1", "d1b1", "e1g1", "e1c1"} & moves


class TestMakeAndUnmake:
    """``unmake_move`` restores exactly what ``make_move`` changed."""

    @pytest.mark.parametrize(
        "fen",
        [
            *(p.fen for p in PERFT_POSITIONS),
            "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3",
            "r3k2r/1P6/8/8/8/8/6p1/R3K2R b KQkq - 0 1",
        ],
    )
    def test_every_move_is_undone(self, fen: str) -> None:
        position = Position.from_fen(fen)
        moves = np.zeros(movegen.MAX_MOVES, dtype=np.int64)
        undo = np.zeros(movegen.UNDO_SIZE, dtype=np.int64)
        count = movegen.generate_moves(position.board, position.state, moves)
        assert count > 0
        board_before = position.board.copy()
        state_before = position.state.copy()
        for move in moves[:count]:
            movegen.make_move(position.board, position.state, move, undo)
            assert not np.array_equal(position.board, board_before)
            movegen.unmake_move(position.board, position.state, move, undo)
            assert np.array_equal(position.board, board_before)
            assert np.array_equal(position.state, state_before)


class TestPerft:
    """Perft matches the published counts."""

    @pytest.mark.parametrize(
        ("fen", "depth", "expected"),
        [
            pytest.param(p.fen, depth, count, id=f"{p.name}-{depth}")
            for p in PERFT_POSITIONS
            for depth, count in enumerate(p.counts, start=1)
            if count <= UNIT_PERFT_MAX_NODES
        ],
    )
    def test_published_counts(self, fen: str, depth: int, expected: int) -> None:
        assert Position.from_fen(fen).perft(depth) == expected

    def test_depth_zero_is_one_leaf(self) -> None:
        assert Position.start().perft(0) == 1

    def test_leaves_the_position_unchanged(self) -> None:
        position = Position.from_fen(PERFT_POSITIONS[1].fen)
        fen = position.fen()
        position.perft(3)
        assert position.fen() == fen

    def test_every_position_has_unit_depths(self) -> None:
        for p in PERFT_POSITIONS:
            assert p.counts[0] <= UNIT_PERFT_MAX_NODES, p.name
