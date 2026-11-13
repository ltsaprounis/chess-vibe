"""Search: iterative-deepening alpha-beta.

The search is negamax alpha-beta to a fixed depth, repeated at depths 1, 2,
3 and so on until a limit is reached. Each depth tries the previous depth's
principal variation first, then captures by most valuable victim and least
valuable attacker, then quiet moves. A leaf is scored by ``evaluate``.

It is plain on purpose, so that the autoresearch loop has room to improve it
and each gain can be traced to one change. It has no transposition table, no
quiescence search, no null-move pruning and no reductions, and it doesn't
score draws by repetition or the fifty-move rule. The runner still applies
those rules to the game.

The compiled ``alpha_beta`` does the work. ``Searcher`` runs it in a
background thread, one depth at a time, and stops it when it is told to or
when a limit is reached:

- a node limit, which ``alpha_beta`` checks at every node;
- a depth limit, checked between depths;
- a soft time limit, checked between depths: no new depth starts after it;
- a hard time limit, which a timer turns into a stop.

``alpha_beta`` releases the GIL, so the UCI thread can read ``stop`` while it
runs. Depth 1 always completes, so there is always a move to play. For ``go
infinite`` the move waits for ``stop``, even if the search ends sooner, on a
forced mate or with no move to play.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from lab_engine.board import EMPTY, PAWN, SIDE
from lab_engine.evaluate import PIECE_VALUES, evaluate
from lab_engine.jit import TABLE, VECTOR, compiled
from lab_engine.movegen import (
    EN_PASSANT_CAPTURE,
    MAX_MOVES,
    NO_MOVE,
    UNDO_SIZE,
    generate_moves,
    in_check,
    make_move,
    move_from,
    move_kind,
    move_promotion,
    move_to,
    mover_in_check,
    unmake_move,
)
from lab_engine.position import Position

logger = logging.getLogger(__name__)

Array = NDArray[np.int64]

MAX_PLY = 64
"""Plies the search's arrays hold."""

MAX_DEPTH = MAX_PLY - 1
"""The deepest search allowed."""

MATE = 32_000
"""The score of giving mate now. Mate in n plies scores ``MATE - n``."""

INFINITY = MATE + 1
"""Beyond any score."""

NO_NODE_LIMIT = 2**62

# Fields of the control array, shared by the search and the threads that
# stop it.
STOP = 0  # set to 1 to ask the search to stop
ABORTED = 1  # set to 1 by the search when it abandons a depth
NODES = 2  # nodes searched so far
NODE_LIMIT = 3  # the search stops once it has searched this many
INTERRUPTIBLE = 4  # 0 while depth 1 runs, so there's always a move
CONTROL_SIZE = 5

PV_FIRST = 1_000_000_000
"""Sort key of the previous depth's move at this ply: tried first."""

CAPTURE_FIRST = 1_000_000
"""Sort key added for a capture or a promotion: tried before quiet moves."""


# ---------------------------------------------------------------------------
# The compiled search
# ---------------------------------------------------------------------------


@compiled(f"void({VECTOR}, {VECTOR}, {VECTOR}, int64, int64)")
def order_moves(board: Array, moves: Array, keys: Array, count: int, pv_move: int) -> None:
    """Sort the first ``count`` moves, the most promising first.

    First the previous depth's move at this ply (``pv_move``), then captures
    and promotions, the most valuable victim first and, among equal victims,
    the least valuable attacker first, then quiet moves in generated order.

    Args:
        board: The position's board, before any of the moves.
        moves: The moves, sorted in place.
        keys: Scratch space for the sort keys, as long as ``moves``.
        count: How many of ``moves`` to sort.
        pv_move: The move to try first, or ``NO_MOVE``.
    """
    for i in range(count):
        move = moves[i]
        if move == pv_move:
            keys[i] = PV_FIRST
            continue
        key = 0
        victim = abs(board[move_to(move)])
        if move_kind(move) == EN_PASSANT_CAPTURE:
            victim = PAWN
        if victim != EMPTY:
            attacker = abs(board[move_from(move)])
            key += CAPTURE_FIRST + 10 * PIECE_VALUES[victim] - PIECE_VALUES[attacker]
        promotion = move_promotion(move)
        if promotion != 0:
            key += CAPTURE_FIRST + PIECE_VALUES[promotion]
        keys[i] = key
    # Insertion sort, highest key first; equal keys keep their order.
    for i in range(1, count):
        move = moves[i]
        key = keys[i]
        j = i - 1
        while j >= 0 and keys[j] < key:
            moves[j + 1] = moves[j]
            keys[j + 1] = keys[j]
            j -= 1
        moves[j + 1] = move
        keys[j + 1] = key


