"""Tests for the converters module."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.converters import (
    game_result_from_string,
    game_to_response,
    game_to_summary,
    move_to_response,
    sprt_test_to_response,
    time_control_from_string,
    time_control_to_response,
)
from shared.storage.models import (
    Game,
    GameResult,
    Move,
    SPRTStatus,
    SPRTTest,
)
from shared.time_control import (
    DepthTimeControl,
    FixedTimeControl,
    IncrementTimeControl,
    NodesTimeControl,
)


class TestTimeControlFromString:
    """Tests for time_control_from_string."""

    def test_movetime(self) -> None:
        tc = time_control_from_string("movetime=1000")
        assert isinstance(tc, FixedTimeControl)
        assert tc.movetime_ms == 1000

    def test_depth(self) -> None:
        tc = time_control_from_string("depth=10")
        assert isinstance(tc, DepthTimeControl)
        assert tc.depth == 10

    def test_nodes(self) -> None:
        tc = time_control_from_string("nodes=50000")
        assert isinstance(tc, NodesTimeControl)
        assert tc.nodes == 50000

    def test_increment(self) -> None:
        tc = time_control_from_string("wtime=60000,btime=60000,winc=1000,binc=1000")
        assert isinstance(tc, IncrementTimeControl)
        assert tc.wtime_ms == 60000
        assert tc.winc_ms == 1000

    def test_unknown_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown time control"):
            time_control_from_string("invalid=abc")


class TestTimeControlToResponse:
    """Tests for time_control_to_response."""

    def test_fixed_time(self) -> None:
        r = time_control_to_response(FixedTimeControl(movetime_ms=500))
        assert r.type == "fixed_time"
        assert r.movetime_ms == 500

    def test_depth(self) -> None:
        r = time_control_to_response(DepthTimeControl(depth=15))
        assert r.type == "depth"
        assert r.depth == 15

    def test_nodes(self) -> None:
        r = time_control_to_response(NodesTimeControl(nodes=100))
        assert r.type == "nodes"
        assert r.nodes == 100

    def test_increment(self) -> None:
        r = time_control_to_response(
            IncrementTimeControl(wtime_ms=1000, btime_ms=1000, winc_ms=100, binc_ms=100)
        )
        assert r.type == "increment"
        assert r.wtime_ms == 1000


class TestMoveToResponse:
    """Tests for move_to_response."""

    def test_basic_move(self) -> None:
        m = Move(uci="e2e4", san="e4", fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR")
        r = move_to_response(m)
        assert r.uci == "e2e4"
        assert r.san == "e4"

    def test_move_with_eval(self) -> None:
        m = Move(
            uci="e2e4",
            san="e4",
            fen_after="...",
            score_cp=35,
            depth=20,
        )
        r = move_to_response(m)
        assert r.score_cp == 35
        assert r.depth == 20


class TestGameToResponse:
    """Tests for game_to_response."""

    def test_game_conversion(self) -> None:
        game = Game(
            id="test-game-1",
            white_engine="engine-a",
            black_engine="engine-b",
            result=GameResult.WHITE_WIN,
            moves=[
                Move(uci="e2e4", san="e4", fen_after="..."),
            ],
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            time_control=FixedTimeControl(movetime_ms=1000),
        )
        r = game_to_response(game)
        assert r.id == "test-game-1"
        assert r.result == "1-0"
        assert len(r.moves) == 1
        assert r.time_control is not None
        assert r.time_control.type == "fixed_time"


class TestGameToSummary:
    """Tests for game_to_summary."""

    def test_summary(self) -> None:
        game = Game(
            id="test-game-2",
            white_engine="engine-a",
            black_engine="engine-b",
            result=GameResult.DRAW,
            moves=[
                Move(uci="e2e4", san="e4", fen_after="..."),
                Move(uci="e7e5", san="e5", fen_after="..."),
            ],
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        r = game_to_summary(game)
        assert r.id == "test-game-2"
        assert r.move_count == 2
        assert r.result == "1/2-1/2"


class TestSPRTTestToResponse:
    """Tests for sprt_test_to_response."""

    def test_conversion(self) -> None:
        test = SPRTTest(
            id="test-1",
            engine_a="a",
            engine_b="b",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
            alpha=0.05,
            beta=0.05,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            status=SPRTStatus.RUNNING,
            wins=10,
            losses=5,
            draws=3,
            llr=1.5,
        )
        r = sprt_test_to_response(test)
        assert r.id == "test-1"
        assert r.status == "running"
        assert r.wins == 10
        assert r.result is None


class TestGameResultFromString:
    """Tests for game_result_from_string."""

    def test_white_win(self) -> None:
        assert game_result_from_string("1-0") == GameResult.WHITE_WIN

    def test_draw(self) -> None:
        assert game_result_from_string("1/2-1/2") == GameResult.DRAW

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            game_result_from_string("invalid")
