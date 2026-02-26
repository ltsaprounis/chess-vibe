"""Tests for the SPRT runner CLI entry point and orchestration."""

from __future__ import annotations

import json

import pytest
from shared.storage.models import GameResult
from shared.time_control import FixedTimeControl
from sprt_runner.runner import (
    RunConfig,
    format_complete_message,
    format_error_message,
    format_game_result_message,
    format_progress_message,
    parse_time_control,
)


class TestParseTimeControl:
    """Tests for CLI time control string parsing."""

    def test_fixed_time(self) -> None:
        tc = parse_time_control("movetime=1000")
        assert isinstance(tc, FixedTimeControl)
        assert tc.movetime_ms == 1000

    def test_depth(self) -> None:
        from shared.time_control import DepthTimeControl

        tc = parse_time_control("depth=10")
        assert isinstance(tc, DepthTimeControl)
        assert tc.depth == 10

    def test_nodes(self) -> None:
        from shared.time_control import NodesTimeControl

        tc = parse_time_control("nodes=50000")
        assert isinstance(tc, NodesTimeControl)
        assert tc.nodes == 50000

    def test_increment(self) -> None:
        from shared.time_control import IncrementTimeControl

        tc = parse_time_control("wtime=60000,btime=60000,winc=1000,binc=1000")
        assert isinstance(tc, IncrementTimeControl)
        assert tc.wtime_ms == 60000

    def test_invalid_format(self) -> None:
        with pytest.raises(ValueError, match="Unknown time control"):
            parse_time_control("invalid=5")


class TestRunConfig:
    """Tests for RunConfig construction."""

    def test_basic_config(self) -> None:
        config = RunConfig(
            base="random-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
        )
        assert config.base == "random-engine"
        assert config.test == "test-engine"
        assert config.alpha == 0.05
        assert config.beta == 0.05


class TestJSONLineFormatters:
    """Tests for JSON-lines output formatting."""

    def test_game_result_message(self) -> None:
        msg = format_game_result_message(
            game_id="game-1",
            result=GameResult.WHITE_WIN,
            termination="checkmate",
            move_count=42,
        )
        parsed = json.loads(msg)
        assert parsed["type"] == "game_result"
        assert parsed["game_id"] == "game-1"
        assert parsed["result"] == "1-0"
        assert parsed["termination"] == "checkmate"
        assert parsed["move_count"] == 42

    def test_progress_message(self) -> None:
        msg = format_progress_message(
            wins=10,
            losses=5,
            draws=3,
            llr=1.5,
            lower_bound=-2.94,
            upper_bound=2.94,
            games_total=18,
        )
        parsed = json.loads(msg)
        assert parsed["type"] == "progress"
        assert parsed["wins"] == 10
        assert parsed["losses"] == 5
        assert parsed["draws"] == 3
        assert parsed["llr"] == 1.5

    def test_complete_message(self) -> None:
        msg = format_complete_message(
            result="H1",
            total_games=100,
            llr=3.5,
        )
        parsed = json.loads(msg)
        assert parsed["type"] == "complete"
        assert parsed["result"] == "H1"
        assert parsed["total_games"] == 100

    def test_error_message(self) -> None:
        msg = format_error_message("Engine crashed unexpectedly")
        parsed = json.loads(msg)
        assert parsed["type"] == "error"
        assert parsed["message"] == "Engine crashed unexpectedly"

    def test_all_messages_are_valid_json(self) -> None:
        """All formatters should produce valid single-line JSON."""
        messages = [
            format_game_result_message("g1", GameResult.DRAW, "stalemate", 50),
            format_progress_message(0, 0, 0, 0.0, -3.0, 3.0, 0),
            format_complete_message("H0", 200, -3.5),
            format_error_message("test error"),
        ]
        for msg in messages:
            assert "\n" not in msg
            json.loads(msg)  # Should not raise
