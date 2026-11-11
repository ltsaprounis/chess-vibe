"""CLI entry point and SPRT test orchestrator.

Provides the CLI interface for running SPRT tests between engine pairs.
Outputs JSON-lines to stdout for consumption by the backend. Errors go
to stderr only.

Uses ``multiprocessing`` for game-level parallelism with ``asyncio``
inside each worker for UCI I/O. Workers report results via
``multiprocessing.Queue``; the coordinator aggregates single-threaded.

Message types:
    - ``game_result``: Result of a single game.
    - ``progress``: Running SPRT statistics.
    - ``error``: An error. Most concern one game or worker, and the run
      carries on. A fatal one is the run's last line, and the CLI then
      exits 1.
    - ``complete``: Final SPRT result.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import logging
import multiprocessing
import queue
import shlex
import signal
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, cast

from shared.storage.models import Game, GameResult, Move
from shared.storage.pgn_export import export_game_to_pgn
from shared.time_control import (
    TimeControl,
    parse_time_control,
)
from shared.uci_client import UCIClient

from sprt_runner.adjudication import AdjudicationConfig, validate_syzygy_path
from sprt_runner.game import (
    DEFAULT_MOVE_TIMEOUT_MS,
    DEFAULT_TIME_MARGIN_MS,
    GameConfig,
    play_game,
)
from sprt_runner.openings import OpeningPair, load_openings, make_opening_pairs
from sprt_runner.sprt import SPRTDecision, sprt_test
from sprt_runner.worktree import (
    EngineSpec,
    cleanup_worktree,
    parse_engine_spec,
    resolve_engine_path,
)

logger = logging.getLogger(__name__)

# How long (seconds) one ``result_queue.get()`` in the SPRT loop waits before
# the coordinator checks for dead workers and waits again. It bounds how long
# a dead worker goes unnoticed, and how long the process lingers after
# SIGTERM or SIGINT, since ``asyncio.run`` waits for that blocked get (#198).
# Results are read as soon as they arrive, whatever this is set to.
_QUEUE_TIMEOUT_SECONDS: float = 0.5


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class RunConfig:
    """Configuration for an SPRT test run.

    Attributes:
        base: Base engine specification (ENGINE[:COMMIT]).
        test: Test engine specification (ENGINE[:COMMIT]).
        time_control: Time control for games.
        elo0: Null hypothesis Elo difference.
        elo1: Alternative hypothesis Elo difference.
        alpha: Type-I error rate.
        beta: Type-II error rate.
        book_path: Path to the opening book file (EPD or PGN).
        concurrency: Number of concurrent game worker processes.
        adjudication: Adjudication configuration.
        repo_root: Root of the git repository.
        keep_worktrees: If True, preserve worktrees after test completes.
        output_dir: Directory to write game files into, or None to not save them.
        test_id: SPRT test ID stamped into each game record, or None.
        time_margin_ms: Grace on clock and movetime searches, in milliseconds.
            See :class:`~sprt_runner.game.GameConfig`.
        move_timeout_ms: Limit on depth and nodes searches, in milliseconds.
            See :class:`~sprt_runner.game.GameConfig`.
    """

    base: str
    test: str
    time_control: TimeControl
    elo0: float
    elo1: float
    alpha: float = 0.05
    beta: float = 0.05
    book_path: Path | None = None
    concurrency: int = 1
    adjudication: AdjudicationConfig = field(default_factory=AdjudicationConfig)
    repo_root: Path = field(default_factory=lambda: Path.cwd())
    keep_worktrees: bool = False
    output_dir: Path | None = None
    test_id: str | None = None
    time_margin_ms: int = DEFAULT_TIME_MARGIN_MS
    move_timeout_ms: int = DEFAULT_MOVE_TIMEOUT_MS


class RunAbortedError(Exception):
    """The run stopped on a fatal error, already reported as its last ``error`` line.

    The exception's message is that line's message. ``main()`` exits 1 on it,
    so a caller that checks the exit code doesn't mistake the stop for a
    finished test.
    """


# ---------------------------------------------------------------------------
# JSON-lines message formatters
# ---------------------------------------------------------------------------


def format_game_result_message(
    game_id: str,
    result: GameResult,
    termination: str,
    move_count: int,
) -> str:
    """Format a game result as a JSON-lines message.

    Args:
        game_id: Unique game identifier.
        result: The game result.
        termination: How the game ended.
        move_count: Number of moves played.

    Returns:
        Single-line JSON string.
    """
    return json.dumps(
        {
            "type": "game_result",
            "game_id": game_id,
            "result": result.value,
            "termination": termination,
            "move_count": move_count,
        }
    )


def format_progress_message(
    wins: int,
    losses: int,
    draws: int,
    llr: float,
    lower_bound: float,
    upper_bound: float,
    games_total: int,
    errors: int = 0,
) -> str:
    """Format SPRT progress as a JSON-lines message.

    Args:
        wins: Wins for the test engine.
        losses: Losses for the test engine.
        draws: Drawn games.
        llr: Current log-likelihood ratio.
        lower_bound: Lower SPRT boundary.
        upper_bound: Upper SPRT boundary.
        games_total: Total games played.
        errors: Number of games lost to dead workers.

    Returns:
        Single-line JSON string.
    """
    return json.dumps(
        {
            "type": "progress",
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "llr": llr,
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
            "games_total": games_total,
            "errors": errors,
        }
    )


def format_complete_message(result: str, total_games: int, llr: float) -> str:
    """Format SPRT completion as a JSON-lines message.

    Args:
        result: SPRT outcome ("H0" or "H1").
        total_games: Total games played.
        llr: Final log-likelihood ratio.

    Returns:
        Single-line JSON string.
    """
    return json.dumps(
        {
            "type": "complete",
            "result": result,
            "total_games": total_games,
            "llr": llr,
        }
    )


def format_error_message(message: str) -> str:
    """Format an error as a JSON-lines message.

    Args:
        message: Error description.

    Returns:
        Single-line JSON string.
    """
    return json.dumps(
        {
            "type": "error",
            "message": message,
        }
    )


def _abort(message: str) -> NoReturn:
    """Report a fatal error as an ``error`` line and stop the run.

    Args:
        message: Why the run stopped.

    Raises:
        RunAbortedError: Always, carrying ``message``.
    """
    print(format_error_message(message), flush=True)
    raise RunAbortedError(message)


def format_interrupted_message(games_played: int) -> str:
    """Format an interruption notice as a JSON-lines message.

    Emitted when the SPRT run is interrupted by a signal (SIGINT/SIGTERM)
    before a statistical decision is reached.

    Args:
        games_played: Number of games completed before the interruption.

    Returns:
        Single-line JSON string.
    """
    return json.dumps(
        {
            "type": "interrupted",
            "games_played": games_played,
        }
    )


# ---------------------------------------------------------------------------
# Game persistence helpers
# ---------------------------------------------------------------------------


def _write_game(output_dir: Path, game: Game) -> None:
    """Write a game's eval JSON and PGN to *output_dir*.

    Args:
        output_dir: Directory to write files into.
        game: The game to persist.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    eval_path = output_dir / f"{game.id}.eval.json"
    eval_path.write_text(
        json.dumps(_game_to_dict(game), indent=2),
        encoding="utf-8",
    )

    pgn_path = output_dir / f"{game.id}.pgn"
    pgn_path.write_text(export_game_to_pgn(game), encoding="utf-8")


