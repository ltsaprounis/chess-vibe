"""Tests for lab_engine.position: FEN, legal moves and UCI moves."""

from __future__ import annotations

import numpy as np
import pytest
from lab_engine import board as b
from lab_engine.position import START_FEN, Position, move_to_uci

from tests.perft_table import PERFT_POSITIONS

CASTLING_FEN = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"


class TestFen:
    """Positions read and write FEN."""

    @pytest.mark.parametrize(
        "fen",
        [
            START_FEN,
            "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3",
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
            "r3k2r/8/8/8/8/8/8/R3K2R b Kq - 12 40",
            *(p.fen for p in PERFT_POSITIONS if len(p.fen.split()) == 6),
        ],
    )
    def test_round_trips(self, fen: str) -> None:
        assert Position.from_fen(fen).fen() == fen

    def test_start_is_the_initial_position(self) -> None:
        assert Position.start().fen() == START_FEN

    def test_four_fields_start_the_clocks(self) -> None:
        assert Position.from_fen("8/8/8/8/8/8/8/K6k w - -").fen() == "8/8/8/8/8/8/8/K6k w - - 0 1"

    def test_reads_the_state(self) -> None:
        position = Position.from_fen("r3k2r/8/8/3pP3/8/8/8/R3K2R w Kq d6 7 21")
        assert position.state[b.SIDE] == b.WHITE
        assert position.state[b.CASTLING] == b.WHITE_KINGSIDE | b.BLACK_QUEENSIDE
        assert position.state[b.EN_PASSANT] == b.parse_square("d6")
        assert position.state[b.HALFMOVE_CLOCK] == 7
        assert position.state[b.FULLMOVE_NUMBER] == 21
        assert position.state[b.WHITE_KING] == b.E1
        assert position.state[b.BLACK_KING] == b.E8

    def test_reads_black_to_move(self) -> None:
        assert Position.from_fen("4k3/8/8/8/8/8/8/4K3 b - - 0 1").state[b.SIDE] == b.BLACK

    def test_places_pieces_with_their_colours(self) -> None:
        position = Position.start()
        assert position.board[b.E1] == b.KING
        assert position.board[b.E8] == -b.KING
        assert position.board[b.parse_square("d1")] == b.QUEEN
        assert position.board[b.parse_square("b8")] == -b.KNIGHT
        assert position.board[b.parse_square("a2")] == b.PAWN
        assert position.board[b.parse_square("h7")] == -b.PAWN
        assert position.board[b.parse_square("e4")] == b.EMPTY

    def test_border_is_offboard(self) -> None:
        position = Position.start()
        inside = {b.square(f, r) for f in range(8) for r in range(8)}
        border = [sq for sq in range(b.BOARD_SIZE) if sq not in inside]
        assert len(border) == b.BOARD_SIZE - 64
        assert all(position.board[sq] == b.OFFBOARD for sq in border)

    def test_arrays_are_int64(self) -> None:
        position = Position.start()
        assert position.board.dtype == np.int64
        assert position.state.dtype == np.int64
        assert position.board.shape == (b.BOARD_SIZE,)
        assert position.state.shape == (b.STATE_SIZE,)

    @pytest.mark.parametrize(
        "fen",
        [
            "",
            "8/8/8/8/8/8/8/K6k w -",
            "8/8/8/8/8/8/K6k w - - 0 1",
            "8/8/8/8/8/8/8/8/K6k w - - 0 1",
            "8/8/8/8/8/8/8/K6k x - - 0 1",
            "8/8/8/8/8/8/8/K5k w - - 0 1",
            "8/8/8/8/8/8/8/K7k w - - 0 1",
            "8/8/8/8/8/8/8/K6k w - - 0 1 extra",
            "8/8/8/8/8/8/8/K9 w - - 0 1",
            "8/8/8/8/8/8/8/K6k1 w - - 0 1",
            "81/8/8/8/8/8/8/K6k w - - 0 1",
            "8/8/8/8/8/8/8/X6k w - - 0 1",
            "8/8/8/8/8/8/8/K6k w X - 0 1",
            "8/8/8/8/8/8/8/K6k w KK - 0 1",
            "8/8/8/8/8/8/8/K6k w - e4 0 1",
            "8/8/8/8/8/8/8/K6k w - e3 0 1",
            "8/8/8/8/8/8/8/K6k b - e6 0 1",
            "8/8/8/8/8/8/8/K6k w - - x 1",
            "8/8/8/8/8/8/8/K6k w - - -1 1",
            "8/8/8/8/8/8/8/K6k w - - 0 -1",
            "8/8/8/8/8/8/8/7k w - - 0 1",
            "8/8/8/8/8/8/8/K6K w - - 0 1",
            "8/8/8/8/8/8/8/Kk5k w - - 0 1",
        ],
    )
    def test_rejects_invalid_fen(self, fen: str) -> None:
        with pytest.raises(ValueError, match="FEN"):
            Position.from_fen(fen)