@compiled(
    f"int64({VECTOR}, {VECTOR}, int64, int64, int64, int64, "
    f"{TABLE}, {TABLE}, {TABLE}, {TABLE}, {VECTOR}, {VECTOR})"
)
def alpha_beta(
    board: Array,
    state: Array,
    depth: int,
    ply: int,
    alpha: int,
    beta: int,
    moves: Array,
    keys: Array,
    undo: Array,
    pv: Array,
    previous_pv: Array,
    control: Array,
) -> int:
    """Search the position ``depth`` plies deep, and return its score.

    Negamax alpha-beta: the score is from the side to move's point of view,
    and a score at or above ``beta`` stops the search of this position, which
    the opponent would avoid.

    Args:
        board: The position's board; restored before returning.
        state: The position's state; restored before returning.
        depth: Plies left to search.
        ply: Plies from the root, which selects the rows used below.
        alpha: A score the side to move is already sure of.
        beta: A score the opponent is already sure of holding it to.
        moves: One row of ``MAX_MOVES`` moves per ply.
        keys: Sort keys, shaped like ``moves``.
        undo: One undo record per ply.
        pv: Row ``ply`` gets this position's principal variation: its length
            in column 0, then the moves.
        previous_pv: The previous depth's principal variation, one move per
            ply, tried first at its ply.
        control: Stop requests, node counts and limits (see ``STOP``).

    Returns:
        The score. Meaningless once ``control[ABORTED]`` is set.
    """
    control[NODES] += 1
    pv[ply, 0] = 0
    if control[INTERRUPTIBLE] != 0 and (
        control[STOP] != 0 or control[NODES] >= control[NODE_LIMIT]
    ):
        control[ABORTED] = 1
        return 0
    if depth == 0:
        return evaluate(board, state[SIDE])

    count = generate_moves(board, state, moves[ply])
    order_moves(board, moves[ply], keys[ply], count, previous_pv[ply])
    legal = 0
    for i in range(count):
        move = moves[ply, i]
        make_move(board, state, move, undo[ply])
        if mover_in_check(board, state):
            unmake_move(board, state, move, undo[ply])
            continue
        legal += 1
        score = -alpha_beta(
            board,
            state,
            depth - 1,
            ply + 1,
            -beta,
            -alpha,
            moves,
            keys,
            undo,
            pv,
            previous_pv,
            control,
        )
        unmake_move(board, state, move, undo[ply])
        if control[ABORTED] != 0:
            return 0
        if score > alpha:
            alpha = score
            # This move, then the line below it.
            pv[ply, 1] = move
            length = pv[ply + 1, 0]
            for j in range(length):
                pv[ply, 2 + j] = pv[ply + 1, 1 + j]
            pv[ply, 0] = length + 1
            if alpha >= beta:
                break
    if legal == 0:
        return -MATE + ply if in_check(board, state) else 0
    return alpha


# ---------------------------------------------------------------------------
# Running a search
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchLimits:
    """When a search stops. Without any limit it runs until ``stop``, a forced
    mate, or ``MAX_DEPTH``.

    Attributes:
        depth: The deepest depth to search, 1 to ``MAX_DEPTH``.
        nodes: Stop after this many nodes, at least 1. Depth 1 always
            completes, so a tiny limit can be overrun.
        soft_ms: Start no new depth after this many milliseconds.
        hard_ms: Abandon the search after this many milliseconds, and play
            the last completed depth's move.
        infinite: Search until ``stop``, and hold the move until then too,
            as ``go infinite`` asks. It takes no other limit.
    """

    depth: int | None = None
    nodes: int | None = None
    soft_ms: int | None = None
    hard_ms: int | None = None
    infinite: bool = False

    def __post_init__(self) -> None:
        """Refuse limits the search can't honour."""
        if self.depth is not None and not 1 <= self.depth <= MAX_DEPTH:
            raise ValueError(f"depth must be 1 to {MAX_DEPTH}, got {self.depth}")
        if self.nodes is not None and self.nodes < 1:
            raise ValueError(f"nodes must be at least 1, got {self.nodes}")
        for name, value in (("soft_ms", self.soft_ms), ("hard_ms", self.hard_ms)):
            if value is not None and value < 0:
                raise ValueError(f"{name} must be at least 0, got {value}")
        limits = (self.depth, self.nodes, self.soft_ms, self.hard_ms)
        if self.infinite and any(limit is not None for limit in limits):
            raise ValueError("infinite must be the only limit")


@dataclass(frozen=True)
class Iteration:
    """The result of one completed depth.

    Attributes:
        depth: The depth searched.
        score: The score, from the side to move's point of view.
        nodes: Nodes searched so far, at every depth.
        time_ms: Milliseconds since the search started.
        pv: The principal variation, starting with the best move.
    """

    depth: int
    score: int
    nodes: int
    time_ms: int
    pv: tuple[int, ...]


def _elapsed_ms(started: float) -> int:
    """Return whole milliseconds since ``started``, a ``time.perf_counter()``."""
    return int((time.perf_counter() - started) * 1000)


def _ignore(_: Iteration) -> None:
    """Report nothing."""


