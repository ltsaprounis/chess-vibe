"""UCI (Universal Chess Interface) protocol handler for the random engine.

The UCI protocol is a text-based, line-oriented protocol that chess GUIs use
to communicate with engines.  The full specification lives at
https://backscattering.de/chess/uci/ — this module implements the minimal
subset required for a working engine:

Protocol flow
-------------
1. GUI sends ``uci``.  Engine replies with ``id name ...``, ``id author ...``,
   and ``uciok``.
2. GUI sends ``isready``.  Engine replies ``readyok`` once initialisation is
   complete.
3. GUI sends ``position startpos [moves ...]`` or ``position fen <fen> [moves ...]``
   to set the board.
4. GUI sends ``go [wtime ... btime ... ...]``.  Engine searches and replies
   with ``info ...`` lines followed by ``bestmove <move>``.
5. GUI sends ``quit`` to terminate the engine.

Separation of concerns
----------------------
* **uci.py** (this file) — reads stdin line-by-line, parses UCI commands,
  dispatches to ``RandomEngine``, and writes responses to stdout.
* **engine.py** — pure game logic; no I/O.

This design makes it easy to fork the engine: copy the directory, replace
``engine.py`` with your own logic, and keep ``uci.py`` unchanged.
"""

from __future__ import annotations

import logging
import sys

from random_engine.engine import RandomEngine

logger = logging.getLogger(__name__)

# Engine metadata reported during the UCI handshake.
ENGINE_NAME = "RandomEngine"
ENGINE_AUTHOR = "chess-vibe"


def _send(line: str) -> None:
    """Write a single UCI response line to stdout and flush immediately.

    Flushing is critical — UCI GUIs read line-by-line and will hang if the
    output is buffered.

    Args:
        line: The response line to send (without trailing newline).
    """
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _handle_uci() -> None:
    """Handle the ``uci`` command.

    Responds with the engine's identity and ``uciok`` to signal that the
    engine is ready to receive further commands.  A real engine would also
    advertise configurable options here (``option name ...``).
    """
    _send(f"id name {ENGINE_NAME}")
    _send(f"id author {ENGINE_AUTHOR}")
    # No configurable options for the random engine.
    _send("uciok")


def _handle_isready() -> None:
    """Handle the ``isready`` command.

    The GUI sends this to synchronise — the engine must reply ``readyok``
    only when it is truly ready to process commands.  For the random engine
    there is no initialisation work, so we reply immediately.
    """
    _send("readyok")


def _handle_position(engine: RandomEngine, tokens: list[str]) -> None:
    """Handle the ``position`` command.

    Supported forms::

        position startpos [moves e2e4 e7e5 ...]
        position fen <fen> [moves e2e4 e7e5 ...]

    Args:
        engine: The engine instance whose board will be updated.
        tokens: The full command split on whitespace (``["position", ...]``).
    """
    if len(tokens) < 2:
        logger.warning("Malformed position command: %s", " ".join(tokens))
        return

    # Extract optional trailing moves list.
    moves: list[str] | None = None
    if "moves" in tokens:
        moves_idx = tokens.index("moves")
        moves = tokens[moves_idx + 1 :]

    if tokens[1] == "startpos":
        engine.set_position_startpos(moves=moves)
    elif tokens[1] == "fen":
        # FEN occupies tokens[2:8] (six space-separated fields).
        fen_end = tokens.index("moves") if "moves" in tokens else len(tokens)
        fen = " ".join(tokens[2:fen_end])
        engine.set_position_fen(fen, moves=moves)
    else:
        logger.warning("Unknown position type: %s", tokens[1])


def _handle_go(engine: RandomEngine) -> None:
    """Handle the ``go`` command.

    Ignores all time-control parameters — the random engine always picks a
    move instantly.  Sends a dummy ``info`` line (``score cp 0 depth 0``)
    before the ``bestmove`` line, as required by the acceptance criteria.

    Args:
        engine: The engine instance to query for a move.
    """
    move = engine.pick_move()

    # Report a dummy evaluation — the random engine has no real search.
    _send("info score cp 0 depth 0")
    _send(f"bestmove {move}")


def run_uci_loop() -> None:
    """Main UCI read-eval-print loop.

    Reads commands from stdin one line at a time and dispatches them to the
    appropriate handler.  Exits when ``quit`` is received or stdin is closed.
    """
    engine = RandomEngine()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        tokens = line.split()
        command = tokens[0]

        if command == "uci":
            _handle_uci()
        elif command == "isready":
            _handle_isready()
        elif command == "position":
            _handle_position(engine, tokens)
        elif command == "go":
            _handle_go(engine)
        elif command == "quit":
            break
        else:
            # UCI specifies that unknown commands should be silently ignored.
            logger.debug("Ignoring unknown command: %s", line)
