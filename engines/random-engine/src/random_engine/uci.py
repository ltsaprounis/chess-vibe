"""UCI (Universal Chess Interface) protocol handler for the random engine.

Reads lines from stdin, dispatches each command to the appropriate handler,
and writes responses to stdout.  This module is the **only** place that
touches I/O — ``engine.py`` is kept completely free of I/O concerns.

UCI quick-reference (commands the GUI → engine):
  uci          — ask engine to identify itself and list options
  isready      — ping; engine replies "readyok" when ready
  ucinewgame   — start of a new game (we reset the board)
  position ... — set up the board state
  go ...       — start searching; we pick a move and reply immediately
  stop         — stop the current search (no-op for us)
  quit         — exit the process

Responses written by this module (engine → GUI):
  id name <name>
  id author <author>
  uciok
  readyok
  info score cp <cp> depth <depth>
  bestmove <move>

Reference: https://www.chessprogramming.org/UCI
"""

from __future__ import annotations

import logging
import sys

from random_engine.engine import NoLegalMovesError, RandomEngine

logger = logging.getLogger(__name__)

# Engine identity strings — change these when forking.
_ENGINE_NAME = "RandomEngine"
_ENGINE_AUTHOR = "chess-vibe"


class UCIHandler:
    """Read UCI commands from *stdin* and write responses to *stdout*.

    Instantiate once and call :meth:`run` to start the blocking I/O loop.
    All state lives in the :class:`~random_engine.engine.RandomEngine`
    instance so this class stays thin.

    Args:
        engine: The engine instance used to pick moves.
    """

    def __init__(self, engine: RandomEngine) -> None:
        """Initialise the handler with a RandomEngine instance.

        Args:
            engine: Engine instance that manages board state.
        """
        self._engine = engine

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Block on stdin and dispatch commands until 'quit' is received.

        Reads one line at a time; unknown commands are silently ignored per
        the UCI specification (GUIs may send unrecognised tokens).
        """
        logger.info("UCI handler starting")

        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue  # ignore blank lines

            logger.debug("recv: %r", line)
            tokens = line.split()
            command = tokens[0]

            if command == "uci":
                self._handle_uci()
            elif command == "isready":
                self._handle_isready()
            elif command == "ucinewgame":
                self._handle_ucinewgame()
            elif command == "position":
                self._handle_position(tokens[1:])
            elif command == "go":
                self._handle_go()
            elif command == "stop":
                pass  # random engine needs no explicit stop handling
            elif command == "quit":
                self._handle_quit()
                return  # exit the loop — process will terminate
            else:
                # UCI spec: unrecognised tokens must be ignored
                logger.debug("Ignoring unknown command: %r", command)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _handle_uci(self) -> None:
        """Respond to the 'uci' command with engine identity and 'uciok'."""
        self._send(f"id name {_ENGINE_NAME}")
        self._send(f"id author {_ENGINE_AUTHOR}")
        # No custom options — a real engine would send "option ..." lines here
        self._send("uciok")

    def _handle_isready(self) -> None:
        """Respond to 'isready' with 'readyok'.

        The engine is always ready because we hold no background threads.
        """
        self._send("readyok")

    def _handle_ucinewgame(self) -> None:
        """Reset board state for a new game."""
        self._engine.set_position_startpos()

    def _handle_position(self, args: list[str]) -> None:
        """Parse and apply a 'position' command.

        Supports two forms::

            position startpos [moves <move1> <move2> ...]
            position fen <fen> [moves <move1> <move2> ...]

        Args:
            args: Tokens after the 'position' keyword.
        """
        if not args:
            logger.warning("Received 'position' with no arguments")
            return

        if args[0] == "startpos":
            # Extract optional move list after "moves" keyword
            moves = _extract_moves(args[1:])
            self._engine.set_position_startpos(moves=moves)

        elif args[0] == "fen":
            # FEN is tokens 1-6 (up to 6 fields), then optional "moves ..."
            # We consume until we hit the "moves" keyword or end of tokens.
            fen_tokens: list[str] = []
            rest: list[str] = args[1:]
            for i, token in enumerate(rest):
                if token == "moves":
                    rest = rest[i:]
                    break
                fen_tokens.append(token)
            else:
                rest = []

            fen = " ".join(fen_tokens)
            moves = _extract_moves(rest)
            self._engine.set_position_fen(fen, moves=moves)

        else:
            logger.warning("Unknown position subcommand: %r", args[0])

    def _handle_go(self) -> None:
        """Handle the 'go' command: pick a random move and report it.

        Time control arguments (wtime, btime, movetime, depth, …) are
        intentionally ignored — the random engine doesn't search.

        Outputs::

            info score cp 0 depth 0
            bestmove <move>
        """
        try:
            move = self._engine.pick_move()
        except NoLegalMovesError:
            # Terminal position — report null move per convention
            logger.warning("No legal moves; reporting bestmove (none)")
            self._send("info score cp 0 depth 0")
            self._send("bestmove (none)")
            return

        self._send("info score cp 0 depth 0")
        self._send(f"bestmove {move}")

    def _handle_quit(self) -> None:
        """Handle the 'quit' command by logging and returning cleanly."""
        logger.info("Received 'quit'; exiting")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _send(self, message: str) -> None:
        """Write a line to stdout and flush immediately.

        Args:
            message: The line to send (no trailing newline needed).
        """
        logger.debug("send: %r", message)
        print(message, flush=True)  # UCI must write to stdout


# ------------------------------------------------------------------
# Module-level helper
# ------------------------------------------------------------------


def _extract_moves(tokens: list[str]) -> list[str]:
    """Extract the move list from tokens following a 'moves' keyword.

    Args:
        tokens: Token slice that may start with ``"moves"`` followed by
                move strings, or may be empty.

    Returns:
        List of move strings (empty list if no 'moves' keyword found).
    """
    if tokens and tokens[0] == "moves":
        return tokens[1:]
    return []
