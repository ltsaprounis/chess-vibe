"""Integration tests for SPRT runner with real engine subprocesses.

These tests exercise the complete orchestration path with real engine
subprocesses: runner.py → game.py → UCIClient → engine subprocess.

They are marked with ``@pytest.mark.integration`` so they can be run
or skipped separately.  Tests skip gracefully when the random-engine
venv has not been built.
"""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import chess
import pytest
from shared.time_control import DepthTimeControl
from sprt_runner.adjudication import AdjudicationConfig
from sprt_runner.game import GameConfig
from sprt_runner.runner import (
    WorkerResult,
    WorkerTask,
    _play_single_game,  # pyright: ignore[reportPrivateUsage]
    _resolve_run_command,  # pyright: ignore[reportPrivateUsage]
    worker_entry,
)
from sprt_runner.sprt import SPRTDecision, sprt_test
from sprt_runner.worktree import parse_engine_spec, resolve_engine_path

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]

# KQ vs K — produces short games with random moves.
_KQ_VS_K_FEN = "k7/8/1K6/8/8/8/8/7Q w - - 0 1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sprt_repo_root(tmp_path: Path) -> Path:
    """Create a temporary repo root with engines.json (no build step).

    The real random-engine directory is symlinked so that
    ``resolve_engine_path`` finds the pre-built venv without
    attempting to rebuild it.
    """
    real_engine_dir = _REPO_ROOT / "engines" / "random-engine"
    engines_dir = tmp_path / "engines"
    engines_dir.mkdir()
    os.symlink(real_engine_dir, engines_dir / "random-engine")

    registry = [
        {
            "id": "random-engine",
            "name": "Random Engine",
            "dir": "engines/random-engine",
            "build": None,
            "run": ".venv/bin/python -m random_engine",
        }
    ]
    (tmp_path / "engines.json").write_text(json.dumps(registry))
    return tmp_path


@pytest.fixture
def engine_command(random_engine_command: str) -> str:
    """Return the resolved engine command string.

    Triggers skip if the random-engine venv is not built.
    """
    return random_engine_command


def _make_task(
    engine_cmd: str,
    *,
    start_fen: str | None = None,
    swap_colors: bool = False,
    game_id: str = "integration-test",
) -> WorkerTask:
    """Create a WorkerTask with sensible defaults for integration tests."""
    return WorkerTask(
        game_id=game_id,
        white_cmd=engine_cmd,
        black_cmd=engine_cmd,
        game_config=GameConfig(
            time_control=DepthTimeControl(depth=1),
            adjudication=AdjudicationConfig(draw_consecutive_moves=0),
            start_fen=start_fen,
        ),
        swap_colors=swap_colors,
    )


# ---------------------------------------------------------------------------
# Engine resolution
# ---------------------------------------------------------------------------


class TestEngineResolution:
    """Verify engine path resolution through worktree module."""

    @pytest.mark.asyncio
    async def test_resolve_engine_path(
        self,
        engine_command: str,
        sprt_repo_root: Path,
    ) -> None:
        """Resolve random-engine via engines.json and verify executable exists."""
        spec = parse_engine_spec("random-engine")
        run_cmd, engine_dir = await resolve_engine_path(spec, repo_root=sprt_repo_root)

        resolved = _resolve_run_command(run_cmd, engine_dir)
        parts = resolved.split()
        assert Path(parts[0]).exists(), f"Executable not found: {parts[0]}"


# ---------------------------------------------------------------------------
# Single game — exercises game.py → UCIClient → engine subprocess
# ---------------------------------------------------------------------------


class TestSingleGameIntegration:
    """Play single games using _play_single_game with real engines."""

    @pytest.mark.asyncio
    async def test_game_plays_to_completion(self, engine_command: str) -> None:
        """A complete game is played with legal moves and a valid result."""
        task = _make_task(engine_command, start_fen=_KQ_VS_K_FEN)
        result = await _play_single_game(task)

        assert result.error is None
        assert result.result is not None
        assert result.result.value in ("1-0", "0-1", "1/2-1/2")
        assert result.move_count > 0
        assert result.termination is not None

    @pytest.mark.asyncio
    async def test_game_with_standard_opening(self, engine_command: str) -> None:
        """Game from standard starting position completes without error."""
        task = _make_task(engine_command)
        result = await _play_single_game(task)

        assert result.error is None
        assert result.result is not None
        assert result.move_count > 0

    @pytest.mark.asyncio
    async def test_game_with_custom_fen(self, engine_command: str) -> None:
        """Game from a custom FEN position plays correctly."""
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        task = _make_task(engine_command, start_fen=fen)
        result = await _play_single_game(task)

        assert result.error is None
        assert result.result is not None
        assert result.move_count > 0

    @pytest.mark.asyncio
    async def test_uci_handshake_via_game(self, engine_command: str) -> None:
        """Both engines complete UCI handshake (verified by game succeeding)."""
        task = _make_task(engine_command, start_fen=_KQ_VS_K_FEN)
        result = await _play_single_game(task)

        # If UCI handshake failed, the game would error out
        assert result.error is None
        assert result.result is not None


