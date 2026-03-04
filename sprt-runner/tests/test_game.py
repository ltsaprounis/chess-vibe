"""Tests for single game play between two UCI engines."""

from __future__ import annotations

from collections.abc import Sequence
from unittest.mock import AsyncMock, MagicMock

import pytest
from shared.storage.models import GameResult
from shared.time_control import (
    DepthTimeControl,
    FixedTimeControl,
    IncrementTimeControl,
    NodesTimeControl,
)
from shared.uci_client import BestMove, UCIInfo, UCIScore
from sprt_runner.adjudication import AdjudicationConfig
from sprt_runner.game import (
    GameConfig,
    TerminationReason,
    _watchdog_timeout_ms,
    play_game,
)


def _mock_engine(moves: list[str], scores: Sequence[UCIScore | None] | None = None) -> MagicMock:
    """Create a mock UCI client with scripted responses.

    Args:
        moves: List of UCI moves to play.
        scores: Optional list of scores per move.
    """
    engine = MagicMock()
    engine.start = AsyncMock()
    engine.quit = AsyncMock()
    engine.uci = AsyncMock(return_value=[])
    engine.isready = AsyncMock()
    engine.position = AsyncMock()
    engine.stop = AsyncMock()
    engine.is_running = True

    resolved_scores: list[UCIScore | None] = (
        list(scores) if scores is not None else [UCIScore(cp=0)] * len(moves)
    )

    go_results: list[tuple[BestMove, list[UCIInfo]]] = []
    for i, move in enumerate(moves):
        score = resolved_scores[i] if i < len(resolved_scores) else UCIScore(cp=0)
        info = UCIInfo(depth=10, score=score, pv=[move])
        go_results.append((BestMove(move=move), [info]))

    engine.go = AsyncMock(side_effect=go_results)
    return engine


class TestGameConfig:
    """Tests for GameConfig dataclass."""

    def test_defaults(self) -> None:
        tc = DepthTimeControl(depth=5)
        config = GameConfig(time_control=tc)
        assert config.time_control == tc
        assert config.adjudication is not None
        assert config.start_fen is None

    def test_custom_config(self) -> None:
        tc = FixedTimeControl(movetime_ms=100)
        adj = AdjudicationConfig(win_threshold_cp=500)
        config = GameConfig(time_control=tc, adjudication=adj, start_fen="custom fen")
        assert config.time_control == tc
        assert config.adjudication.win_threshold_cp == 500
        assert config.start_fen == "custom fen"