def _game_to_dict(game: Game) -> dict[str, object]:
    """Serialise a :class:`Game` to a JSON-compatible dict."""
    return {
        "id": game.id,
        "white_engine": game.white_engine,
        "black_engine": game.black_engine,
        "result": game.result.value,
        "opening_name": game.opening_name,
        "sprt_test_id": game.sprt_test_id,
        "start_fen": game.start_fen,
        "time_control": _tc_to_dict(game.time_control) if game.time_control else None,
        "created_at": game.created_at.isoformat(),
        "moves": [
            {
                "uci": m.uci,
                "san": m.san,
                "fen_after": m.fen_after,
                "score_cp": m.score_cp,
                "score_mate": m.score_mate,
                "depth": m.depth,
                "seldepth": m.seldepth,
                "pv": m.pv,
                "nodes": m.nodes,
                "time_ms": m.time_ms,
                "clock_white_ms": m.clock_white_ms,
                "clock_black_ms": m.clock_black_ms,
            }
            for m in game.moves
        ],
    }


def _tc_to_dict(tc: object) -> dict[str, object]:
    """Serialise a time control to a JSON-compatible dict."""
    from shared.time_control import (
        DepthTimeControl,
        FixedTimeControl,
        IncrementTimeControl,
        NodesTimeControl,
    )

    if isinstance(tc, FixedTimeControl):
        return {"type": "fixed_time", "movetime_ms": tc.movetime_ms}
    if isinstance(tc, IncrementTimeControl):
        return {
            "type": "increment",
            "wtime_ms": tc.wtime_ms,
            "btime_ms": tc.btime_ms,
            "winc_ms": tc.winc_ms,
            "binc_ms": tc.binc_ms,
            "moves_to_go": tc.moves_to_go,
        }
    if isinstance(tc, DepthTimeControl):
        return {"type": "depth", "depth": tc.depth}
    if isinstance(tc, NodesTimeControl):
        return {"type": "nodes", "nodes": tc.nodes}
    raise TypeError(f"Unknown time control type: {type(tc)}")


