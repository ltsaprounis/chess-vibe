"""Tests for random_engine.engine — board state and random move selection."""

from __future__ import annotations

import chess
import pytest
from random_engine.engine import RandomEngine

# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestRandomEngineInit:
    """Tests for RandomEngine initialisation."""

    def test_initial_board_is_standard_startpos(self) -> None:
        engine = RandomEngine()
        assert engine.board.fen() == chess.STARTING_FEN

    def test_initial_board_has_legal_moves(self) -> None:
        engine = RandomEngine()
        assert list(engine.board.legal_moves)


# ---------------------------------------------------------------------------
# position startpos
# ---------------------------------------------------------------------------


class TestSetPositionStartpos:
    """Tests for set_position_startpos."""

    def test_resets_to_startpos(self) -> None:
        engine = RandomEngine()
        # Mutate the board first…
        engine.board.push_uci("e2e4")
        # …then reset.
        engine.set_position_startpos()
        assert engine.board.fen() == chess.STARTING_FEN

    def test_with_moves(self) -> None:
        engine = RandomEngine()
        engine.set_position_startpos(moves=["e2e4", "e7e5"])
        # After 1.e4 e5 the board should reflect those moves.
        expected = chess.Board()
        expected.push_uci("e2e4")
        expected.push_uci("e7e5")
        assert engine.board.fen() == expected.fen()

    def test_with_no_moves_is_startpos(self) -> None:
        engine = RandomEngine()
        engine.set_position_startpos(moves=None)
        assert engine.board.fen() == chess.STARTING_FEN

    def test_with_empty_moves_list(self) -> None:
        engine = RandomEngine()
        engine.set_position_startpos(moves=[])
        assert engine.board.fen() == chess.STARTING_FEN


# ---------------------------------------------------------------------------
# position fen
# ---------------------------------------------------------------------------


class TestSetPositionFen:
    """Tests for set_position_fen."""

    def test_sets_custom_fen(self) -> None:
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        engine = RandomEngine()
        engine.set_position_fen(fen)
        assert engine.board.fen() == fen

    def test_fen_with_moves(self) -> None:
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        engine = RandomEngine()
        engine.set_position_fen(fen, moves=["e7e5"])
        expected = chess.Board(fen)
        expected.push_uci("e7e5")
        assert engine.board.fen() == expected.fen()


# ---------------------------------------------------------------------------
# pick_move
# ---------------------------------------------------------------------------


class TestPickMove:
    """Tests for pick_move — random legal move selection."""

    def test_returns_valid_uci_string(self) -> None:
        engine = RandomEngine()
        move = engine.pick_move()
        # Must be parseable as a UCI move on the starting board.
        parsed = chess.Move.from_uci(move)
        assert parsed in engine.board.legal_moves

    def test_move_is_legal(self) -> None:
        """Run several trials to gain confidence moves are always legal."""
        engine = RandomEngine()
        for _ in range(50):
            engine.set_position_startpos()
            move = engine.pick_move()
            parsed = chess.Move.from_uci(move)
            assert parsed in engine.board.legal_moves

    def test_move_after_sequence(self) -> None:
        engine = RandomEngine()
        engine.set_position_startpos(moves=["e2e4", "e7e5", "g1f3"])
        move = engine.pick_move()
        parsed = chess.Move.from_uci(move)
        assert parsed in engine.board.legal_moves

    def test_raises_when_no_legal_moves(self) -> None:
        # Scholar's mate: 1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7#
        engine = RandomEngine()
        engine.set_position_startpos(moves=["e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6", "h5f7"])
        with pytest.raises(ValueError, match="No legal moves"):
            engine.pick_move()

    def test_move_from_custom_fen(self) -> None:
        # A FEN with limited legal moves (king-only endgame).
        fen = "8/8/8/8/8/8/8/4K3 w - - 0 1"
        engine = RandomEngine()
        engine.set_position_fen(fen)
        move = engine.pick_move()
        parsed = chess.Move.from_uci(move)
        assert parsed in engine.board.legal_moves