class TestPlayGame:
    """Tests for playing a single game between two engines."""

    @pytest.mark.asyncio
    async def test_basic_scholars_mate(self) -> None:
        """Test a game that ends in checkmate (scholar's mate)."""
        # Scholar's mate: 1.e4 e5 2.Qh5 Nc6 3.Bc4 Nf6 4.Qxf7#
        white_moves = ["e2e4", "d1h5", "f1c4", "h5f7"]
        black_moves = ["e7e5", "b8c6", "g8f6"]

        white_engine = _mock_engine(white_moves)
        black_engine = _mock_engine(black_moves)

        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0),
        )

        outcome = await play_game(
            white=white_engine,
            black=black_engine,
            config=config,
        )

        assert outcome.result == GameResult.WHITE_WIN
        assert outcome.termination == TerminationReason.CHECKMATE
        assert len(outcome.moves) == 7

    @pytest.mark.asyncio
    async def test_draw_adjudication(self) -> None:
        """Test a game that ends via draw adjudication."""
        # Play enough moves with near-zero evals
        n_moves = 10
        white_moves = [
            "e2e4",
            "d2d4",
            "g1f3",
            "f1e2",
            "e1g1",
            "b1c3",
            "c1e3",
            "d1d2",
            "a1d1",
            "f3e1",
        ]
        black_moves = [
            "e7e5",
            "d7d5",
            "g8f6",
            "f8e7",
            "e8g8",
            "b8c6",
            "c8e6",
            "d8d7",
            "a8d8",
            "f6e8",
        ]

        zero_scores = [UCIScore(cp=0)] * n_moves

        white_engine = _mock_engine(white_moves, zero_scores)
        black_engine = _mock_engine(black_moves, zero_scores)

        config = GameConfig(
            time_control=DepthTimeControl(depth=1),
            adjudication=AdjudicationConfig(
                draw_threshold_cp=10,
                draw_consecutive_moves=3,
                draw_min_move=1,
                win_consecutive_moves=0,
            ),
        )

        outcome = await play_game(
            white=white_engine,
            black=black_engine,
            config=config,
        )

        assert outcome.result == GameResult.DRAW
        assert outcome.termination == TerminationReason.ADJUDICATION

    @pytest.mark.asyncio
    async def test_custom_start_fen(self) -> None:
        """Test a game starting from a custom FEN."""
        # FEN with imminent checkmate for white
        # KQkr position where white can checkmate in 1
        fen = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2"
        # Black plays Qxh2 which is... actually let's use fool's mate FEN
        # After 1.f3 e5, it's white's turn. Black plays Qh4# next.
        # 1. f3 e5 2. g4 Qh4# - so after 1.f3 e5 2.g4:
        fen = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2"

        black_moves = ["d8h4"]  # Qh4#
        black_engine = _mock_engine(black_moves)
        white_engine = _mock_engine([])  # Won't get to play

        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0),
            start_fen=fen,
        )

        outcome = await play_game(
            white=white_engine,
            black=black_engine,
            config=config,
        )

        assert outcome.result == GameResult.BLACK_WIN
        assert outcome.termination == TerminationReason.CHECKMATE
        assert outcome.start_fen == fen

    @pytest.mark.asyncio
    async def test_outcome_has_moves(self) -> None:
        """Test that game outcome contains move records."""
        white_moves = ["e2e4", "d1h5", "f1c4", "h5f7"]
        black_moves = ["e7e5", "b8c6", "g8f6"]

        white_engine = _mock_engine(white_moves)
        black_engine = _mock_engine(black_moves)

        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0),
        )

        outcome = await play_game(
            white=white_engine,
            black=black_engine,
            config=config,
        )

        assert len(outcome.moves) > 0
        # First move should be e2e4
        assert outcome.moves[0].uci == "e2e4"

    @pytest.mark.asyncio
    async def test_max_moves_exceeded(self) -> None:
        """Test that game ends when max moves limit is reached."""
        # Use pawn pushes that don't repeat (no threefold repetition)
        white_moves = ["a2a3", "b2b3", "c2c3", "d2d3", "e2e3", "f2f3"]
        black_moves = ["a7a6", "b7b6", "c7c6", "d7d6", "e7e6", "f7f6"]

        white_engine = _mock_engine(white_moves)
        black_engine = _mock_engine(black_moves)

        config = GameConfig(
            time_control=DepthTimeControl(depth=1),
            adjudication=AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0),
            max_moves=3,
        )

        outcome = await play_game(
            white=white_engine,
            black=black_engine,
            config=config,
        )

        assert outcome.result == GameResult.DRAW
        assert outcome.termination == TerminationReason.MAX_MOVES

    @pytest.mark.asyncio
    async def test_move_watchdog_timeout(self) -> None:
        """Test that watchdog timeout triggers when engine takes too long."""
        import asyncio

        white_engine = MagicMock()
        white_engine.start = AsyncMock()
        white_engine.quit = AsyncMock()
        white_engine.uci = AsyncMock(return_value=[])
        white_engine.isready = AsyncMock()
        white_engine.position = AsyncMock()
        white_engine.stop = AsyncMock()
        white_engine.is_running = True

        # Engine.go() hangs forever
        async def slow_go(_tc: object) -> tuple[BestMove, list[UCIInfo]]:
            await asyncio.sleep(10)
            return BestMove(move="e2e4"), []

        white_engine.go = AsyncMock(side_effect=slow_go)

        black_engine = _mock_engine(["e7e5"])

        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0),
            move_overhead_ms=50,  # 50ms timeout - engine will definitely exceed this
        )

        outcome = await play_game(
            white=white_engine,
            black=black_engine,
            config=config,
        )

        assert outcome.result == GameResult.BLACK_WIN
        assert outcome.termination == TerminationReason.TIMEOUT

    @pytest.mark.asyncio
    async def test_move_watchdog_disabled(self) -> None:
        """Test that game works with watchdog disabled."""
        white_moves = ["e2e4", "d1h5", "f1c4", "h5f7"]
        black_moves = ["e7e5", "b8c6", "g8f6"]

        white_engine = _mock_engine(white_moves)
        black_engine = _mock_engine(black_moves)

        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0),
            move_overhead_ms=0,  # Disabled
        )

        outcome = await play_game(
            white=white_engine,
            black=black_engine,
            config=config,
        )

        assert outcome.result == GameResult.WHITE_WIN
        assert outcome.termination == TerminationReason.CHECKMATE


