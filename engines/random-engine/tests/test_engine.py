"""Tests for the RandomEngine board-state and move-selection logic."""

from __future__ import annotations

import chess
import pytest
from random_engine.engine import NoLegalMovesError, RandomEngine


class TestRandomEngineInit:
    """Tests for RandomEngine initialisation."""

    def test_initial_board_is_startpos(self) -> None:
        engine = RandomEngine()
        assert engine.board == chess.Board()

    def test_initial_board_has_legal_moves(self) -> None:
        engine = RandomEngine()
        assert len(list(engine.board.legal_moves)) > 0


class TestSetPositionStartpos:
    """Tests for set_position_startpos."""

    def test_sets_startpos(self) -> None:
        engine = RandomEngine()
        # Mess up the board first, then reset.
        engine.board.push(chess.Move.from_uci("e2e4"))
        engine.set_position_startpos()
        assert engine.board == chess.Board()

    def test_applies_moves(self) -> None:
        engine = RandomEngine()
        engine.set_position_startpos(moves=["e2e4", "e7e5"])
        # After 1. e4 e5, it is White's turn on move 2.
        assert engine.board.fullmove_number == 2
        assert engine.board.turn == chess.WHITE

    def test_no_moves_defaults_to_empty(self) -> None:
        engine = RandomEngine()
        engine.set_position_startpos()
        assert engine.board == chess.Board()

    def test_resets_after_previous_moves(self) -> None:
        engine = RandomEngine()
        engine.set_position_startpos(moves=["d2d4"])
        engine.set_position_startpos()  # reset
        assert engine.board == chess.Board()

    def test_invalid_move_raises_value_error(self) -> None:
        engine = RandomEngine()
        with pytest.raises(ValueError):
            engine.set_position_startpos(moves=["z9z9"])

    def test_illegal_move_raises_value_error(self) -> None:
        engine = RandomEngine()
        # e1e8 is syntactically valid but not legal from the start position.
        with pytest.raises(ValueError):
            engine.set_position_startpos(moves=["e1e8"])


class TestSetPositionFen:
    """Tests for set_position_fen."""

    def test_sets_given_fen(self) -> None:
        # Use a FEN without en-passant square; python-chess normalises that field.
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        engine = RandomEngine()
        engine.set_position_fen(fen)
        assert engine.board.fen() == fen

    def test_applies_moves_after_fen(self) -> None:
        # After 1. e4, it is Black's turn; play e7e5.
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        engine = RandomEngine()
        engine.set_position_fen(fen, moves=["e7e5"])
        assert engine.board.turn == chess.WHITE

    def test_invalid_move_after_fen_raises(self) -> None:
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        engine = RandomEngine()
        with pytest.raises(ValueError):
            engine.set_position_fen(fen, moves=["z9z9"])


class TestPickMove:
    """Tests for pick_move."""

    def test_returns_legal_move_from_startpos(self) -> None:
        engine = RandomEngine()
        move_uci = engine.pick_move()
        move = chess.Move.from_uci(move_uci)
        assert move in engine.board.legal_moves

    def test_returns_legal_move_after_moves(self) -> None:
        engine = RandomEngine()
        engine.set_position_startpos(moves=["e2e4", "e7e5"])
        move_uci = engine.pick_move()
        move = chess.Move.from_uci(move_uci)
        assert move in engine.board.legal_moves

    def test_returns_uci_string(self) -> None:
        engine = RandomEngine()
        move_uci = engine.pick_move()
        # UCI moves are 4-5 characters (e.g. "e2e4" or "e7e8q")
        assert isinstance(move_uci, str)
        assert 4 <= len(move_uci) <= 5

    def test_raises_when_no_legal_moves(self) -> None:
        # Fool's Mate — checkmate after 4 half-moves.
        engine = RandomEngine()
        engine.set_position_startpos(moves=["f2f3", "e7e5", "g2g4", "d8h4"])
        # Board should now be in checkmate.
        assert engine.board.is_checkmate()
        with pytest.raises(NoLegalMovesError):
            engine.pick_move()

    def test_pick_move_does_not_mutate_board(self) -> None:
        """pick_move must not push the move onto the internal board."""
        engine = RandomEngine()
        fen_before = engine.board.fen()
        engine.pick_move()
        assert engine.board.fen() == fen_before

    def test_randomness_produces_variety(self) -> None:
        """Over many calls, at least two distinct moves should appear."""
        engine = RandomEngine()
        # Start position has 20 legal moves; drawing the same one 50 times
        # is astronomically unlikely.
        moves = {engine.pick_move() for _ in range(50)}
        assert len(moves) > 1
