"""Integration tests for the SPRT runner with real engine subprocesses.

Exercises the complete test orchestration loop:
``runner.py`` → ``game.py`` → ``UCIClient`` → engine subprocess.

Uses ``random-engine`` vs itself with very wide SPRT bounds so tests
terminate quickly (2-4 games). Marked with ``@pytest.mark.integration``
so unit-test runs can skip them.

Skips automatically if the ``random-engine`` venv is not built.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shared.time_control import FixedTimeControl
from sprt_runner.adjudication import AdjudicationConfig
from sprt_runner.runner import RunConfig, run_sprt

pytestmark = pytest.mark.integration

# Repo root — two levels up from this test file.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_json_lines(raw: str) -> list[dict[str, object]]:
    """Parse newline-delimited JSON output into a list of dicts."""
    messages: list[dict[str, object]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped:
            messages.append(json.loads(stripped))
    return messages


@pytest.fixture
def sprt_repo_root(tmp_path: Path) -> Path:
    """Create a temporary repo root with engines.json pointing at the pre-built engine.

    The real ``engines.json`` has a ``build`` command that fails if the
    venv already exists.  This fixture creates a lightweight directory
    structure with ``build: null`` and symlinks the real engine directory
    so ``resolve_engine_path`` can locate the engine without rebuilding.
    """
    # Write engines.json without a build step
    engines_json = tmp_path / "engines.json"
    engines_json.write_text(
        json.dumps(
            [
                {
                    "id": "random-engine",
                    "name": "Random Engine",
                    "dir": "engines/random-engine",
                    "build": None,
                    "run": ".venv/bin/python -m random_engine",
                }
            ]
        )
    )

    # Symlink the engine directory so the run command resolves correctly
    engines_dir = tmp_path / "engines"
    engines_dir.mkdir()
    (engines_dir / "random-engine").symlink_to(
        _REPO_ROOT / "engines" / "random-engine",
        target_is_directory=True,
    )

    return tmp_path


class TestSPRTRunnerIntegration:
    """End-to-end SPRT runner tests with real engine subprocesses."""

    @pytest.mark.asyncio
    async def test_abbreviated_sprt_completes(
        self,
        random_engine_command: str,  # triggers skip if venv absent
        sprt_repo_root: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Run a short SPRT test and verify it reaches a statistical decision.

        Uses very wide bounds (elo0=-200, elo1=200) so the test converges
        in only 2-4 games. Verifies:
        - Real engine subprocesses are spawned and UCI handshake completes
        - Games are played to completion with legal moves
        - SPRT statistics accumulate correctly (wins + draws + losses = total)
        - Test terminates with H0 or H1 decision
        """
        config = RunConfig(
            base="random-engine",
            test="random-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=-200.0,
            elo1=200.0,
            concurrency=1,
            adjudication=AdjudicationConfig(
                # Disable adjudication — let games end naturally
                win_consecutive_moves=0,
                draw_consecutive_moves=0,
            ),
            repo_root=sprt_repo_root,
        )

        await run_sprt(config)

        captured = capsys.readouterr()
        messages = _parse_json_lines(captured.out)

        # --- Must have at least one game_result and one complete message ---
        game_results = [m for m in messages if m["type"] == "game_result"]
        progress_msgs = [m for m in messages if m["type"] == "progress"]
        complete_msgs = [m for m in messages if m["type"] == "complete"]
        error_msgs = [m for m in messages if m["type"] == "error"]

        assert len(error_msgs) == 0, f"Unexpected errors: {error_msgs}"
        assert len(game_results) >= 1, "Expected at least one game result"
        assert len(complete_msgs) == 1, "Expected exactly one complete message"

        # --- Verify game results have correct structure ---
        for gr in game_results:
            assert gr["result"] in ("1-0", "0-1", "1/2-1/2")
            assert isinstance(gr["move_count"], int)
            assert gr["move_count"] > 0
            assert gr["termination"] in (
                "checkmate",
                "stalemate",
                "draw_rule",
                "adjudication",
                "timeout",
                "max_moves",
            )

        # --- Verify SPRT statistics accumulate correctly ---
        final_progress = progress_msgs[-1]
        wins = final_progress["wins"]
        losses = final_progress["losses"]
        draws = final_progress["draws"]
        total = final_progress["games_total"]
        assert isinstance(wins, int)
        assert isinstance(losses, int)
        assert isinstance(draws, int)
        assert isinstance(total, int)
        assert wins + losses + draws == total
        assert total == len(game_results)
        assert total >= 1

        # --- Verify SPRT decision ---
        complete = complete_msgs[0]
        assert complete["result"] in ("H0", "H1")
        assert isinstance(complete["total_games"], int)
        assert complete["total_games"] == total
        assert isinstance(complete["llr"], (int, float))

    @pytest.mark.asyncio
    async def test_sprt_with_opening_book(
        self,
        random_engine_command: str,  # triggers skip if venv absent
        sprt_repo_root: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Run SPRT with a small EPD opening book and verify it works.

        Verifies that opening book loading and position setup work correctly
        with real engine subprocesses.
        """
        # Create a small EPD file with 2 positions
        book_path = tmp_path / "openings.epd"
        book_path.write_text(
            # Sicilian Defense and French Defense
            "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2\n"
            "rnbqkbnr/pppp1ppp/4p3/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2\n"
        )

        config = RunConfig(
            base="random-engine",
            test="random-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=-200.0,
            elo1=200.0,
            concurrency=1,
            book_path=book_path,
            adjudication=AdjudicationConfig(
                win_consecutive_moves=0,
                draw_consecutive_moves=0,
            ),
            repo_root=sprt_repo_root,
        )

        await run_sprt(config)

        captured = capsys.readouterr()
        messages = _parse_json_lines(captured.out)

        game_results = [m for m in messages if m["type"] == "game_result"]
        complete_msgs = [m for m in messages if m["type"] == "complete"]
        error_msgs = [m for m in messages if m["type"] == "error"]

        assert len(error_msgs) == 0, f"Unexpected errors: {error_msgs}"
        assert len(game_results) >= 1
        assert len(complete_msgs) == 1
        assert complete_msgs[0]["result"] in ("H0", "H1")
