"""Integration tests for the SPRT runner with real engine subprocesses.

Exercises the complete test orchestration loop:
``runner.py`` → ``game.py`` → ``UCIClient`` → engine subprocess.

Uses ``random-engine`` vs itself with very wide SPRT bounds so tests
terminate quickly (2-4 games). Marked with ``@pytest.mark.integration``
so unit-test runs can skip them.

The ``random-engine`` tests skip automatically if its venv is not built. The
real-time clock tests use the scripted engine in ``scripted_uci_engine.py``,
which runs on this venv's interpreter and needs no build.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import shlex
import sys
import time
from pathlib import Path
from typing import cast

import pytest
from shared.storage.models import GameResult
from shared.time_control import (
    DepthTimeControl,
    FixedTimeControl,
    IncrementTimeControl,
    TimeControl,
)
from shared.uci_client import UCIClient
from sprt_runner.adjudication import AdjudicationConfig
from sprt_runner.game import GameConfig, TerminationReason, play_game
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


_SCRIPTED_ENGINE = Path(__file__).with_name("scripted_uci_engine.py")
_NO_ADJUDICATION = AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0)


def _scripted_engine_client(*engine_args: str) -> UCIClient:
    """Build a client for the scripted engine with a 50 ms default timeout.

    That is far below the scripted search time, so a ``go`` falling back to the
    default would forfeit the first move.
    """
    command = shlex.join([sys.executable, str(_SCRIPTED_ENGINE), *engine_args])
    return UCIClient(command, default_timeout=0.05)


def _logged_clock(go_line: str, name: str) -> int:
    """Read one clock value from a logged ``go`` line."""
    tokens = go_line.split()
    return int(tokens[tokens.index(name) + 1])


class TestRealEngineClock:
    """The whole path over real pipes, in real time."""

    @pytest.mark.asyncio
    async def test_real_engine_sees_falling_clocks_and_flags_on_expected_move(
        self, tmp_path: Path
    ) -> None:
        """500 ms per move from 1200 ms: 1200, 700, 200, then 500 > 200 + 100.

        Flagging a move early would need over 300 ms of scheduling jitter, and
        the upper bounds hold because a sleep never returns early.
        """
        white_log = tmp_path / "white_go.log"
        black_log = tmp_path / "black_go.log"
        white = _scripted_engine_client("--sleep-ms", "500", "--go-log", str(white_log))
        black = _scripted_engine_client("--go-log", str(black_log))
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=1200, btime_ms=1200),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
        )

        try:
            for engine in (white, black):
                await engine.start()
                await engine.uci(timeout=30.0)
                await engine.isready(timeout=30.0)
            outcome = await play_game(white=white, black=black, config=config)
        finally:
            await white.quit()
            await black.quit()

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.BLACK_WIN
        assert len(outcome.moves) == 4

        white_clocks = [_logged_clock(line, "wtime") for line in white_log.read_text().splitlines()]
        assert len(white_clocks) == 3
        assert white_clocks[0] == 1200
        assert 400 < white_clocks[1] <= 700
        assert 0 <= white_clocks[2] <= 200

        black_lines = black_log.read_text().splitlines()
        assert len(black_lines) == 2
        assert 400 < _logged_clock(black_lines[0], "wtime") <= 700
        assert 0 <= _logged_clock(black_lines[1], "wtime") <= 200
        assert _logged_clock(black_lines[0], "btime") == 1200

        white_after_first = outcome.moves[0].clock_white_ms
        assert white_after_first is not None
        assert 400 < white_after_first <= 700

    @pytest.mark.parametrize(
        "tc",
        [
            FixedTimeControl(movetime_ms=100),
            IncrementTimeControl(wtime_ms=100, btime_ms=100),
            DepthTimeControl(depth=1),
        ],
        ids=["movetime", "clock", "depth"],
    )
    @pytest.mark.asyncio
    async def test_hung_engine_flags_at_the_limit(self, tc: TimeControl) -> None:
        """An engine that never replies loses when the client's deadline fires.

        ``go`` never returns here, so the post-go check cannot catch this: only
        the deadline can. The guard turns a dead deadline into a failure rather
        than a hang. Every limit below works out to 200 ms.
        """
        white = _scripted_engine_client("--sleep-ms", "3600000")
        black = _scripted_engine_client()
        config = GameConfig(
            time_control=tc,
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
            move_timeout_ms=200,
        )

        try:
            for engine in (white, black):
                await engine.start()
                await engine.uci(timeout=30.0)
                await engine.isready(timeout=30.0)
            start_s = time.monotonic()
            outcome = await asyncio.wait_for(
                play_game(white=white, black=black, config=config), timeout=5.0
            )
            elapsed_s = time.monotonic() - start_s
        finally:
            await white.quit()
            await black.quit()

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.BLACK_WIN
        assert outcome.moves == []
        assert 0.2 <= elapsed_s < 2.0


class TestRealTimeMoveLimits:
    """Real slow moves against real clients with the default 10 s ``default_timeout``."""

    @staticmethod
    async def _play_with_slow_first_move(tc: TimeControl, first_sleep_ms: int) -> None:
        """Play two full moves where only White's first search is slow, and check it survived."""
        slow = shlex.join(
            [sys.executable, str(_SCRIPTED_ENGINE), "--first-sleep-ms", str(first_sleep_ms)]
        )
        fast = shlex.join([sys.executable, str(_SCRIPTED_ENGINE)])
        white = UCIClient(slow)
        black = UCIClient(fast)
        config = GameConfig(time_control=tc, adjudication=_NO_ADJUDICATION, max_moves=2)

        try:
            for engine in (white, black):
                await engine.start()
                await engine.uci()
                await engine.isready()
            outcome = await play_game(white=white, black=black, config=config)
        finally:
            await white.quit()
            await black.quit()

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert outcome.result == GameResult.DRAW
        assert len(outcome.moves) == 4

    @pytest.mark.asyncio
    async def test_real_11s_move_survives_movetime_15000(self) -> None:
        await self._play_with_slow_first_move(FixedTimeControl(movetime_ms=15_000), 11_000)

    @pytest.mark.asyncio
    async def test_real_6s_move_survives_depth_1(self) -> None:
        await self._play_with_slow_first_move(DepthTimeControl(depth=1), 6_000)


