"""Random chess engine logic and board state management.

This module contains the core engine logic for the random-move chess engine.
It uses ``python-chess`` for board representation and legal-move generation,
and simply picks a uniformly random legal move when asked to search.

Separation of concerns
----------------------
* **engine.py** (this file) — owns the ``chess.Board``, exposes pure methods
  for setting a position and picking a move.  No I/O here.
* **uci.py** — reads UCI commands from stdin, dispatches to ``RandomEngine``,
  and writes UCI responses to stdout.

Typical usage (from ``uci.py``)::

    engine = RandomEngine()
    engine.set_position_startpos(moves=["e2e4", "e7e5"])
    move = engine.pick_move()   # e.g. "g1f3"
"""

from __future__ import annotations

import logging
import random

import chess

logger = logging.getLogger(__name__)


class RandomEngine:
    """A chess engine that selects a uniformly random legal move.

    Attributes:
        board: The current board state managed by ``python-chess``.
    """

    def __init__(self) -> None:
        """Initialise the engine with the standard starting position."""
        self.board = chess.Board()

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def set_position_startpos(self, *, moves: list[str] | None = None) -> None:
        """Set the board to the standard starting position, optionally applying moves.

        This corresponds to the UCI command::

            position startpos [moves e2e4 e7e5 ...]

        Args:
            moves: Optional list of moves in UCI notation (e.g. ``["e2e4", "e7e5"]``).
                   Each move is pushed onto the board in order.
        """
        self.board = chess.Board()
        self._apply_moves(moves)

    def set_position_fen(self, fen: str, *, moves: list[str] | None = None) -> None:
        """Set the board to an arbitrary FEN, optionally applying moves.

        This corresponds to the UCI command::

            position fen <fen> [moves e2e4 e7e5 ...]

        Args:
            fen: A FEN string describing the position.
            moves: Optional list of moves in UCI notation to apply after the FEN.
        """
        self.board = chess.Board(fen)
        self._apply_moves(moves)

    # ------------------------------------------------------------------
    # Search / move generation
    # ------------------------------------------------------------------

    def pick_move(self) -> str:
        """Pick a uniformly random legal move.

        Returns:
            The selected move in UCI notation (e.g. ``"e2e4"``).

        Raises:
            ValueError: If there are no legal moves (game is over).
        """
        legal_moves = list(self.board.legal_moves)
        if not legal_moves:
            raise ValueError("No legal moves available — the game is over.")

        chosen = random.choice(legal_moves)
        logger.debug("Picked random move: %s from %d legal moves", chosen, len(legal_moves))
        return chosen.uci()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_moves(self, moves: list[str] | None) -> None:
        """Push a sequence of UCI moves onto the current board.

        Args:
            moves: List of moves in UCI notation, or ``None`` to do nothing.
        """
        if moves is None:
            return
        for uci_move in moves:
            self.board.push_uci(uci_move)