class Searcher:
    """Runs one search at a time, in a background thread.

    The arrays the compiled search works in are allocated once and reused.
    """

    def __init__(self) -> None:
        """Allocate the search's arrays."""
        rows = MAX_PLY + 1
        self._moves = np.zeros((rows, MAX_MOVES), dtype=np.int64)
        self._keys = np.zeros((rows, MAX_MOVES), dtype=np.int64)
        self._undo = np.zeros((rows, UNDO_SIZE), dtype=np.int64)
        self._pv = np.zeros((rows, MAX_PLY + 2), dtype=np.int64)
        self._previous_pv = np.zeros(rows, dtype=np.int64)
        self._control = np.zeros(CONTROL_SIZE, dtype=np.int64)
        self._thread: threading.Thread | None = None
        self._timer: threading.Timer | None = None
        self._stop_requested = threading.Event()

    @property
    def is_searching(self) -> bool:
        """Whether a search is running."""
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        position: Position,
        limits: SearchLimits,
        report: Callable[[Iteration], None],
        done: Callable[[int], None],
    ) -> None:
        """Start searching ``position`` in a background thread.

        Args:
            position: The position to search. The search changes and restores
                it, so it must not be changed until the search is done.
            limits: When to stop.
            report: Called, in the search thread, after each completed depth.
            done: Called, in the search thread, with the best move once the
                search stops: ``NO_MOVE`` if there's no legal move.

        Raises:
            RuntimeError: If a search is already running.
        """
        if self.is_searching:
            raise RuntimeError("a search is already running")
        started = time.perf_counter()
        # Cleared here, before the thread exists, so a stop sent straight
        # after start can't be lost.
        self._control[STOP] = 0
        self._stop_requested.clear()
        self._timer = None
        if limits.hard_ms is not None:
            self._timer = threading.Timer(limits.hard_ms / 1000, self.stop)
            self._timer.daemon = True
            self._timer.start()
        self._thread = threading.Thread(
            target=self._run,
            args=(position, limits, report, done, started),
            name="search",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Ask the running search to stop. Safe from any thread."""
        self._control[STOP] = 1
        self._stop_requested.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the running search, if any, to finish.

        Args:
            timeout: Seconds to wait at most; ``None`` waits as long as it takes.

        Returns:
            Whether no search is running any more.
        """
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def search(
        self,
        position: Position,
        limits: SearchLimits,
        report: Callable[[Iteration], None] | None = None,
    ) -> int:
        """Search ``position`` and return the best move, or ``NO_MOVE``.

        Runs in the background thread like ``start``, and waits for it. With
        ``infinite``, it doesn't return until another thread calls ``stop``.
        """
        best: list[int] = []
        self.start(position, limits, report or _ignore, best.append)
        self.wait()
        return best[0]

    def _run(
        self,
        position: Position,
        limits: SearchLimits,
        report: Callable[[Iteration], None],
        done: Callable[[int], None],
        started: float,
    ) -> None:
        """The search thread: search, then hand over the best move."""
        try:
            best_move = self._search(position, limits, report, started)
        except Exception:
            # A search that fails can't answer. Exiting makes the runner
            # score the game as the crash it is, not as a loss on time.
            logger.exception("The search failed; exiting")
            os._exit(1)
        finally:
            timer = self._timer
            if timer is not None:
                timer.cancel()
                timer.join()  # so a stop it was already making can't land later
        if limits.infinite:
            self._stop_requested.wait()
        done(best_move)

    def _search(
        self,
        position: Position,
        limits: SearchLimits,
        report: Callable[[Iteration], None],
        started: float,
    ) -> int:
        """Search depth after depth until a limit; return the best move."""
        control = self._control
        control[ABORTED] = 0
        control[NODES] = 0
        control[NODE_LIMIT] = NO_NODE_LIMIT if limits.nodes is None else limits.nodes
        control[INTERRUPTIBLE] = 0
        self._previous_pv[:] = NO_MOVE
        best_move = NO_MOVE
        for depth in range(1, (limits.depth or MAX_DEPTH) + 1):
            score = alpha_beta(
                position.board,
                position.state,
                depth,
                0,
                -INFINITY,
                INFINITY,
                self._moves,
                self._keys,
                self._undo,
                self._pv,
                self._previous_pv,
                control,
            )
            if control[ABORTED] != 0:
                break  # cut short: keep the last completed depth's move
            control[INTERRUPTIBLE] = 1
            length = int(self._pv[0, 0])
            if length == 0:
                break  # no legal move: checkmate or stalemate
            line = tuple(int(move) for move in self._pv[0, 1 : length + 1])
            best_move = line[0]
            self._previous_pv[:] = NO_MOVE
            self._previous_pv[:length] = line
            elapsed = _elapsed_ms(started)
            report(Iteration(depth, int(score), int(control[NODES]), elapsed, line))
            if abs(score) >= MATE - MAX_PLY:
                break  # a forced mate, for either side: no depth changes it
            if control[STOP] != 0 or control[NODES] >= control[NODE_LIMIT]:
                break
            if limits.soft_ms is not None and elapsed >= limits.soft_ms:
                break
        return best_move