class TestLegalMoves:
    """``legal_moves`` lists every legal move and nothing else."""

    def test_twenty_from_the_start(self) -> None:
        moves = {move_to_uci(m) for m in Position.start().legal_moves()}
        pawns = {f"{f}2{f}{r}" for f in "abcdefgh" for r in "34"}
        knights = {"b1a3", "b1c3", "g1f3", "g1h3"}
        assert moves == pawns | knights

    def test_none_when_checkmated(self) -> None:
        mated = Position.from_fen("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
        assert mated.legal_moves() == []
        assert mated.in_check()

    def test_none_when_stalemated(self) -> None:
        stalemated = Position.from_fen("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
        assert stalemated.legal_moves() == []
        assert not stalemated.in_check()

    def test_a_pinned_piece_moves_only_along_the_pin(self) -> None:
        pinned = Position.from_fen("4k3/4r3/8/8/8/8/8/4R2K b - - 0 1")
        moves = {move_to_uci(m) for m in pinned.legal_moves()}
        rook = {f"e7e{r}" for r in "654321"}
        king = {"e8d8", "e8f8", "e8d7", "e8f7"}
        assert moves == rook | king

    def test_leaves_the_position_unchanged(self) -> None:
        position = Position.from_fen(PERFT_POSITIONS[1].fen)
        before = (position.board.copy(), position.state.copy())
        position.legal_moves()
        assert np.array_equal(position.board, before[0])
        assert np.array_equal(position.state, before[1])


class TestPushUci:
    """``push_uci`` plays a legal move given in UCI notation."""

    @pytest.mark.parametrize(
        ("fen", "move", "after"),
        [
            (START_FEN, "e2e4", "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"),
            (
                "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
                "g8f6",
                "rnbqkb1r/pppppppp/5n2/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 1 2",
            ),
            (
                "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3",
                "e5f6",
                "rnbqkbnr/ppp1p1pp/5P2/3p4/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 3",
            ),
            (
                "rnbqkbnr/pppp1ppp/8/8/3Pp3/8/PPP1PPPP/RNBQKBNR b KQkq d3 0 2",
                "e4d3",
                "rnbqkbnr/pppp1ppp/8/8/8/3p4/PPP1PPPP/RNBQKBNR w KQkq - 0 3",
            ),
            (CASTLING_FEN, "e1g1", "r3k2r/8/8/8/8/8/8/R4RK1 b kq - 1 1"),
            (CASTLING_FEN, "e1c1", "r3k2r/8/8/8/8/8/8/2KR3R b kq - 1 1"),
            (
                "r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1",
                "e8g8",
                "r4rk1/8/8/8/8/8/8/R3K2R w KQ - 1 2",
            ),
            (
                "r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1",
                "e8c8",
                "2kr3r/8/8/8/8/8/8/R3K2R w KQ - 1 2",
            ),
            (CASTLING_FEN, "e1e2", "r3k2r/8/8/8/8/8/4K3/R6R b kq - 1 1"),
            (CASTLING_FEN, "h1h2", "r3k2r/8/8/8/8/8/7R/R3K3 b Qkq - 1 1"),
            (CASTLING_FEN, "a1a8", "R3k2r/8/8/8/8/8/8/4K2R b Kk - 0 1"),
            ("8/4P3/8/8/8/8/8/k6K w - - 0 1", "e7e8q", "4Q3/8/8/8/8/8/8/k6K b - - 0 1"),
            ("8/4P3/8/8/8/8/8/k6K w - - 0 1", "e7e8n", "4N3/8/8/8/8/8/8/k6K b - - 0 1"),
            ("3r4/4P3/8/8/8/8/8/k6K w - - 0 1", "e7d8r", "3R4/8/8/8/8/8/8/k6K b - - 0 1"),
            ("k6K/8/8/8/8/8/4p3/8 b - - 5 9", "e2e1b", "k6K/8/8/8/8/8/8/4b3 w - - 0 10"),
        ],
    )
    def test_plays_the_move(self, fen: str, move: str, after: str) -> None:
        position = Position.from_fen(fen)
        position.push_uci(move)
        assert position.fen() == after

    def test_tracks_the_king_squares(self) -> None:
        position = Position.from_fen(CASTLING_FEN)
        position.push_uci("e1g1")
        position.push_uci("e8c8")
        assert position.state[b.WHITE_KING] == b.G1
        assert position.state[b.BLACK_KING] == b.C8

    @pytest.mark.parametrize("move", ["e2e5", "e1e2", "e7e5", "e2e4q", "a1a1", "xyz", ""])
    def test_rejects_moves_that_are_not_legal_here(self, move: str) -> None:
        position = Position.start()
        with pytest.raises(ValueError, match="legal"):
            position.push_uci(move)
        assert position.fen() == START_FEN


class TestCopy:
    """A copy is independent of its original."""

    def test_changing_a_copy_leaves_the_original(self) -> None:
        position = Position.start()
        copy = position.copy()
        copy.push_uci("e2e4")
        assert position.fen() == START_FEN
        assert copy.fen() != START_FEN