class TestWatchdogTimeoutMs:
    """Tests for _watchdog_timeout_ms helper."""

    def test_fixed_time_returns_movetime(self) -> None:
        tc = FixedTimeControl(movetime_ms=10000)
        assert _watchdog_timeout_ms(tc, is_white=True) == 10000
        assert _watchdog_timeout_ms(tc, is_white=False) == 10000

    def test_increment_returns_current_side_time(self) -> None:
        tc = IncrementTimeControl(wtime_ms=60000, btime_ms=30000)
        assert _watchdog_timeout_ms(tc, is_white=True) == 60000
        assert _watchdog_timeout_ms(tc, is_white=False) == 30000

    def test_depth_returns_zero(self) -> None:
        tc = DepthTimeControl(depth=20)
        assert _watchdog_timeout_ms(tc, is_white=True) == 0

    def test_nodes_returns_zero(self) -> None:
        tc = NodesTimeControl(nodes=50000)
        assert _watchdog_timeout_ms(tc, is_white=False) == 0


class TestWatchdogIntegration:
    """Integration tests for watchdog timeout in play_game."""

    @pytest.mark.asyncio
    async def test_movetime_watchdog_does_not_fire_early(self) -> None:
        """With movetime=10000 and overhead=5000, watchdog should not fire before 10s.

        The mock engine responds instantly, so the watchdog (10s + 5s = 15s)
        should never trigger and the game should complete normally.
        """
        white_moves = ["e2e4", "d1h5", "f1c4", "h5f7"]
        black_moves = ["e7e5", "b8c6", "g8f6"]

        white_engine = _mock_engine(white_moves)
        black_engine = _mock_engine(black_moves)

        config = GameConfig(
            time_control=FixedTimeControl(movetime_ms=10000),
            adjudication=AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0),
            move_overhead_ms=5000,
        )

        outcome = await play_game(
            white=white_engine,
            black=black_engine,
            config=config,
        )

        assert outcome.result == GameResult.WHITE_WIN
        assert outcome.termination == TerminationReason.CHECKMATE

    @pytest.mark.asyncio
    async def test_movetime_watchdog_fires_after_total_deadline(self) -> None:
        """Watchdog fires after movetime + overhead when engine hangs.

        movetime=100ms + overhead=50ms = 150ms total deadline.
        Engine sleeps for 10s, so the watchdog should fire.
        """
        import asyncio as _asyncio

        white_engine = MagicMock()
        white_engine.start = AsyncMock()
        white_engine.quit = AsyncMock()
        white_engine.uci = AsyncMock(return_value=[])
        white_engine.isready = AsyncMock()
        white_engine.position = AsyncMock()
        white_engine.stop = AsyncMock()
        white_engine.is_running = True

        async def slow_go(_tc: object) -> tuple[BestMove, list[UCIInfo]]:
            await _asyncio.sleep(10)
            return BestMove(move="e2e4"), []

        white_engine.go = AsyncMock(side_effect=slow_go)
        black_engine = _mock_engine(["e7e5"])

        config = GameConfig(
            time_control=FixedTimeControl(movetime_ms=100),
            adjudication=AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0),
            move_overhead_ms=50,  # total = 150ms; engine sleeps 10s → timeout
        )

        outcome = await play_game(
            white=white_engine,
            black=black_engine,
            config=config,
        )

        assert outcome.result == GameResult.BLACK_WIN
        assert outcome.termination == TerminationReason.TIMEOUT

    @pytest.mark.asyncio
    async def test_increment_tc_watchdog_uses_side_clock(self) -> None:
        """For wtime/btime TC, watchdog uses the current side's remaining time.

        White has 60s remaining + 50ms overhead = 60.05s deadline.
        Engine responds instantly, so no timeout.
        """
        white_moves = ["e2e4", "d1h5", "f1c4", "h5f7"]
        black_moves = ["e7e5", "b8c6", "g8f6"]

        white_engine = _mock_engine(white_moves)
        black_engine = _mock_engine(black_moves)

        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=60000, btime_ms=60000),
            adjudication=AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0),
            move_overhead_ms=50,
        )

        outcome = await play_game(
            white=white_engine,
            black=black_engine,
            config=config,
        )

        assert outcome.result == GameResult.WHITE_WIN
        assert outcome.termination == TerminationReason.CHECKMATE

    @pytest.mark.asyncio
    async def test_depth_tc_watchdog_uses_overhead_only(self) -> None:
        """For depth TC, watchdog uses only move_overhead_ms (no time bound).

        Engine responds instantly, so the overhead-only watchdog never fires.
        """
        white_moves = ["e2e4", "d1h5", "f1c4", "h5f7"]
        black_moves = ["e7e5", "b8c6", "g8f6"]

        white_engine = _mock_engine(white_moves)
        black_engine = _mock_engine(black_moves)

        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0),
            move_overhead_ms=5000,
        )

        outcome = await play_game(
            white=white_engine,
            black=black_engine,
            config=config,
        )

        assert outcome.result == GameResult.WHITE_WIN
        assert outcome.termination == TerminationReason.CHECKMATE
