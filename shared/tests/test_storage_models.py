"""Tests for storage domain models."""

from datetime import datetime

import pytest
from shared.storage.models import (
    Engine,
    Game,
    GameFilter,
    GameResult,
    Move,
    OpeningBook,
    SPRTOutcome,
    SPRTStatus,
    SPRTTest,
    SPRTTestFilter,
)
from shared.time_control import FixedTimeControl

# ---------------------------------------------------------------------------
# Move
# ---------------------------------------------------------------------------


class TestMove:
    """Tests for the Move dataclass."""

    def test_creation_minimal(self) -> None:
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        move = Move(uci="e2e4", san="e4", fen_after=fen)
        assert move.uci == "e2e4"
        assert move.san == "e4"
        assert move.score_cp is None
        assert move.pv == []

    def test_creation_with_eval(self) -> None:
        move = Move(
            uci="e2e4",
            san="e4",
            fen_after="fen",
            score_cp=35,
            depth=20,
            seldepth=25,
            pv=["e7e5", "g1f3"],
            nodes=1000000,
            time_ms=500,
            clock_white_ms=59500,
            clock_black_ms=60000,
        )
        assert move.score_cp == 35
        assert move.depth == 20
        assert move.seldepth == 25
        assert move.pv == ["e7e5", "g1f3"]
        assert move.nodes == 1000000
        assert move.time_ms == 500
        assert move.clock_white_ms == 59500
        assert move.clock_black_ms == 60000

    def test_creation_with_mate_score(self) -> None:
        move = Move(uci="d1h5", san="Qh5#", fen_after="fen", score_mate=1)
        assert move.score_mate == 1
        assert move.score_cp is None

    def test_frozen(self) -> None:
        move = Move(uci="e2e4", san="e4", fen_after="fen")
        with pytest.raises(AttributeError):
            move.uci = "d2d4"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Game
# ---------------------------------------------------------------------------


class TestGame:
    """Tests for the Game dataclass."""

    def test_creation_minimal(self) -> None:
        now = datetime.now()
        game = Game(
            id="game-1",
            white_engine="engine-a",
            black_engine="engine-b",
            result=GameResult.WHITE_WIN,
            moves=[],
            created_at=now,
        )
        assert game.id == "game-1"
        assert game.result == GameResult.WHITE_WIN
        assert game.opening_name is None
        assert game.sprt_test_id is None
        assert game.start_fen is None
        assert game.time_control is None

    def test_creation_with_all_fields(self) -> None:
        tc = FixedTimeControl(movetime_ms=1000)
        now = datetime.now()
        game = Game(
            id="game-2",
            white_engine="engine-a",
            black_engine="engine-b",
            result=GameResult.DRAW,
            moves=[Move(uci="e2e4", san="e4", fen_after="fen")],
            created_at=now,
            opening_name="Sicilian Defense",
            sprt_test_id="test-1",
            start_fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            time_control=tc,
        )
        assert game.opening_name == "Sicilian Defense"
        assert game.sprt_test_id == "test-1"
        assert game.time_control == tc

    def test_frozen(self) -> None:
        game = Game(
            id="g",
            white_engine="w",
            black_engine="b",
            result=GameResult.UNFINISHED,
            moves=[],
            created_at=datetime.now(),
        )
        with pytest.raises(AttributeError):
            game.id = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# GameResult enum
# ---------------------------------------------------------------------------


class TestGameResult:
    """Tests for the GameResult enum."""

    def test_values(self) -> None:
        assert GameResult.WHITE_WIN.value == "1-0"
        assert GameResult.BLACK_WIN.value == "0-1"
        assert GameResult.DRAW.value == "1/2-1/2"
        assert GameResult.UNFINISHED.value == "*"

    def test_from_value(self) -> None:
        assert GameResult("1-0") == GameResult.WHITE_WIN


# ---------------------------------------------------------------------------
# SPRTTest
# ---------------------------------------------------------------------------


class TestSPRTTest:
    """Tests for the SPRTTest dataclass."""

    def test_creation_with_defaults(self) -> None:
        tc = FixedTimeControl(movetime_ms=1000)
        now = datetime.now()
        test = SPRTTest(
            id="test-1",
            engine_a="engine-a",
            engine_b="engine-b",
            time_control=tc,
            elo0=0.0,
            elo1=5.0,
            alpha=0.05,
            beta=0.05,
            created_at=now,
        )
        assert test.status == SPRTStatus.RUNNING
        assert test.wins == 0
        assert test.losses == 0
        assert test.draws == 0
        assert test.llr == 0.0
        assert test.result is None
        assert test.completed_at is None

    def test_frozen(self) -> None:
        tc = FixedTimeControl(movetime_ms=1000)
        test = SPRTTest(
            id="t",
            engine_a="a",
            engine_b="b",
            time_control=tc,
            elo0=0.0,
            elo1=5.0,
            alpha=0.05,
            beta=0.05,
            created_at=datetime.now(),
        )
        with pytest.raises(AttributeError):
            test.wins = 10  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SPRTStatus / SPRTOutcome enums
# ---------------------------------------------------------------------------


class TestSPRTEnums:
    """Tests for SPRTStatus and SPRTOutcome enums."""

    def test_status_values(self) -> None:
        assert SPRTStatus.RUNNING.value == "running"
        assert SPRTStatus.COMPLETED.value == "completed"
        assert SPRTStatus.CANCELLED.value == "cancelled"

    def test_outcome_values(self) -> None:
        assert SPRTOutcome.H0.value == "H0"
        assert SPRTOutcome.H1.value == "H1"


# ---------------------------------------------------------------------------
# Engine / OpeningBook
# ---------------------------------------------------------------------------


class TestEngine:
    """Tests for the Engine dataclass."""

    def test_creation(self) -> None:
        e = Engine(id="e1", name="Stockfish", path="/usr/bin/stockfish")
        assert e.id == "e1"
        assert e.description is None

    def test_creation_with_description(self) -> None:
        e = Engine(id="e1", name="Stockfish", path="/usr/bin/stockfish", description="Fast engine")
        assert e.description == "Fast engine"

    def test_frozen(self) -> None:
        e = Engine(id="e1", name="n", path="/p")
        with pytest.raises(AttributeError):
            e.id = "e2"  # type: ignore[misc]


class TestOpeningBook:
    """Tests for the OpeningBook dataclass."""

    def test_creation(self) -> None:
        b = OpeningBook(id="b1", name="Default", path="/books/default.pgn", format="pgn")
        assert b.format == "pgn"

    def test_frozen(self) -> None:
        b = OpeningBook(id="b1", name="n", path="/p", format="pgn")
        with pytest.raises(AttributeError):
            b.id = "b2"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestGameFilter:
    """Tests for the GameFilter dataclass."""

    def test_defaults_all_none(self) -> None:
        f = GameFilter()
        assert f.sprt_test_id is None
        assert f.result is None
        assert f.engine_id is None
        assert f.opening_name is None

    def test_with_values(self) -> None:
        f = GameFilter(result=GameResult.WHITE_WIN, engine_id="engine-a")
        assert f.result == GameResult.WHITE_WIN
        assert f.engine_id == "engine-a"


class TestSPRTTestFilter:
    """Tests for the SPRTTestFilter dataclass."""

    def test_defaults_all_none(self) -> None:
        f = SPRTTestFilter()
        assert f.status is None
        assert f.engine_id is None

    def test_with_values(self) -> None:
        f = SPRTTestFilter(status=SPRTStatus.RUNNING)
        assert f.status == SPRTStatus.RUNNING