# ---------------------------------------------------------------------------
# SPRT orchestration
# ---------------------------------------------------------------------------


def _resolve_run_command(run_cmd: str, engine_dir: Path) -> str:
    """Resolve an engine run command to a full executable path.

    The run command from ``engines.json`` is relative to the engine
    directory. This function resolves the first token (the executable)
    to a full path while preserving any arguments.

    Args:
        run_cmd: Run command from the engine registry.
        engine_dir: Directory where the engine is located.

    Returns:
        The resolved command string with full path to executable.
    """
    parts = run_cmd.split()
    if not parts:
        return run_cmd

    # Resolve the executable relative to engine_dir
    executable = engine_dir / parts[0]
    resolved_parts = [shlex.quote(str(executable)), *parts[1:]]
    return " ".join(resolved_parts)


# ---------------------------------------------------------------------------
# Worker process for multiprocessing concurrency
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerTask:
    """A game task to be executed by a worker process.

    Attributes:
        game_id: Unique game identifier.
        white_cmd: Resolved command for the white engine.
        black_cmd: Resolved command for the black engine.
        game_config: Configuration for this game.
        swap_colors: Whether colours are swapped (test plays white).
    """

    game_id: str
    white_cmd: str
    black_cmd: str
    game_config: GameConfig
    swap_colors: bool


@dataclass(frozen=True)
class WorkerResult:
    """Result from a worker process sent via multiprocessing.Queue.

    Attributes:
        game_id: Unique game identifier.
        result: The game result.
        termination: How the game ended.
        move_count: Number of moves played.
        swap_colors: Whether colours were swapped.
        error: Error message if the game failed, or None.
    """

    game_id: str
    result: GameResult | None
    termination: str | None
    move_count: int
    swap_colors: bool
    error: str | None = None
    moves: list[Move] = field(default_factory=list[Move])
    start_fen: str | None = None


async def _play_single_game(task: WorkerTask) -> WorkerResult:
    """Play a single game (async, runs inside a worker process).

    Args:
        task: The game task to execute.

    Returns:
        WorkerResult with the game outcome.
    """
    white_engine = UCIClient(task.white_cmd)
    black_engine = UCIClient(task.black_cmd)

    try:
        await white_engine.start()
        await white_engine.uci()
        await white_engine.isready()

        await black_engine.start()
        await black_engine.uci()
        await black_engine.isready()

        outcome = await play_game(
            white=white_engine,
            black=black_engine,
            config=task.game_config,
        )

        return WorkerResult(
            game_id=task.game_id,
            result=outcome.result,
            termination=outcome.termination.value,
            move_count=len(outcome.moves),
            swap_colors=task.swap_colors,
            moves=outcome.moves,
            start_fen=outcome.start_fen,
        )
    except Exception as e:
        return WorkerResult(
            game_id=task.game_id,
            result=None,
            termination=None,
            move_count=0,
            swap_colors=task.swap_colors,
            error=str(e),
        )
    finally:
        await white_engine.quit()
        await black_engine.quit()


