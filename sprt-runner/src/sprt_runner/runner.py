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
    - ``error``: Non-fatal error during a game.
    - ``complete``: Final SPRT result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import multiprocessing
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from shared.storage.models import GameResult
from shared.time_control import (
    TimeControl,
    parse_time_control,
)
from shared.uci_client import UCIClient

from sprt_runner.adjudication import AdjudicationConfig
from sprt_runner.game import GameConfig, play_game
from sprt_runner.openings import OpeningPair, load_openings, make_opening_pairs
from sprt_runner.sprt import SPRTDecision, sprt_test
from sprt_runner.worktree import parse_engine_spec, resolve_engine_path

logger = logging.getLogger(__name__)


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
    resolved_parts = [str(executable), *parts[1:]]
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
    worker_result = asyncio.run(_play_single_game(task))
    result_queue.put(worker_result)


def _cleanup_workers(
    active_workers: list[multiprocessing.Process],
) -> list[multiprocessing.Process]:
    """Join finished workers and return only those still alive.

    Calls ``join(timeout=1)`` on every worker *before* checking
    ``is_alive()``.  This closes the race window where a worker has
    already put its result on the queue but hasn't fully exited yet
    — ``join`` gives it a brief moment to finish, so ``is_alive()``
    returns ``False`` and the slot is freed for a new worker.

    Args:
        active_workers: List of currently tracked worker processes.

    Returns:
        A new list containing only the workers that are still alive.
    """
    still_alive: list[multiprocessing.Process] = []
    for w in active_workers:
        w.join(timeout=1)
        if w.is_alive():
            still_alive.append(w)
    return still_alive


async def run_sprt(config: RunConfig) -> None:
    """Run a complete SPRT test.

    Resolves engine paths, loads opening book, plays games using
    ``multiprocessing`` for game-level parallelism (with ``asyncio``
    inside each worker for UCI I/O), and streams JSON-lines progress
    to stdout. Workers report via ``multiprocessing.Queue``; the
    coordinator aggregates single-threaded.

    Args:
        config: Run configuration.
    """
    # Resolve engines
    base_spec = parse_engine_spec(config.base)
    test_spec = parse_engine_spec(config.test)

    try:
        base_run, base_dir = await resolve_engine_path(base_spec, repo_root=config.repo_root)
        test_run, test_dir = await resolve_engine_path(test_spec, repo_root=config.repo_root)
    except Exception as e:
        print(format_error_message(f"Failed to resolve engines: {e}"), flush=True)
        return

    # Load opening book
    opening_pairs: list[OpeningPair] = []
    if config.book_path is not None:
        try:
            fens = load_openings(config.book_path)
            opening_pairs = make_opening_pairs(fens)
        except Exception as e:
            print(format_error_message(f"Failed to load opening book: {e}"), flush=True)
            return

    # SPRT tracking
    wins = 0
    losses = 0
    draws = 0
    games_played = 0
    pair_index = 0

    # Result queue for IPC from worker processes
    result_queue: multiprocessing.Queue[WorkerResult] = multiprocessing.Queue()

    active_workers: list[multiprocessing.Process] = []

    while True:
        # Launch workers up to concurrency limit
        while len(active_workers) < config.concurrency:
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
            active_workers.append(worker)

        # Wait for a result from any worker (with timeout for crash safety).
        # Offload the blocking queue.get() to a thread so the asyncio event
        # loop stays responsive (allows cancellation, status updates, etc.).
        loop = asyncio.get_running_loop()
        try:
            worker_result = await loop.run_in_executor(None, lambda: result_queue.get(timeout=300))
        except Exception:
            # Queue timeout — check for dead workers
            dead = [w for w in active_workers if not w.is_alive()]
            for w in dead:
                w.join(timeout=1)
            active_workers = [w for w in active_workers if w.is_alive()]
            if not active_workers:
                print(format_error_message("All workers died unexpectedly"), flush=True)
                break
            continue

        # Clean up finished workers (join to free resources)
        active_workers = _cleanup_workers(active_workers)

        # Handle result
        if worker_result.error is not None:
            print(
                format_error_message(f"Game {worker_result.game_id} failed: {worker_result.error}"),
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
            # Terminate remaining workers
            for w in active_workers:
                w.terminate()
                w.join(timeout=5)
            break


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

    return parser


def main() -> None:
    """CLI entry point for ``python -m sprt_runner``."""
    # Configure logging to stderr only
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

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

    adjudication = AdjudicationConfig(
        win_threshold_cp=args.adjudicate_win,
        draw_threshold_cp=args.adjudicate_draw,
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
    )

    asyncio.run(run_sprt(run_config))