@pytest.fixture
def scripted_repo_root(tmp_path: Path) -> Path:
    """Create a temporary repo root registering a slow and a fast scripted engine."""
    engine_dir = tmp_path / "engines" / "scripted"
    engine_dir.mkdir(parents=True)

    def _entry(engine_id: str, sleep_ms: int) -> dict[str, object]:
        return {
            "id": engine_id,
            "name": engine_id,
            "dir": "engines/scripted",
            "build": None,
            "run": f"{sys.executable} {_SCRIPTED_ENGINE} --sleep-ms {sleep_ms}",
        }

    (tmp_path / "engines.json").write_text(
        json.dumps([_entry("slow-engine", 150), _entry("fast-engine", 0)])
    )
    return tmp_path


def _clock_series(moves: list[dict[str, object]], field: str) -> list[int]:
    """Read one clock field from every sidecar move, requiring it to be populated."""
    series: list[int] = []
    for move in moves:
        value = move[field]
        assert isinstance(value, int), f"{field} is not populated: {value!r}"
        series.append(value)
    return series


class TestClockEndToEnd:
    """``run_sprt`` through spawned workers, real engines and ``.eval.json`` sidecars."""

    @pytest.mark.asyncio
    async def test_slow_engine_flags_and_sidecars_record_falling_clocks(
        self,
        scripted_repo_root: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The slow engine loses on time in every game, so three wins decide the test.

        The outer timeout fails the test instead of hanging it if clocks stop
        being enforced: unflagged games are all draws, which never reach a
        decision. The short queue timeout lets the reader thread exit promptly
        after that cancellation.
        """
        monkeypatch.setattr("sprt_runner.runner._QUEUE_TIMEOUT_SECONDS", 5)
        output_dir = tmp_path / "games"
        config = RunConfig(
            base="slow-engine",
            test="fast-engine",
            time_control=IncrementTimeControl(wtime_ms=1000, btime_ms=1000),
            elo0=-200.0,
            elo1=200.0,
            concurrency=1,
            adjudication=_NO_ADJUDICATION,
            repo_root=scripted_repo_root,
            output_dir=output_dir,
            time_margin_ms=100,
        )

        await asyncio.wait_for(run_sprt(config), timeout=120)

        messages = _parse_json_lines(capsys.readouterr().out)
        assert [m for m in messages if m["type"] == "error"] == []
        game_results = [m for m in messages if m["type"] == "game_result"]
        assert len(game_results) >= 1
        assert {m["termination"] for m in game_results} == {"timeout"}
        assert [m["result"] for m in messages if m["type"] == "complete"] == ["H1"]

        sidecars = sorted(output_dir.glob("*.eval.json"))
        assert len(sidecars) == len(game_results)
        for sidecar in sidecars:
            game = cast(dict[str, object], json.loads(sidecar.read_text()))
            moves = cast(list[dict[str, object]], game["moves"])
            white_clocks = _clock_series(moves, "clock_white_ms")
            black_clocks = _clock_series(moves, "clock_black_ms")
            assert white_clocks == sorted(white_clocks, reverse=True)
            assert black_clocks == sorted(black_clocks, reverse=True)
            assert max(white_clocks) <= 1000
            assert max(black_clocks) <= 1000

            # The slow engine's clock drops by at least its sleep on each of its moves.
            is_slow_white = game["white_engine"] == "slow-engine"
            slow_clocks = white_clocks[::2] if is_slow_white else black_clocks[1::2]
            assert len(slow_clocks) >= 2
            for before, after in itertools.pairwise(slow_clocks):
                assert after == 0 or before - after >= 150