def _configure_logging() -> None:
    """Send INFO and above to stderr with a timestamp, level and logger name.

    A no-op once the root logger has a handler, so a forked worker keeps the
    setup it inherited.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def worker_entry(
    task: WorkerTask,
    result_queue: multiprocessing.Queue[WorkerResult],
) -> None:
    """Entry point for a worker process.

    Runs an asyncio event loop to play a single game and puts the
    result onto the multiprocessing queue.

    Args:
        task: The game task to execute.
        result_queue: Queue to send results back to the coordinator.
    """
    # main() starts workers from a forkserver, whose children begin with
    # default signal handling. A worker forked from the coordinator (when a
    # caller chose fork, or for in-process callers on POSIX platforms other
    # than macOS up to Python 3.13) starts with the coordinator's handlers
    # instead. asyncio's SIGTERM handler would make it ignore ``terminate()``,
    # so each worker still playing at a decision or a shutdown would cost 5 s,
    # one after another, before SIGKILL. The coordinator's SIGINT handler
    # belongs to the coordinator's event loop; Python's own handler lets
    # ``asyncio.run`` below install this worker's. (asyncio already clears the
    # inherited wakeup fd at fork.) Under fork, a SIGTERM that lands before
    # these lines, a window of about a millisecond, is still lost; they come
    # first to keep it short.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    # Workers started by forkserver or spawn don't inherit the runner's
    # logging setup, so their warnings would lose their format and their INFO
    # lines would be dropped.
    _configure_logging()
    worker_result = asyncio.run(_play_single_game(task))
    result_queue.put(worker_result)


def _cleanup_workers(
    workers: list[multiprocessing.Process],
) -> list[multiprocessing.Process]:
    """Drop workers that have exited and return the ones that haven't.

    Dropping them releases their ``Process`` objects, and with them the file
    descriptors each one keeps open. ``join()`` is called only once a worker
    has exited, so it returns at once. Joining live workers with a timeout
    after every result cost about a second per worker per result (#187).

    Args:
        workers: Worker processes whose games have finished.

    Returns:
        A new list containing only the workers that haven't exited yet.
    """
    still_alive: list[multiprocessing.Process] = []
    for w in workers:
        if w.is_alive():
            still_alive.append(w)
        else:
            w.join()
    return still_alive


def _drain_results(
    result_queue: multiprocessing.Queue[WorkerResult],
) -> list[WorkerResult]:
    """Read every result already waiting on the queue, without waiting for more.

    A result whose bytes are still being written is read to its end. Call
    this only while nothing else is reading the queue: a reader blocked in
    ``get`` holds the queue's read lock, and this would then find nothing.

    Args:
        result_queue: Queue the workers put their results on.

    Returns:
        The results that were waiting, in queue order.
    """
    drained: list[WorkerResult] = []
    while True:
        try:
            drained.append(result_queue.get_nowait())
        except queue.Empty:
            return drained


def _terminate_all_workers(active_workers: list[multiprocessing.Process]) -> None:
    """Terminate all active workers using SIGTERM → join → SIGKILL.

    Sends ``SIGTERM`` to each worker, waits briefly, and escalates to
    ``SIGKILL`` if the worker does not exit in time.

    Args:
        active_workers: Workers to terminate.
    """
    for w in active_workers:
        w.terminate()
        w.join(timeout=5)
        if w.is_alive():
            logger.warning(
                "Worker %d did not exit after SIGTERM, sending SIGKILL",
                w.pid,
            )
            w.kill()
            w.join(timeout=2)


def _collect_worktree_paths(
    base_spec: EngineSpec, test_spec: EngineSpec, repo_root: Path
) -> list[Path]:
    """Return the list of worktree paths that would be created for the given specs.

    Args:
        base_spec: Parsed base engine specification.
        test_spec: Parsed test engine specification.
        repo_root: Path to the git repository root.

    Returns:
        List of worktree paths (may be empty if neither spec uses a commit).
    """
    paths: list[Path] = []
    seen: set[str] = set()
    for spec in (base_spec, test_spec):
        if spec.commit is not None and spec.commit not in seen:
            seen.add(spec.commit)
            paths.append(repo_root / ".worktrees" / spec.commit)
    return paths


async def run_sprt(config: RunConfig) -> None:
    """Run a complete SPRT test.

    Resolves engine paths, loads opening book, plays games using
    ``multiprocessing`` for game-level parallelism (with ``asyncio``
    inside each worker for UCI I/O), and streams JSON-lines progress
    to stdout. Workers report via ``multiprocessing.Queue``; the
    coordinator aggregates single-threaded.

    Worktrees created during engine resolution are cleaned up after the
    test completes (or fails), unless ``config.keep_worktrees`` is True.

    On ``SIGTERM`` the current asyncio task is cancelled, which triggers
    graceful worker termination inside ``_run_sprt_inner``.

    Args:
        config: Run configuration.

    Raises:
        RunAbortedError: If the run stopped on a fatal error, such as a failed
            engine build, an unreadable opening book, a broken result queue,
            or a full round of worker deaths. It's raised after the error is
            printed and after the workers and worktrees are cleaned up.
    """
    # Register SIGTERM → cancel-task handler so SIGTERM triggers the same
    # graceful shutdown path as SIGINT (KeyboardInterrupt → CancelledError).
    loop = asyncio.get_running_loop()
    current_task = asyncio.current_task()

    def _sigterm_handler() -> None:
        logger.info("Received SIGTERM — cancelling SPRT task")
        if current_task is not None:
            current_task.cancel()

    sigterm_registered = False
    try:
        loop.add_signal_handler(signal.SIGTERM, _sigterm_handler)
        sigterm_registered = True
    except NotImplementedError:
        pass  # Windows — SIGTERM via add_signal_handler not supported

    # Resolve engines
    base_spec = parse_engine_spec(config.base)
    test_spec = parse_engine_spec(config.test)

    # Determine which worktree paths may be created so we can clean them up later
    worktree_paths = _collect_worktree_paths(base_spec, test_spec, config.repo_root)

    try:
        await _run_sprt_inner(config, base_spec, test_spec)
    finally:
        if sigterm_registered:
            loop.remove_signal_handler(signal.SIGTERM)
        if not config.keep_worktrees:
            for wt_path in worktree_paths:
                await cleanup_worktree(wt_path, repo_root=config.repo_root)


async def _run_sprt_inner(config: RunConfig, base_spec: EngineSpec, test_spec: EngineSpec) -> None:
    """Execute the core SPRT loop.

    Extracted from ``run_sprt`` so the outer function can wrap it in a
    ``try``/``finally`` block for worktree cleanup.

    Args:
        config: Run configuration.
        base_spec: Parsed base engine specification.
        test_spec: Parsed test engine specification.

    Raises:
        RunAbortedError: If the run stopped on a fatal error.
    """
    try:
        base_run, base_dir = await resolve_engine_path(base_spec, repo_root=config.repo_root)
        test_run, test_dir = await resolve_engine_path(test_spec, repo_root=config.repo_root)
    except Exception as e:
        _abort(f"Failed to resolve engines: {e}")

    # Load opening book
    opening_pairs: list[OpeningPair] = []
    if config.book_path is not None:
        try:
            fens = load_openings(config.book_path)
            opening_pairs = make_opening_pairs(fens)
        except Exception as e:
            _abort(f"Failed to load opening book: {e}")

    # SPRT tracking
    wins = 0
    losses = 0
    draws = 0
    errors = 0
    games_played = 0
    pair_index = 0

    # Game persistence — save games when output_dir is provided
    output_dir = config.output_dir
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    # Result queue for IPC from worker processes
    result_queue: multiprocessing.Queue[WorkerResult] = multiprocessing.Queue()

    # A game holds a slot from launch until its result is read. Its process
    # then moves to ``finished`` until it has exited and can be reaped. It may
    # still be shutting down when its result arrives, so freeing slots only on
    # process exit, or waiting for the exit, stalls the run (#187).
    running: dict[str, multiprocessing.Process] = {}
    finished: list[multiprocessing.Process] = []
    # Results read while checking for dead workers, handled before waiting again.
    pending: collections.deque[WorkerResult] = collections.deque()
    # Deaths since the last delivered result. A full round of them is fatal.
    consecutive_deaths = 0

    try:
        while True:
            # Launch workers up to concurrency limit
            while len(running) < config.concurrency:
                # Select opening
                start_fen: str | None = None
                swap_colors = False
                if opening_pairs:
                    pair = opening_pairs[pair_index % len(opening_pairs)]
                    start_fen = pair.fen
                    swap_colors = pair.swap_colors
                    pair_index += 1
                else:
                    swap_colors = pair_index % 2 == 1
                    pair_index += 1

                # Determine colour assignment
                if swap_colors:
                    white_run, white_dir = test_run, test_dir
                    black_run, black_dir = base_run, base_dir
                else:
                    white_run, white_dir = base_run, base_dir
                    black_run, black_dir = test_run, test_dir

                white_cmd = _resolve_run_command(white_run, white_dir)
                black_cmd = _resolve_run_command(black_run, black_dir)

                game_id = str(uuid.uuid4())
                game_config = GameConfig(
                    time_control=config.time_control,
                    adjudication=config.adjudication,
                    start_fen=start_fen,
                    time_margin_ms=config.time_margin_ms,
                    move_timeout_ms=config.move_timeout_ms,
                )

                task = WorkerTask(
                    game_id=game_id,
                    white_cmd=white_cmd,
                    black_cmd=black_cmd,
                    game_config=game_config,
                    swap_colors=swap_colors,
                )

                worker = multiprocessing.Process(
                    target=worker_entry,
                    args=(task, result_queue),
                )
                worker.start()
                running[game_id] = worker

            worker_result: WorkerResult | None
            if pending:
                worker_result = pending.popleft()
            else:
                # Offload the blocking queue.get() to a thread so the asyncio
                # event loop stays responsive (allows cancellation). The short
                # timeout bounds how long that thread outlives a cancellation.
                loop = asyncio.get_running_loop()
                try:
                    worker_result = await loop.run_in_executor(
                        None, lambda: result_queue.get(timeout=_QUEUE_TIMEOUT_SECONDS)
                    )
                except queue.Empty:
                    worker_result = None
                except Exception:
                    logger.exception("Unexpected error while reading from result queue")
                    _abort("Fatal error reading result queue; aborting")

            if worker_result is not None:
                delivered_by = running.pop(worker_result.game_id, None)
                if delivered_by is not None:
                    finished.append(delivered_by)
                consecutive_deaths = 0
            finished = _cleanup_workers(finished)

            # A worker that exits without delivering a result has died. A
            # worker flushes its result into the pipe before it can exit (the
            # queue's feeder thread is joined at exit, as long as
            # ``worker_entry`` never calls ``cancel_join_thread`` or
            # ``os._exit``), so read what is waiting before declaring any of
            # them dead. No ``get`` is outstanding here, so nothing else holds
            # the queue's read lock.
            exited = [gid for gid, w in running.items() if not w.is_alive()]
            if exited:
                try:
                    drained = _drain_results(result_queue)
                except Exception:
                    logger.exception("Unexpected error while reading from result queue")
                    _abort("Fatal error reading result queue; aborting")
                if drained:
                    consecutive_deaths = 0
                pending.extend(drained)
                delivered = {r.game_id for r in pending}
                dead = [gid for gid in exited if gid not in delivered]
                for dead_game_id in dead:
                    dead_worker = running.pop(dead_game_id)
                    print(
                        format_error_message(
                            f"Worker for game {dead_game_id} died unexpectedly "
                            f"(pid={dead_worker.pid})"
                        ),
                        flush=True,
                    )
                    errors += 1
                    consecutive_deaths += 1
                    dead_worker.join()
                if dead and consecutive_deaths >= config.concurrency:
                    deaths = "death" if consecutive_deaths == 1 else "deaths"
                    _abort(
                        f"Aborting after {consecutive_deaths} worker {deaths} "
                        "in a row with no result"
                    )

            if worker_result is None:
                continue

            # Handle result
            if worker_result.error is not None:
                print(
                    format_error_message(
                        f"Game {worker_result.game_id} failed: {worker_result.error}"
                    ),
                    flush=True,
                )
                continue

            if worker_result.result is None:
                continue

            games_played += 1

            # Determine result from test engine's perspective
            if worker_result.swap_colors:
                # Test was white
                if worker_result.result == GameResult.WHITE_WIN:
                    wins += 1
                elif worker_result.result == GameResult.BLACK_WIN:
                    losses += 1
                else:
                    draws += 1
            else:
                # Test was black
                if worker_result.result == GameResult.BLACK_WIN:
                    wins += 1
                elif worker_result.result == GameResult.WHITE_WIN:
                    losses += 1
                else:
                    draws += 1

            # Output game result
            print(
                format_game_result_message(
                    game_id=worker_result.game_id,
                    result=worker_result.result,
                    termination=worker_result.termination or "unknown",
                    move_count=worker_result.move_count,
                ),
                flush=True,
            )

            # Persist game if storage is configured
            if output_dir is not None:
                # Determine engine names for white/black based on colour swap
                if worker_result.swap_colors:
                    white_engine = config.test
                    black_engine = config.base
                else:
                    white_engine = config.base
                    black_engine = config.test

                game = Game(
                    id=worker_result.game_id,
                    white_engine=white_engine,
                    black_engine=black_engine,
                    result=worker_result.result,
                    moves=worker_result.moves,
                    created_at=datetime.now(UTC),
                    sprt_test_id=config.test_id,
                    start_fen=worker_result.start_fen,
                    time_control=config.time_control,
                )
                try:
                    _write_game(output_dir, game)
                except Exception:
                    logger.exception("Failed to persist game %s", worker_result.game_id)

            # SPRT check
            sprt_result = sprt_test(
                wins=wins,
                losses=losses,
                draws=draws,
                elo0=config.elo0,
                elo1=config.elo1,
                alpha=config.alpha,
                beta=config.beta,
            )

            # Output progress
            print(
                format_progress_message(
                    wins=wins,
                    losses=losses,
                    draws=draws,
                    llr=sprt_result.llr,
                    lower_bound=sprt_result.lower_bound,
                    upper_bound=sprt_result.upper_bound,
                    games_total=games_played,
                    errors=errors,
                ),
                flush=True,
            )

            # Check stopping condition
            if sprt_result.decision != SPRTDecision.CONTINUE:
                result_str = sprt_result.decision.value
                print(
                    format_complete_message(
                        result=result_str,
                        total_games=games_played,
                        llr=sprt_result.llr,
                    ),
                    flush=True,
                )
                # Terminate remaining workers before breaking
                _terminate_all_workers([*running.values(), *finished])
                running.clear()
                finished.clear()
                break

    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("SPRT run interrupted — shutting down gracefully")
        # Report partial progress so the caller knows how far the test got
        if games_played > 0:
            partial_sprt = sprt_test(
                wins=wins,
                losses=losses,
                draws=draws,
                elo0=config.elo0,
                elo1=config.elo1,
                alpha=config.alpha,
                beta=config.beta,
            )
            print(
                format_progress_message(
                    wins=wins,
                    losses=losses,
                    draws=draws,
                    llr=partial_sprt.llr,
                    lower_bound=partial_sprt.lower_bound,
                    upper_bound=partial_sprt.upper_bound,
                    games_total=games_played,
                    errors=errors,
                ),
                flush=True,
            )
        print(format_interrupted_message(games_played), flush=True)
        raise

    finally:
        # Terminate any workers still alive (no-op if already cleared at
        # normal SPRT convergence).
        _terminate_all_workers([*running.values(), *finished])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        Configured argument parser.
    """
    parser = argparse.ArgumentParser(
        prog="sprt_runner",
        description="Run SPRT tests between chess engines",
    )
    sub = parser.add_subparsers(dest="command")
    run_parser = sub.add_parser("run", help="Run an SPRT test")

    run_parser.add_argument("--base", required=True, help="Base engine (ENGINE[:COMMIT])")
    run_parser.add_argument("--test", required=True, help="Test engine (ENGINE[:COMMIT])")
    run_parser.add_argument(
        "--tc", required=True, help="Time control (e.g. movetime=1000, depth=10)"
    )
    run_parser.add_argument("--elo0", type=float, default=0.0, help="H0 Elo bound")
    run_parser.add_argument("--elo1", type=float, default=5.0, help="H1 Elo bound")
    run_parser.add_argument("--book", type=str, default=None, help="Path to opening book (EPD/PGN)")
    run_parser.add_argument(
        "--concurrency", type=int, default=1, help="Number of concurrent game worker processes"
    )
    run_parser.add_argument("--alpha", type=float, default=0.05, help="Type-I error rate")
    run_parser.add_argument("--beta", type=float, default=0.05, help="Type-II error rate")
    run_parser.add_argument(
        "--adjudicate-win", type=int, default=1000, help="Win adjudication threshold (cp)"
    )
    run_parser.add_argument(
        "--adjudicate-draw", type=int, default=10, help="Draw adjudication threshold (cp)"
    )
    run_parser.add_argument(
        "--syzygy-path", type=str, default=None, help="Path to Syzygy tablebase directory"
    )
    run_parser.add_argument(
        "--keep-worktrees",
        action="store_true",
        default=False,
        help="Preserve git worktrees after test completes (for debugging)",
    )
    run_parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory to write game files (.eval.json + .pgn) into",
    )
    run_parser.add_argument(
        "--test-id",
        type=str,
        default=None,
        help="SPRT test ID stamped into each game record for filtering",
    )
    run_parser.add_argument(
        "--time-margin-ms",
        type=int,
        default=DEFAULT_TIME_MARGIN_MS,
        help="Grace added to the limit of clock and movetime searches (ms)",
    )
    run_parser.add_argument(
        "--move-timeout-ms",
        type=int,
        default=DEFAULT_MOVE_TIMEOUT_MS,
        help="Limit on depth and nodes searches (ms)",
    )

    return parser


