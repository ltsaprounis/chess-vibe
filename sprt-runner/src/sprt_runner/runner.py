"""CLI entry point and SPRT test orchestrator.

Provides the CLI interface for running SPRT tests between engine pairs.
Outputs JSON-lines to stdout for consumption by the backend. Errors go
to stderr only.

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
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from shared.storage.models import GameResult
from shared.time_control import (
    DepthTimeControl,
    FixedTimeControl,
    IncrementTimeControl,
    NodesTimeControl,
    TimeControl,
)
from shared.uci_client import UCIClient

from sprt_runner.adjudication import AdjudicationConfig
from sprt_runner.game import GameConfig, play_game
from sprt_runner.openings import OpeningPair, load_epd_openings, make_opening_pairs
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
        book_path: Path to the opening book file (EPD).
        concurrency: Number of concurrent games.
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
# Time control parsing
# ---------------------------------------------------------------------------


def parse_time_control(tc_str: str) -> TimeControl:
    """Parse a time control specification string.

    Supported formats:
        - ``movetime=1000`` — Fixed time per move in ms.
        - ``depth=10`` — Search to fixed depth.
        - ``nodes=50000`` — Search fixed number of nodes.
        - ``wtime=60000,btime=60000,winc=1000,binc=1000`` — Increment.

    Args:
        tc_str: Time control string.

    Returns:
        Parsed TimeControl.

    Raises:
        ValueError: If the format is unrecognised.
    """
    parts = dict(part.split("=", 1) for part in tc_str.split(","))

    if "movetime" in parts:
        return FixedTimeControl(movetime_ms=int(parts["movetime"]))
    if "depth" in parts:
        return DepthTimeControl(depth=int(parts["depth"]))
    if "nodes" in parts:
        return NodesTimeControl(nodes=int(parts["nodes"]))
    if "wtime" in parts and "btime" in parts:
        return IncrementTimeControl(
            wtime_ms=int(parts["wtime"]),
            btime_ms=int(parts["btime"]),
            winc_ms=int(parts.get("winc", "0")),
            binc_ms=int(parts.get("binc", "0")),
        )

    raise ValueError(f"Unknown time control format: {tc_str!r}")


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


async def run_sprt(config: RunConfig) -> None:
    """Run a complete SPRT test.

    Resolves engine paths, loads opening book, plays games in pairs,
    and streams JSON-lines progress to stdout.

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
            fens = load_epd_openings(config.book_path)
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

    while True:
        # Select opening
        start_fen: str | None = None
        swap_colors = False
        if opening_pairs:
            pair = opening_pairs[pair_index % len(opening_pairs)]
            start_fen = pair.fen
            swap_colors = pair.swap_colors
            pair_index += 1

        # Determine colour assignment
        if swap_colors:
            white_run, white_dir = test_run, test_dir
            black_run, black_dir = base_run, base_dir
        else:
            white_run, white_dir = base_run, base_dir
            black_run, black_dir = test_run, test_dir

        # Create engine clients using the run command from the engine directory
        # The run command is relative to the engine dir (e.g. ".venv/bin/python -m engine")
        # We construct the full command by splitting and resolving the first token
        white_cmd = _resolve_run_command(white_run, white_dir)
        black_cmd = _resolve_run_command(black_run, black_dir)

        white_engine = UCIClient(white_cmd)
        black_engine = UCIClient(black_cmd)

        game_id = str(uuid.uuid4())
        game_config = GameConfig(
            time_control=config.time_control,
            adjudication=config.adjudication,
            start_fen=start_fen,
        )

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
                config=game_config,
            )
        except Exception as e:
            print(format_error_message(f"Game {game_id} failed: {e}"), flush=True)
            logger.exception("Game failed")
            continue
        finally:
            await white_engine.quit()
            await black_engine.quit()

        games_played += 1

        # Determine result from test engine's perspective
        if swap_colors:
            # Test was white
            if outcome.result == GameResult.WHITE_WIN:
                wins += 1
            elif outcome.result == GameResult.BLACK_WIN:
                losses += 1
            else:
                draws += 1
        else:
            # Test was black
            if outcome.result == GameResult.BLACK_WIN:
                wins += 1
            elif outcome.result == GameResult.WHITE_WIN:
                losses += 1
            else:
                draws += 1

        # Output game result
        print(
            format_game_result_message(
                game_id=game_id,
                result=outcome.result,
                termination=outcome.termination.value,
                move_count=len(outcome.moves),
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
    run_parser.add_argument("--book", type=str, default=None, help="Path to opening book (EPD)")
    run_parser.add_argument("--concurrency", type=int, default=1, help="Number of concurrent games")
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
