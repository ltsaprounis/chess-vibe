"""Chess engine logic for the random engine.

Maintains the current board state and provides random legal move selection.
This module is intentionally decoupled from UCI I/O — it knows nothing about
stdin/stdout. Fork this engine by replacing this file with your own logic
without touching ``uci.py``.

Typical usage::

    engine = RandomEngine()
    engine.set_position_startpos(moves=["e2e4", "e7e5"])
    move = engine.pick_move()  # e.g. "g1f3"
"""

from __future__ import annotations

import logging
import random

import chess

logger = logging.getLogger(__name__)


class NoLegalMovesError(Exception):
    """Raised when pick_move() is called on a terminal position."""


class RandomEngine:
    """Stateful chess engine that picks a random legal move.

    Attributes:
        board: The current ``chess.Board`` reflecting the game position.
    """

    def __init__(self) -> None:
        """Initialise the engine with a start-position board."""
        self.board: chess.Board = chess.Board()

    def set_position_startpos(self, *, moves: list[str] | None = None) -> None:
        """Reset the board to the starting position and apply moves.

        Args:
            moves: Optional list of moves in UCI notation (e.g. ``["e2e4", "e7e5"]``).
                   Applied in order after resetting to the start position.
        """
        self.board = chess.Board()
        self._apply_moves(moves or [])
        logger.debug("Position set to startpos; %d move(s) applied", len(moves or []))

    def set_position_fen(self, fen: str, *, moves: list[str] | None = None) -> None:
        """Set the board to the given FEN string and apply moves.

        Args:
            fen:   FEN string representing the desired position.
            moves: Optional list of moves in UCI notation applied after the FEN.
        """
        self.board = chess.Board(fen)
        self._apply_moves(moves or [])
        logger.debug("Position set from FEN %r; %d move(s) applied", fen, len(moves or []))

    def pick_move(self) -> str:
        """Pick a random legal move in the current position.

        Returns:
            The chosen move in UCI notation (e.g. ``"e2e4"``).

        Raises:
            NoLegalMovesError: If the position is terminal (checkmate/stalemate).
        """
        legal_moves = list(self.board.legal_moves)
        if not legal_moves:
            raise NoLegalMovesError("No legal moves in the current position")

        move = random.choice(legal_moves)
        logger.debug("Picked move %s from %d legal moves", move.uci(), len(legal_moves))
        return move.uci()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_moves(self, moves: list[str]) -> None:
        """Apply a sequence of UCI-notation moves to the internal board.

        Args:
            moves: List of moves in UCI notation to push onto the board.

        Raises:
            ValueError: If a move is illegal or not parseable.
        """
        for uci_move in moves:
            try:
                move = chess.Move.from_uci(uci_move)
            except chess.InvalidMoveError as exc:
                raise ValueError(f"Invalid move notation: {uci_move!r}") from exc

            if move not in self.board.legal_moves:
                raise ValueError(f"Illegal move {uci_move!r} in position {self.board.fen()!r}")

            self.board.push(move)