def main() -> None:
    """CLI entry point for ``python -m sprt_runner``."""
    # Configure logging to stderr only
    _configure_logging()

    parser = build_parser()
    args = parser.parse_args()

    if args.command != "run":
        parser.print_help()
        sys.exit(1)

    try:
        tc = parse_time_control(args.tc)
    except ValueError as e:
        print(format_error_message(str(e)), flush=True)
        sys.exit(1)

    if args.time_margin_ms < 0:
        message = f"--time-margin-ms must not be negative, got {args.time_margin_ms}"
        print(format_error_message(message), flush=True)
        sys.exit(1)

    if args.move_timeout_ms <= 0:
        message = f"--move-timeout-ms must be positive, got {args.move_timeout_ms}"
        print(format_error_message(message), flush=True)
        sys.exit(1)

    # With no worker slot the coordinator would launch nothing and wait forever.
    if args.concurrency < 1:
        message = f"--concurrency must be at least 1, got {args.concurrency}"
        print(format_error_message(message), flush=True)
        sys.exit(1)

    # Unlike --book/--output-dir, an explicitly-supplied path is validated
    # up front: silently running for hours without the adjudication the user
    # asked for is worse than refusing to start.
    syzygy_path: Path | None = None
    if args.syzygy_path is not None:
        try:
            syzygy_path = validate_syzygy_path(args.syzygy_path)
        except ValueError as e:
            print(format_error_message(str(e)), flush=True)
            sys.exit(1)

    adjudication = AdjudicationConfig(
        win_threshold_cp=args.adjudicate_win,
        draw_threshold_cp=args.adjudicate_draw,
        syzygy_path=syzygy_path,
    )

    run_config = RunConfig(
        base=args.base,
        test=args.test,
        time_control=tc,
        elo0=args.elo0,
        elo1=args.elo1,
        alpha=args.alpha,
        beta=args.beta,
        book_path=Path(args.book) if args.book else None,
        concurrency=args.concurrency,
        adjudication=adjudication,
        keep_worktrees=args.keep_worktrees,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        test_id=args.test_id,
        time_margin_ms=args.time_margin_ms,
        move_timeout_ms=args.move_timeout_ms,
    )

    # Start workers from a forkserver with the workers' module preloaded,
    # unless the caller already chose a start method. A forkserver child starts
    # with default signal handling, unlike a fork of this multi-threaded
    # process. With the preload it also starts about as fast as a fork, which
    # matters when games are short. In 10 ms-per-move runs spawn, macOS's
    # default, played about 30% fewer games a minute.
    # typeshed types this as str, but allow_none=True returns None when no
    # start method has been chosen yet.
    chosen = cast("str | None", multiprocessing.get_start_method(allow_none=True))
    if chosen is None and "forkserver" in multiprocessing.get_all_start_methods():
        multiprocessing.set_start_method("forkserver")
        multiprocessing.set_forkserver_preload([worker_entry.__module__])

    try:
        asyncio.run(run_sprt(run_config))
    except RunAbortedError:
        # The run has already printed why it stopped, as its last line.
        sys.exit(1)
    except KeyboardInterrupt:
        # asyncio.run() re-raises KeyboardInterrupt after cancelling the task.
        sys.exit(130)
    except asyncio.CancelledError:
        # SIGTERM handler cancels the task; asyncio.run() propagates CancelledError.
        sys.exit(1)
