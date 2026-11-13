"""The UCI protocol: commands in on stdin, answers out on stdout.

The engine understands ``uci``, ``isready``, ``ucinewgame``, ``position``,
``go``, ``stop`` and ``quit``, and ignores anything else, as UCI asks.
``go`` takes ``wtime``, ``btime``, ``winc``, ``binc``, ``movestogo``,
``movetime``, ``depth``, ``nodes``, ``mate`` and ``infinite``. ``go infinite``,
and ``go`` on its own, search until ``stop`` and hold ``bestmove`` until then.
``go mate n`` searches 2n plies, the depth at which a mate in n shows.

The main thread reads commands. ``go`` starts the search in the searcher's
background thread, which writes an ``info`` line after each completed depth
and ``bestmove`` at the end, so the main thread can answer ``isready`` and
act on ``stop`` or ``quit`` during a search. Every line goes out through one
lock, so lines never interleave. A command that changes what's searched
(``position``, ``ucinewgame``, ``go``) first stops a running search and
waits for its ``bestmove``.

``info`` lines give the score from the engine's own point of view, as the
SPRT runner's adjudication expects: ``score cp 35`` when it thinks it's
ahead, and ``score mate 3`` or ``score mate -3`` when it sees mate.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass

import lab_engine
from lab_engine import runtime
from lab_engine.board import SIDE, WHITE
from lab_engine.movegen import NO_MOVE
from lab_engine.position import Position, move_to_uci
from lab_engine.search import MATE, MAX_DEPTH, MAX_PLY, Iteration, Searcher, SearchLimits
from lab_engine.timeman import TimeBudget, clock_budget, movetime_budget

# Every compiled module is imported by now. If the source changed while they
# were compiled, stop rather than run code compiled from a mix of versions.
runtime.check_source_unchanged(lab_engine.SOURCE_DIGEST)

logger = logging.getLogger(__name__)

ENGINE_NAME = "LabEngine"
ENGINE_AUTHOR = "chess-vibe"

_GO_NUMBERS = (
    "wtime",
    "btime",
    "winc",
    "binc",
    "movestogo",
    "movetime",
    "depth",
    "nodes",
    "mate",
)


@dataclass(frozen=True)
class GoCommand:
    """The parameters of a ``go`` command: milliseconds, plies, nodes, and moves for ``mate``."""

    wtime: int | None = None
    btime: int | None = None
    winc: int | None = None
    binc: int | None = None
    movestogo: int | None = None
    movetime: int | None = None
    depth: int | None = None
    nodes: int | None = None
    mate: int | None = None
    infinite: bool = False


def parse_go(tokens: list[str]) -> GoCommand:
    """Read a ``go`` command's parameters.

    Words it doesn't know (``ponder``, ``searchmoves``) and values that
    aren't integers are skipped. ``go`` on its own means ``go infinite``.

    Args:
        tokens: The command split on whitespace, ``"go"`` first.
    """
    numbers: dict[str, int] = {}
    infinite = len(tokens) == 1
    i = 1
    while i < len(tokens):
        word = tokens[i]
        if word == "infinite":
            infinite = True
        elif word in _GO_NUMBERS and i + 1 < len(tokens):
            try:
                numbers[word] = int(tokens[i + 1])
                i += 1
            except ValueError:
                logger.warning("Ignoring go %s %s: not an integer", word, tokens[i + 1])
        i += 1
    return GoCommand(
        wtime=numbers.get("wtime"),
        btime=numbers.get("btime"),
        winc=numbers.get("winc"),
        binc=numbers.get("binc"),
        movestogo=numbers.get("movestogo"),
        movetime=numbers.get("movetime"),
        depth=numbers.get("depth"),
        nodes=numbers.get("nodes"),
        mate=numbers.get("mate"),
        infinite=infinite,
    )


def search_limits(go: GoCommand, side: int) -> SearchLimits:
    """Turn a ``go`` command into limits for a search by ``side``.

    ``infinite`` overrides the rest. Otherwise every limit given applies, and
    the search stops at the first one reached. ``mate n`` limits the depth to
    2n plies. ``movetime`` takes the place of the clocks if both are given.
    Without the mover's own clock, the clocks set no time limit.

    Args:
        go: The parsed command.
        side: The side to move, WHITE or BLACK.
    """
    if go.infinite:
        return SearchLimits(infinite=True)
    depths = [d for d in (go.depth, None if go.mate is None else 2 * go.mate) if d is not None]
    depth = min(max(min(depths), 1), MAX_DEPTH) if depths else None
    nodes = None if go.nodes is None else max(go.nodes, 1)
    budget: TimeBudget | None = None
    if go.movetime is not None:
        budget = movetime_budget(go.movetime)
    else:
        remaining = go.wtime if side == WHITE else go.btime
        increment = go.winc if side == WHITE else go.binc
        if remaining is not None:
            budget = clock_budget(max(remaining, 0), max(increment or 0, 0), go.movestogo)
    if budget is None:
        return SearchLimits(depth=depth, nodes=nodes)
    return SearchLimits(depth=depth, nodes=nodes, soft_ms=budget.soft_ms, hard_ms=budget.hard_ms)


def format_score(score: int) -> str:
    """Return a search score as UCI writes it: ``cp 35``, ``mate 3`` or ``mate -3``.

    Mate is counted in moves, not plies: the engine mating on its next move
    is ``mate 1``, and being mated after its move is ``mate -1``.
    """
    if score >= MATE - MAX_PLY:
        return f"mate {(MATE - score + 1) // 2}"
    if score <= -(MATE - MAX_PLY):
        return f"mate -{(MATE + score) // 2}"
    return f"cp {score}"


def format_info(iteration: Iteration) -> str:
    """Return the ``info`` line for a completed depth. ``pv`` comes last."""
    nps = iteration.nodes * 1000 // max(iteration.time_ms, 1)
    line = " ".join(move_to_uci(move) for move in iteration.pv)
    return (
        f"info depth {iteration.depth} score {format_score(iteration.score)} "
        f"nodes {iteration.nodes} nps {nps} time {iteration.time_ms} pv {line}"
    )


def _parse_position(tokens: list[str]) -> Position:
    """Read ``position startpos|fen <FEN> [moves ...]``.

    Raises:
        ValueError: If the command is malformed, the FEN invalid, or a move
            illegal.
    """
    if "moves" in tokens:
        split = tokens.index("moves")
        setup, moves = tokens[1:split], tokens[split + 1 :]
    else:
        setup, moves = tokens[1:], []
    if setup == ["startpos"]:
        position = Position.start()
    elif setup[:1] == ["fen"]:
        position = Position.from_fen(" ".join(setup[1:]))
    else:
        raise ValueError("expected startpos or fen")
    for move in moves:
        position.push_uci(move)
    return position


class UciSession:
    """The engine's side of a UCI conversation.

    It takes one command line at a time and writes its answers through
    ``write``, from the calling thread or the search thread.
    """

    def __init__(self, write: Callable[[str], None]) -> None:
        """Start with the initial position and no search running.

        Args:
            write: Sends one line, without its newline. It's called under a
                lock, so lines never interleave.
        """
        self._write = write
        self._lock = threading.Lock()
        self._position = Position.start()
        self._searcher = Searcher()

    @property
    def searching(self) -> bool:
        """Whether a search is running."""
        return self._searcher.is_searching

    def warm_up(self) -> None:
        """Run a depth-1 search, writing nothing.

        Nothing is compiled after import, but numba's first call with arrays
        does one-time work in Python (it imports ``numpy.ma``, for one), and
        a thread starts for the first time. Doing that before the first
        command keeps it out of the first move's time.
        """
        self._searcher.search(Position.start(), SearchLimits(depth=1))

    def handle(self, line: str) -> bool:
        """Act on one command line.

        Returns:
            False once the session should end (after ``quit``), else True.
        """
        tokens = line.split()
        if not tokens:
            return True
        command = tokens[0]
        if command == "uci":
            self._send(f"id name {ENGINE_NAME}")
            self._send(f"id author {ENGINE_AUTHOR}")
            self._send("uciok")
        elif command == "isready":
            self._send("readyok")
        elif command == "ucinewgame":
            self._stop_search()
            self._position = Position.start()
        elif command == "position":
            self._stop_search()
            self._set_position(tokens)
        elif command == "go":
            self._stop_search()
            self._go(tokens)
        elif command == "stop":
            self._stop_search()
        elif command == "quit":
            self._stop_search()
            return False
        else:
            logger.debug("Ignoring %r", line)
        return True

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for a running search to finish; return whether none is running."""
        return self._searcher.wait(timeout)

    def _send(self, line: str) -> None:
        with self._lock:
            self._write(line)

    def _stop_search(self) -> None:
        """Stop a running search, if any, and wait for its ``bestmove``."""
        self._searcher.stop()
        self._searcher.wait()

    def _set_position(self, tokens: list[str]) -> None:
        """Set the position, or keep the current one if the command is bad."""
        try:
            self._position = _parse_position(tokens)
        except ValueError as error:
            logger.warning("Ignoring %r: %s", " ".join(tokens), error)

    def _go(self, tokens: list[str]) -> None:
        limits = search_limits(parse_go(tokens), int(self._position.state[SIDE]))
        self._searcher.start(self._position.copy(), limits, self._report, self._bestmove)

    def _report(self, iteration: Iteration) -> None:
        self._send(format_info(iteration))

    def _bestmove(self, move: int) -> None:
        self._send(f"bestmove {'0000' if move == NO_MOVE else move_to_uci(move)}")


def _write_line(line: str) -> None:
    """Write one line to stdout and flush it, as UCI needs."""
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main() -> None:
    """Run the engine on stdin and stdout, until ``quit`` or the end of input."""
    logging.basicConfig(
        level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    session = UciSession(_write_line)
    session.warm_up()
    for line in sys.stdin:
        if not session.handle(line):
            return
    session.handle("quit")