# ---------------------------------------------------------------------------
# Multiprocessing worker — exercises runner.py → game.py → UCIClient
# ---------------------------------------------------------------------------


class TestWorkerIntegration:
    """Verify worker_entry plays a game via multiprocessing."""

    def test_worker_returns_result_via_queue(self, engine_command: str) -> None:
        """worker_entry plays a game and sends the result via Queue."""
        result_queue: multiprocessing.Queue[WorkerResult] = multiprocessing.Queue()
        task = _make_task(engine_command, start_fen=_KQ_VS_K_FEN)

        worker = multiprocessing.Process(
            target=worker_entry,
            args=(task, result_queue),
        )
        worker.start()
        try:
            result: WorkerResult = result_queue.get(timeout=30)
            assert result.error is None
            assert result.result is not None
            assert result.result.value in ("1-0", "0-1", "1/2-1/2")
            assert result.move_count > 0
            assert result.game_id == "integration-test"
        finally:
            worker.join(timeout=5)


# ---------------------------------------------------------------------------
# SPRT statistics — verifies accumulation and decision logic with real games
# ---------------------------------------------------------------------------


class TestSPRTStatisticsIntegration:
    """Play multiple games and verify SPRT statistics accumulate correctly."""

    @pytest.mark.asyncio
    async def test_sprt_terminates_with_decision(self, engine_command: str) -> None:
        """Play games until SPRT reaches H0 or H1 with wide bounds.

        Uses very wide Elo bounds (elo0=-500, elo1=500) so the test
        terminates quickly once a non-draw result occurs.
        """
        wins = losses = draws = 0
        max_games = 20  # safety cap

        for i in range(max_games):
            task = _make_task(
                engine_command,
                start_fen=_KQ_VS_K_FEN,
                swap_colors=(i % 2 == 1),
                game_id=f"sprt-{i}",
            )
            result = await _play_single_game(task)

            assert result.error is None
            assert result.result is not None

            # Accumulate from test engine perspective (test == base here)
            if result.swap_colors:
                if result.result.value == "1-0":
                    wins += 1
                elif result.result.value == "0-1":
                    losses += 1
                else:
                    draws += 1
            else:
                if result.result.value == "0-1":
                    wins += 1
                elif result.result.value == "1-0":
                    losses += 1
                else:
                    draws += 1

            total = wins + losses + draws
            assert total == i + 1, "wins + losses + draws must equal total games"

            sprt_result = sprt_test(
                wins=wins,
                losses=losses,
                draws=draws,
                elo0=-500.0,
                elo1=500.0,
            )

            if sprt_result.decision != SPRTDecision.CONTINUE:
                assert sprt_result.decision in (SPRTDecision.H0, SPRTDecision.H1)
                return

        # If we reach here, SPRT didn't converge in max_games
        pytest.fail(f"SPRT did not reach a decision in {max_games} games")


# ---------------------------------------------------------------------------
# Opening book — verifies EPD loading and game play from book positions
# ---------------------------------------------------------------------------


class TestOpeningBookIntegration:
    """Verify opening book positions are used in games."""

    @pytest.mark.asyncio
    async def test_game_from_epd_opening(self, engine_command: str) -> None:
        """Play a game from an EPD opening position."""
        # e4 opening
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        task = _make_task(engine_command, start_fen=fen)
        result = await _play_single_game(task)

        assert result.error is None
        assert result.result is not None
        assert result.move_count > 0

    @pytest.mark.asyncio
    async def test_opening_book_loading(self, tmp_path: Path, engine_command: str) -> None:
        """Verify EPD file loading produces valid FENs that engines can play."""
        epd_file = tmp_path / "openings.epd"
        epd_file.write_text(
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1\n"
            "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1\n"
        )

        from sprt_runner.openings import load_openings, make_opening_pairs

        fens = load_openings(epd_file)
        assert len(fens) == 2

        pairs = make_opening_pairs(fens)
        assert len(pairs) == 4  # 2 FENs x 2 colour assignments

        # Verify each FEN is a valid chess position
        for pair in pairs:
            board = chess.Board(pair.fen)
            assert board.is_valid()

        # Play a game from the first position
        task = _make_task(engine_command, start_fen=pairs[0].fen)
        result = await _play_single_game(task)
        assert result.error is None
