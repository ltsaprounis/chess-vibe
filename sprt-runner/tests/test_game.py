"""Tests for single game play between two UCI engines."""

from __future__ import annotations

import dataclasses
import logging
import pickle
from collections.abc import Iterator, Sequence
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.storage.models import GameResult, Move
from shared.time_control import (
    DepthTimeControl,
    FixedTimeControl,
    IncrementTimeControl,
    NodesTimeControl,
    TimeControl,
)
from shared.uci_client import BestMove, UCIEngineError, UCIInfo, UCIScore, UCITimeoutError
from sprt_runner.adjudication import AdjudicationConfig
from sprt_runner.game import (
    DEFAULT_MOVE_TIMEOUT_MS,
    DEFAULT_TIME_MARGIN_MS,
    GameConfig,
    GameOutcome,
    TerminationReason,
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
        assert config.time_margin_ms == DEFAULT_TIME_MARGIN_MS == 100
        assert config.move_timeout_ms == DEFAULT_MOVE_TIMEOUT_MS == 30_000

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


class TestSyzygyTablebaseLifecycle:
    """Tests that the Syzygy tablebase handle is opened once per game and reused."""

    @pytest.mark.asyncio
    async def test_tablebase_opened_once_not_per_probe(self) -> None:
        """The tablebase handle is opened once and reused for every probe.

        A bare K+P vs K endgame keeps the piece count low for the whole
        game, so every half-move triggers an adjudication probe. Kings
        shuffle back and forth (never adjacent, never giving check) so
        every scripted move stays legal. The tablebase mock always raises
        KeyError (position not found) so the game runs to the max-moves
        limit, producing several probes.
        """
        fen = "4k3/8/8/8/8/8/P7/4K3 w - - 0 1"
        white_moves = ["e1d1", "d1e1"]
        black_moves = ["e8d8", "d8e8"]

        white_engine = _mock_engine(white_moves)
        black_engine = _mock_engine(black_moves)

        mock_tb = MagicMock()
        mock_tb.probe_wdl.side_effect = KeyError("not found")

        config = GameConfig(
            time_control=DepthTimeControl(depth=1),
            adjudication=AdjudicationConfig(
                syzygy_path=Path("/fake/syzygy"),
                win_consecutive_moves=0,
                draw_consecutive_moves=0,
            ),
            start_fen=fen,
            max_moves=2,
        )

        with patch("chess.syzygy.open_tablebase", return_value=mock_tb) as mock_open:
            outcome = await play_game(white=white_engine, black=black_engine, config=config)

        mock_open.assert_called_once()
        assert mock_tb.probe_wdl.call_count == 4
        # Exactly one close, after all probes — guards against a handle
        # closed (and then reused) mid-game, e.g. inside _check_syzygy.
        mock_tb.close.assert_called_once()
        assert outcome.termination == TerminationReason.MAX_MOVES

    @pytest.mark.asyncio
    async def test_tablebase_closed_when_game_ends_normally(self) -> None:
        """The tablebase handle is closed once the game completes."""
        white_moves = ["e2e4", "d1h5", "f1c4", "h5f7"]
        black_moves = ["e7e5", "b8c6", "g8f6"]

        white_engine = _mock_engine(white_moves)
        black_engine = _mock_engine(black_moves)

        mock_tb = MagicMock()

        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(
                syzygy_path=Path("/fake/syzygy"),
                win_consecutive_moves=0,
                draw_consecutive_moves=0,
            ),
        )

        with patch("chess.syzygy.open_tablebase", return_value=mock_tb):
            outcome = await play_game(white=white_engine, black=black_engine, config=config)

        assert outcome.termination == TerminationReason.CHECKMATE
        mock_tb.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_tablebase_closed_on_exception(self) -> None:
        """The tablebase handle is closed even when the game loop raises."""
        white_engine = _mock_engine(["h1h2"])
        white_engine.position = AsyncMock(side_effect=RuntimeError("boom"))
        black_engine = _mock_engine([])

        mock_tb = MagicMock()

        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(syzygy_path=Path("/fake/syzygy")),
        )

        with (
            patch("chess.syzygy.open_tablebase", return_value=mock_tb),
            pytest.raises(RuntimeError),
        ):
            await play_game(white=white_engine, black=black_engine, config=config)

        mock_tb.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_completed_outcome_survives_close_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A tablebase.close() failure must not destroy a completed GameOutcome.

        Closing the handle is best-effort cleanup after the game is
        already decided; if it raises (e.g. the underlying mmap close
        fails), the already-computed result must still be returned, not
        replaced by the close exception.
        """
        white_moves = ["e2e4", "d1h5", "f1c4", "h5f7"]
        black_moves = ["e7e5", "b8c6", "g8f6"]

        white_engine = _mock_engine(white_moves)
        black_engine = _mock_engine(black_moves)

        mock_tb = MagicMock()
        mock_tb.close.side_effect = OSError("mmap close failed")

        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(
                syzygy_path=Path("/fake/syzygy"),
                win_consecutive_moves=0,
                draw_consecutive_moves=0,
            ),
        )

        with (
            patch("chess.syzygy.open_tablebase", return_value=mock_tb),
            caplog.at_level(logging.WARNING),
        ):
            outcome = await play_game(white=white_engine, black=black_engine, config=config)

        assert outcome.result == GameResult.WHITE_WIN
        assert outcome.termination == TerminationReason.CHECKMATE
        mock_tb.close.assert_called_once()
        assert any("close" in record.message.lower() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_no_tablebase_opened_when_syzygy_path_is_none(self) -> None:
        """No tablebase is opened at all when syzygy_path is None."""
        white_moves = ["e2e4", "d1h5", "f1c4", "h5f7"]
        black_moves = ["e7e5", "b8c6", "g8f6"]

        white_engine = _mock_engine(white_moves)
        black_engine = _mock_engine(black_moves)

        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(
                syzygy_path=None, win_consecutive_moves=0, draw_consecutive_moves=0
            ),
        )

        with patch("chess.syzygy.open_tablebase") as mock_open:
            outcome = await play_game(white=white_engine, black=black_engine, config=config)

        mock_open.assert_not_called()
        assert outcome.termination == TerminationReason.CHECKMATE

    @pytest.mark.asyncio
    async def test_tablebase_open_failure_does_not_crash_game(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Open failure is logged once, not per move, and does not crash the game.

        Pins two things: (1) the failure is actually logged — deleting
        the warning in ``_open_tablebase`` must turn this test red — and
        (2) it is logged exactly once, not once per half-move for the
        rest of the game (the fix for the original per-move warning
        spam this test used to let through silently).
        """
        white_moves = ["e2e4", "d1h5", "f1c4", "h5f7"]
        black_moves = ["e7e5", "b8c6", "g8f6"]

        white_engine = _mock_engine(white_moves)
        black_engine = _mock_engine(black_moves)

        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(
                syzygy_path=Path("/fake/syzygy"),
                win_consecutive_moves=0,
                draw_consecutive_moves=0,
            ),
        )

        with (
            patch("chess.syzygy.open_tablebase", side_effect=OSError("cannot open directory")),
            caplog.at_level(logging.WARNING),
        ):
            outcome = await play_game(white=white_engine, black=black_engine, config=config)

        assert outcome.result == GameResult.WHITE_WIN
        assert outcome.termination == TerminationReason.CHECKMATE
        open_failure_records = [r for r in caplog.records if "Failed to open Syzygy" in r.message]
        assert len(open_failure_records) == 1
        assert len(caplog.records) == 1

    @pytest.mark.asyncio
    async def test_tablebase_opened_and_closed_once_per_game_not_per_process(self) -> None:
        """Each ``play_game`` call opens and closes its own handle.

        Guards against the module-level-cache-keyed-by-path design the
        issue explicitly rejected (global mutable state with no clear
        ownership or close point): under a cache, a second game would
        reuse — or worse, reuse an already-closed — handle from the
        first, so ``open_tablebase`` would be called once total rather
        than once per game.
        """
        white_moves = ["e2e4", "d1h5", "f1c4", "h5f7"]
        black_moves = ["e7e5", "b8c6", "g8f6"]

        mock_tb = MagicMock()

        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(
                syzygy_path=Path("/fake/syzygy"),
                win_consecutive_moves=0,
                draw_consecutive_moves=0,
            ),
        )

        with patch("chess.syzygy.open_tablebase", return_value=mock_tb) as mock_open:
            outcome_1 = await play_game(
                white=_mock_engine(white_moves), black=_mock_engine(black_moves), config=config
            )
            outcome_2 = await play_game(
                white=_mock_engine(white_moves), black=_mock_engine(black_moves), config=config
            )

        assert outcome_1.termination == TerminationReason.CHECKMATE
        assert outcome_2.termination == TerminationReason.CHECKMATE
        assert mock_open.call_count == 2
        assert mock_tb.close.call_count == 2

    @pytest.mark.asyncio
    async def test_tablebase_closed_on_adjudication_exit(self) -> None:
        """The handle is closed when the game ends via tablebase adjudication itself.

        Every other lifecycle test exits via CHECKMATE, MAX_MOVES, or an
        exception; this covers the one exit where a probe directly
        produces the returned ``GameOutcome``.
        """
        fen = "4k3/8/8/8/8/8/P7/4K3 w - - 0 1"
        white_engine = _mock_engine(["e1d1"])
        black_engine = _mock_engine([])

        mock_tb = MagicMock()
        mock_tb.probe_wdl.return_value = 2  # Decisive for side to move

        config = GameConfig(
            time_control=DepthTimeControl(depth=1),
            adjudication=AdjudicationConfig(
                syzygy_path=Path("/fake/syzygy"),
                win_consecutive_moves=0,
                draw_consecutive_moves=0,
            ),
            start_fen=fen,
        )

        with patch("chess.syzygy.open_tablebase", return_value=mock_tb):
            outcome = await play_game(white=white_engine, black=black_engine, config=config)

        assert outcome.termination == TerminationReason.ADJUDICATION
        mock_tb.probe_wdl.assert_called_once()
        mock_tb.close.assert_called_once()


class TestGameConfigPicklability:
    """Regression test: GameConfig must remain picklable across process boundaries.

    The SPRT runner sends GameConfig (via WorkerTask) to worker processes
    through multiprocessing.Process, which pickles its args. A live
    Syzygy tablebase handle is not picklable, so it must never live on
    the config — only the syzygy_path (a Path) does.
    """

    def test_game_config_with_syzygy_path_is_picklable(self) -> None:
        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(syzygy_path=Path("/fake/syzygy")),
        )

        pickled = pickle.dumps(config)
        restored = pickle.loads(pickled)

        assert restored.adjudication.syzygy_path == Path("/fake/syzygy")

    def test_adjudication_config_has_no_tablebase_field(self) -> None:
        """Structural guard: AdjudicationConfig must never gain a Tablebase field.

        A frozen-dataclass instance with a ``None``-valued field still
        pickles fine, so an instance-level check alone would not catch a
        future ``tablebase: chess.syzygy.Tablebase | None = None`` field
        being added back to the config — exactly the design the issue
        rejected. Assert on the declared shape instead of one instance.
        """
        for f in dataclasses.fields(AdjudicationConfig):
            assert "Tablebase" not in str(f.type), (
                f"AdjudicationConfig.{f.name} must not reference chess.syzygy.Tablebase; "
                "the open handle belongs in play_game(), not the (pickled) config"
            )

    @pytest.mark.asyncio
    async def test_config_stays_picklable_after_handle_is_opened(self) -> None:
        """The config instance play_game() receives must stay picklable after use.

        Uses an unpicklable stand-in for the tablebase handle (a bare
        ``MagicMock`` fails ``pickle.dumps``), so this would fail if
        ``play_game`` ever stashed the open handle onto ``config`` —
        e.g. via ``dataclasses.replace`` — even though
        ``test_game_config_with_syzygy_path_is_picklable`` above stays
        green regardless (it never opens a handle at all).
        """
        white_moves = ["e2e4", "d1h5", "f1c4", "h5f7"]
        black_moves = ["e7e5", "b8c6", "g8f6"]

        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(
                syzygy_path=Path("/fake/syzygy"),
                win_consecutive_moves=0,
                draw_consecutive_moves=0,
            ),
        )

        unpicklable_handle = MagicMock()
        with patch("chess.syzygy.open_tablebase", return_value=unpicklable_handle):
            await play_game(
                white=_mock_engine(white_moves), black=_mock_engine(black_moves), config=config
            )

        pickled = pickle.dumps(config)
        restored = pickle.loads(pickled)
        assert restored.adjudication.syzygy_path == Path("/fake/syzygy")


# ---------------------------------------------------------------------------
# Clock accounting and move limits (fake clock)
# ---------------------------------------------------------------------------

_NO_ADJUDICATION = AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0)

# Pawn pushes that stay legal in either move order and can never repeat a
# position, so scripted time use is the only thing that can end a short game.
_WHITE_PUSHES = [f"{f}2{f}3" for f in "abcdefgh"] + [f"{f}3{f}4" for f in "abcdefgh"]
_BLACK_PUSHES = [f"{f}7{f}6" for f in "abcdefgh"] + [f"{f}6{f}5" for f in "abcdefgh"]

_WHITE_TO_MOVE_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_BLACK_TO_MOVE_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1"

# What UCIClient.go falls back to when no timeout is passed. The fake models
# it because silently reaching this limit is one of the bugs under test.
_CLIENT_DEFAULT_TIMEOUT_S = 10.0

# Both ways a mover can lose on time. The client's deadline fires during the
# search; the post-go check catches a move that arrives just past the limit,
# before a deadline which only started after the go command was sent.
_FLAG_PATHS = pytest.mark.parametrize(
    "client_raises", [True, False], ids=["client_deadline", "post_go_check"]
)


class _FakeTime:
    """Stands in for the ``time`` module as seen by ``sprt_runner.game``.

    It offers only ``monotonic_ns``, so accounting on any other clock fails
    with an AttributeError instead of passing unnoticed.
    """

    def __init__(self) -> None:
        self.now_ns = 7_000_000_000_000

    def monotonic_ns(self) -> int:
        return self.now_ns

    def advance_ms(self, ms: float) -> None:
        self.now_ns += round(ms * 1_000_000)


@dataclasses.dataclass(frozen=True)
class _GoCall:
    """One recorded ``go``: what the engine was told and how long it was given."""

    time_control: TimeControl
    timeout: float | None


class _TimedEngine:
    """Scripted UCI client whose searches advance the fake clock, not real time.

    Attributes:
        client: The mock to hand to ``play_game``.
        calls: Every ``go`` received, in order.
    """

    def __init__(
        self,
        fake_time: _FakeTime,
        moves: Sequence[str],
        spends_ms: Sequence[float] = (0,),
        *,
        client_raises: bool = True,
        position_spend_ms: float = 0,
        reported_time_ms: int | None = None,
        crash_on_search: int | None = None,
        sends_info: bool = True,
    ) -> None:
        """Script the engine.

        Args:
            fake_time: Clock that each search advances.
            moves: Moves to play, in order.
            spends_ms: Time spent per search. The last entry repeats.
            client_raises: If True, ``go`` raises UCITimeoutError when the
                spend exceeds the timeout, like the real client. If False, the
                move arrives late without raising, like a move that lands just
                before the client's later-starting deadline fires.
            position_spend_ms: Time spent inside each ``position`` call.
            reported_time_ms: Search time the engine claims in its ``info``
                line, whatever it really spent.
            crash_on_search: Zero-based search on which ``go`` raises
                UCIEngineError, as the real client does when the engine dies.
            sends_info: If False, the engine answers with ``bestmove`` alone,
                which UCI allows.
        """
        self.calls: list[_GoCall] = []
        self._fake_time = fake_time
        self._moves = list(moves)
        self._spends_ms = list(spends_ms)
        self._client_raises = client_raises
        self._position_spend_ms = position_spend_ms
        self._reported_time_ms = reported_time_ms
        self._crash_on_search = crash_on_search
        self._sends_info = sends_info

        self.client = MagicMock()
        self.client.position = AsyncMock(side_effect=self._position)
        self.client.go = AsyncMock(side_effect=self._go)

    async def _position(self, *, fen: str | None = None, moves: list[str] | None = None) -> None:
        self._fake_time.advance_ms(self._position_spend_ms)

    async def _go(
        self, time_control: TimeControl, *, timeout: float | None = None
    ) -> tuple[BestMove, list[UCIInfo]]:
        index = len(self.calls)
        self.calls.append(_GoCall(time_control, timeout))
        if index == self._crash_on_search:
            raise UCIEngineError("engine process terminated")
        assert index < len(self._moves), f"engine asked for move {index + 1} past its script"

        spend_ms = self._spends_ms[min(index, len(self._spends_ms) - 1)]
        effective_s = timeout if timeout is not None else _CLIENT_DEFAULT_TIMEOUT_S
        if self._client_raises and round(spend_ms * 1_000_000) > round(effective_s * 1e9):
            self._fake_time.advance_ms(effective_s * 1000)
            raise UCITimeoutError(f"no bestmove within {effective_s}s")

        self._fake_time.advance_ms(spend_ms)
        move = self._moves[index]
        info = UCIInfo(depth=1, score=UCIScore(cp=0), pv=[move], time_ms=self._reported_time_ms)
        return BestMove(move=move), [info] if self._sends_info else []


@pytest.fixture
def fake_time() -> Iterator[_FakeTime]:
    """Replace the ``time`` module used by the game loop with a fake clock."""
    fake = _FakeTime()
    with patch("sprt_runner.game.time", fake):
        yield fake


def _clocks_sent(engine: _TimedEngine) -> list[tuple[int, int]]:
    """Return the (wtime, btime) pair of every ``go`` the engine received."""
    sent: list[tuple[int, int]] = []
    for call in engine.calls:
        assert isinstance(call.time_control, IncrementTimeControl)
        sent.append((call.time_control.wtime_ms, call.time_control.btime_ms))
    return sent


def _timeouts_sent(engine: _TimedEngine) -> list[float | None]:
    """Return the timeout of every ``go`` the engine received."""
    return [call.timeout for call in engine.calls]


async def _play(config: GameConfig, white: _TimedEngine, black: _TimedEngine) -> GameOutcome:
    """Play a game between two timed engines."""
    return await play_game(white=white.client, black=black.client, config=config)


class TestFallingClocks:
    """Every ``go`` carries both sides' current clocks, not the starting ones."""

    @pytest.mark.asyncio
    async def test_every_go_carries_both_current_clocks(self, fake_time: _FakeTime) -> None:
        """Different start values and increments, so a swapped side is caught."""
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [100, 200, 300])
        black = _TimedEngine(fake_time, _BLACK_PUSHES, [50, 60, 70])
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=5000, btime_ms=3000, winc_ms=70, binc_ms=30),
            adjudication=_NO_ADJUDICATION,
            max_moves=3,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert _clocks_sent(white) == [(5000, 3000), (4970, 2980), (4840, 2950)]
        assert _clocks_sent(black) == [(4970, 3000), (4840, 2980), (4610, 2950)]

    @pytest.mark.asyncio
    async def test_go_preserves_increments_and_moves_to_go(self, fake_time: _FakeTime) -> None:
        """Only the clocks change between searches; the rest of the TC is untouched."""
        tc = IncrementTimeControl(
            wtime_ms=5000, btime_ms=3000, winc_ms=70, binc_ms=30, moves_to_go=40
        )
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [100])
        black = _TimedEngine(fake_time, _BLACK_PUSHES, [50])
        config = GameConfig(time_control=tc, adjudication=_NO_ADJUDICATION, max_moves=2)

        await _play(config, white, black)

        assert [call.time_control for call in black.calls] == [
            IncrementTimeControl(
                wtime_ms=4970, btime_ms=3000, winc_ms=70, binc_ms=30, moves_to_go=40
            ),
            IncrementTimeControl(
                wtime_ms=4940, btime_ms=2980, winc_ms=70, binc_ms=30, moves_to_go=40
            ),
        ]

    @pytest.mark.asyncio
    async def test_elapsed_accumulates_below_one_millisecond(self, fake_time: _FakeTime) -> None:
        """Sub-millisecond spends add up: nanoseconds are tracked, display is floored.

        Rounding each spend to whole milliseconds would give 1000, 1000, 1000
        (floor) or 999, 998, 997 (round) instead of 999, 998, 998.
        """
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [0.6])
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=1000, btime_ms=1000),
            adjudication=_NO_ADJUDICATION,
            max_moves=3,
        )

        outcome = await _play(config, white, black)

        assert [move.clock_white_ms for move in outcome.moves[::2]] == [999, 998, 998]

    @pytest.mark.parametrize("start_fen", [None, _WHITE_TO_MOVE_FEN], ids=["startpos", "fen"])
    @pytest.mark.asyncio
    async def test_position_command_is_not_charged(
        self, fake_time: _FakeTime, start_fen: str | None
    ) -> None:
        """Timing starts immediately before ``go``, so ``position`` costs nothing."""
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [100], position_spend_ms=400)
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=1000, btime_ms=1000),
            adjudication=_NO_ADJUDICATION,
            start_fen=start_fen,
            time_margin_ms=0,
            max_moves=3,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert [move.clock_white_ms for move in outcome.moves[::2]] == [900, 800, 700]


class TestFlagOnExpectedMove:
    """A side that runs out of time loses on the move the arithmetic predicts."""

    @_FLAG_PATHS
    @pytest.mark.asyncio
    async def test_white_flagged_on_fifth_move(
        self, fake_time: _FakeTime, client_raises: bool
    ) -> None:
        """1000+10 at 250 ms per move: 760, 520, 280, 40, then 250 > 40 + 100."""
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [250], client_raises=client_raises)
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=1000, btime_ms=1000, winc_ms=10, binc_ms=10),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
            max_moves=12,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.BLACK_WIN
        assert len(outcome.moves) == 8
        assert [wtime for wtime, _ in _clocks_sent(white)] == [1000, 760, 520, 280, 40]
        assert [btime for _, btime in _clocks_sent(black)] == [1000, 1010, 1020, 1030]
        assert white.calls[-1].timeout == 0.14

    @_FLAG_PATHS
    @pytest.mark.asyncio
    async def test_black_flagged_on_fifth_move(
        self, fake_time: _FakeTime, client_raises: bool
    ) -> None:
        """Black's clock decides, not White's much larger one."""
        white = _TimedEngine(fake_time, _WHITE_PUSHES)
        black = _TimedEngine(fake_time, _BLACK_PUSHES, [250], client_raises=client_raises)
        config = GameConfig(
            time_control=IncrementTimeControl(
                wtime_ms=9000, btime_ms=1000, winc_ms=500, binc_ms=10
            ),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
            max_moves=12,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.WHITE_WIN
        assert len(outcome.moves) == 9
        assert [btime for _, btime in _clocks_sent(black)] == [1000, 760, 520, 280, 40]

    @_FLAG_PATHS
    @pytest.mark.asyncio
    async def test_black_flagged_when_black_moves_first(
        self, fake_time: _FakeTime, client_raises: bool
    ) -> None:
        """Colour comes from the board, not from ply parity."""
        white = _TimedEngine(fake_time, _WHITE_PUSHES)
        black = _TimedEngine(fake_time, _BLACK_PUSHES, [250], client_raises=client_raises)
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=1000, btime_ms=1000, winc_ms=10, binc_ms=10),
            adjudication=_NO_ADJUDICATION,
            start_fen=_BLACK_TO_MOVE_FEN,
            time_margin_ms=100,
            max_moves=12,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.WHITE_WIN
        assert len(outcome.moves) == 8
        assert [btime for _, btime in _clocks_sent(black)] == [1000, 760, 520, 280, 40]
        assert outcome.start_fen == _BLACK_TO_MOVE_FEN

    @_FLAG_PATHS
    @pytest.mark.asyncio
    async def test_white_flagged_when_black_moves_first(
        self, fake_time: _FakeTime, client_raises: bool
    ) -> None:
        """White is still the one charged when it is the second to move."""
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [250], client_raises=client_raises)
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=1000, btime_ms=1000, winc_ms=10, binc_ms=10),
            adjudication=_NO_ADJUDICATION,
            start_fen=_BLACK_TO_MOVE_FEN,
            time_margin_ms=100,
            max_moves=12,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.BLACK_WIN
        assert len(outcome.moves) == 9
        assert [wtime for wtime, _ in _clocks_sent(white)] == [1000, 760, 520, 280, 40]


class TestIncrement:
    """Each side's own increment is added after each of its completed moves."""

    @pytest.mark.asyncio
    async def test_spend_equal_to_increment_never_flags(self, fake_time: _FakeTime) -> None:
        """Clocks stay put with no margin; swapped increments would flag White at once."""
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [150])
        black = _TimedEngine(fake_time, _BLACK_PUSHES, [20])
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=200, btime_ms=300, winc_ms=150, binc_ms=20),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=0,
            max_moves=16,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert len(outcome.moves) == 32
        assert {move.clock_white_ms for move in outcome.moves} == {200}
        assert {move.clock_black_ms for move in outcome.moves} == {300}
        assert set(_clocks_sent(white)) == {(200, 300)}
        assert set(_clocks_sent(black)) == {(200, 300)}

    @pytest.mark.asyncio
    async def test_each_side_receives_its_own_increment(self, fake_time: _FakeTime) -> None:
        """With instant moves the clocks grow only by each side's own increment."""
        white = _TimedEngine(fake_time, _WHITE_PUSHES)
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=1000, btime_ms=1000, winc_ms=300, binc_ms=7),
            adjudication=_NO_ADJUDICATION,
            max_moves=2,
        )

        outcome = await _play(config, white, black)

        assert [(m.clock_white_ms, m.clock_black_ms) for m in outcome.moves] == [
            (1300, 1000),
            (1300, 1007),
            (1600, 1007),
            (1600, 1014),
        ]


class TestMarginBoundary:
    """The limit is inclusive: exactly at it passes, anything past it flags."""

    @_FLAG_PATHS
    @pytest.mark.asyncio
    async def test_move_exactly_at_limit_is_not_a_flag(
        self, fake_time: _FakeTime, client_raises: bool
    ) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [600], client_raises=client_raises)
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=500, btime_ms=500),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
            max_moves=1,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert len(outcome.moves) == 2
        assert white.calls[0].timeout == 0.6
        assert outcome.moves[0].clock_white_ms == 0

    @pytest.mark.parametrize(
        ("spend_ms", "margin_ms"),
        [(601, 100), (600.000001, 100), (501, 0)],
        ids=["1ms_over", "1ns_over", "1ms_over_no_margin"],
    )
    @pytest.mark.asyncio
    async def test_white_past_limit_flags_after_go_returns(
        self, fake_time: _FakeTime, spend_ms: float, margin_ms: int
    ) -> None:
        """A late move that the client let through still loses and is not recorded."""
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [spend_ms], client_raises=False)
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=500, btime_ms=500),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=margin_ms,
            max_moves=1,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.BLACK_WIN
        assert outcome.moves == []

    @pytest.mark.asyncio
    async def test_late_move_without_info_lines_still_flags(self, fake_time: _FakeTime) -> None:
        """UCI doesn't require ``info``, so the flag can't depend on it."""
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [601], client_raises=False, sends_info=False)
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=500, btime_ms=500),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
            max_moves=1,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.BLACK_WIN

    @pytest.mark.asyncio
    async def test_black_past_limit_flags_after_go_returns(self, fake_time: _FakeTime) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES)
        black = _TimedEngine(fake_time, _BLACK_PUSHES, [601], client_raises=False)
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=9000, btime_ms=500),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
            max_moves=2,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.WHITE_WIN
        assert [move.uci for move in outcome.moves] == [_WHITE_PUSHES[0]]


class _DebtorGame:
    """A game where only the debtor uses time, moving first whichever its colour.

    Mirroring the game like this gives both colours the same expected values,
    while still running each colour's own clock arithmetic.

    Attributes:
        debtor: The engine that runs up the time debt.
        opponent: The engine that moves instantly on a large clock.
        opponent_wins: The result if the debtor loses.
    """

    def __init__(
        self,
        fake_time: _FakeTime,
        *,
        debtor_is_white: bool,
        spends_ms: Sequence[float],
        start_ms: int,
        max_moves: int,
        inc_ms: int = 0,
        client_raises: bool = True,
    ) -> None:
        self._debtor_is_white = debtor_is_white
        self.debtor = _TimedEngine(
            fake_time,
            _WHITE_PUSHES if debtor_is_white else _BLACK_PUSHES,
            spends_ms,
            client_raises=client_raises,
        )
        self.opponent = _TimedEngine(fake_time, _BLACK_PUSHES if debtor_is_white else _WHITE_PUSHES)
        self.opponent_wins = GameResult.BLACK_WIN if debtor_is_white else GameResult.WHITE_WIN
        self._config = GameConfig(
            time_control=IncrementTimeControl(
                wtime_ms=start_ms if debtor_is_white else 10_000,
                btime_ms=10_000 if debtor_is_white else start_ms,
                winc_ms=inc_ms if debtor_is_white else 0,
                binc_ms=0 if debtor_is_white else inc_ms,
            ),
            adjudication=_NO_ADJUDICATION,
            start_fen=None if debtor_is_white else _BLACK_TO_MOVE_FEN,
            time_margin_ms=100,
            max_moves=max_moves,
        )

    async def play(self) -> GameOutcome:
        """Play the game."""
        if self._debtor_is_white:
            return await _play(self._config, self.debtor, self.opponent)
        return await _play(self._config, self.opponent, self.debtor)

    def debtor_clock_sent_to(self, engine: _TimedEngine) -> list[int]:
        """Return the debtor's clock as shown on every ``go`` the engine received."""
        return [wtime if self._debtor_is_white else btime for wtime, btime in _clocks_sent(engine)]

    def debtor_clock_recorded(self, moves: Sequence[Move]) -> list[int | None]:
        """Return the debtor's clock as recorded on each of the given moves."""
        return [
            move.clock_white_ms if self._debtor_is_white else move.clock_black_ms for move in moves
        ]


_DEBTOR_COLOURS = pytest.mark.parametrize("debtor_is_white", [True, False], ids=["white", "black"])


class TestOverrunCarryOver:
    """Time used from the margin stays owed by either colour; it is never forgiven."""

    @_DEBTOR_COLOURS
    @_FLAG_PATHS
    @pytest.mark.asyncio
    async def test_debt_shrinks_the_next_limit(
        self, fake_time: _FakeTime, debtor_is_white: bool, client_raises: bool
    ) -> None:
        """100 - 150 = -50 shows as 0, then 60 > -50 + 100 flags though 60 < margin.

        A clamp that forgave the debt would give the second move a 100 ms limit.
        """
        game = _DebtorGame(
            fake_time,
            debtor_is_white=debtor_is_white,
            spends_ms=[150, 60],
            start_ms=100,
            max_moves=4,
            client_raises=client_raises,
        )

        outcome = await game.play()

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == game.opponent_wins
        assert len(outcome.moves) == 2
        assert game.debtor_clock_recorded(outcome.moves[:1]) == [0]
        assert game.debtor_clock_sent_to(game.opponent) == [0]
        assert game.debtor_clock_sent_to(game.debtor) == [100, 0]
        assert game.debtor.calls[1].timeout == 0.05

    @_DEBTOR_COLOURS
    @pytest.mark.asyncio
    async def test_increment_repays_debt(self, fake_time: _FakeTime, debtor_is_white: bool) -> None:
        """-50 + 30 = -20 shows as 0, then -20 + 30 = 10 shows as 10, not 30."""
        game = _DebtorGame(
            fake_time,
            debtor_is_white=debtor_is_white,
            spends_ms=[150, 0],
            start_ms=100,
            inc_ms=30,
            max_moves=3,
        )

        outcome = await game.play()

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert game.debtor_clock_sent_to(game.debtor) == [100, 0, 10]
        assert _timeouts_sent(game.debtor) == [0.2, 0.08, 0.11]
        assert game.debtor_clock_recorded(outcome.moves[::2]) == [0, 10, 40]

    @_DEBTOR_COLOURS
    @_FLAG_PATHS
    @pytest.mark.asyncio
    async def test_margin_can_be_spent_only_once(
        self, fake_time: _FakeTime, debtor_is_white: bool, client_raises: bool
    ) -> None:
        """Using the whole margin leaves a limit of exactly 0, still sent explicitly."""
        game = _DebtorGame(
            fake_time,
            debtor_is_white=debtor_is_white,
            spends_ms=[200, 1],
            start_ms=100,
            max_moves=4,
            client_raises=client_raises,
        )

        outcome = await game.play()

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == game.opponent_wins
        assert len(outcome.moves) == 2
        assert game.debtor.calls[1].timeout == 0.0


class TestMovetimeLimit:
    """``movetime`` searches get the movetime plus the margin, not 5 s of slack."""

    @pytest.mark.asyncio
    async def test_11s_move_at_movetime_15000_survives(self, fake_time: _FakeTime) -> None:
        """Reproduces the hidden 10 s limit: the move is within its time control."""
        tc = FixedTimeControl(movetime_ms=15_000)
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [11_000])
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(time_control=tc, adjudication=_NO_ADJUDICATION, max_moves=1)

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert len(outcome.moves) == 2
        assert white.calls[0].timeout == 15.1
        assert white.calls[0].time_control == tc
        assert white.calls[0].time_control.to_uci_params() == "movetime 15000"

    @_FLAG_PATHS
    @pytest.mark.asyncio
    async def test_just_past_movetime_limit_flags(
        self, fake_time: _FakeTime, client_raises: bool
    ) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [15_101], client_raises=client_raises)
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=FixedTimeControl(movetime_ms=15_000),
            adjudication=_NO_ADJUDICATION,
            max_moves=1,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.BLACK_WIN
        assert outcome.moves == []
        assert white.calls[0].timeout == 15.1

    @_FLAG_PATHS
    @pytest.mark.asyncio
    async def test_exactly_at_movetime_limit_is_not_a_flag(
        self, fake_time: _FakeTime, client_raises: bool
    ) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [1_250], client_raises=client_raises)
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=FixedTimeControl(movetime_ms=1_000),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=250,
            max_moves=1,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert white.calls[0].timeout == 1.25


_UNBOUNDED_TCS = pytest.mark.parametrize(
    "tc",
    [DepthTimeControl(depth=1), NodesTimeControl(nodes=1000)],
    ids=["depth", "nodes"],
)


class TestUnboundedSearchLimit:
    """``depth`` and ``nodes`` searches get ``move_timeout_ms`` and no margin."""

    @_UNBOUNDED_TCS
    @pytest.mark.asyncio
    async def test_6s_search_survives(self, fake_time: _FakeTime, tc: TimeControl) -> None:
        """Reproduces the 5 s depth/nodes limit with the default of 30 s."""
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [6_000])
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(time_control=tc, adjudication=_NO_ADJUDICATION, max_moves=1)

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert len(outcome.moves) == 2
        assert white.calls[0].timeout == 30.0
        assert white.calls[0].time_control == tc

    @_UNBOUNDED_TCS
    @pytest.mark.asyncio
    async def test_custom_move_timeout_is_honoured(
        self, fake_time: _FakeTime, tc: TimeControl
    ) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [40_000])
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=tc,
            adjudication=_NO_ADJUDICATION,
            move_timeout_ms=45_000,
            max_moves=1,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert white.calls[0].timeout == 45.0

    @_UNBOUNDED_TCS
    @pytest.mark.asyncio
    async def test_margin_does_not_extend_move_timeout(
        self, fake_time: _FakeTime, tc: TimeControl
    ) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES)
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=tc,
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=999,
            max_moves=1,
        )

        await _play(config, white, black)

        assert white.calls[0].timeout == 30.0

    @_UNBOUNDED_TCS
    @_FLAG_PATHS
    @pytest.mark.asyncio
    async def test_past_move_timeout_flags(
        self, fake_time: _FakeTime, tc: TimeControl, client_raises: bool
    ) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [2_001], client_raises=client_raises)
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=tc,
            adjudication=_NO_ADJUDICATION,
            move_timeout_ms=2_000,
            max_moves=1,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.BLACK_WIN
        assert outcome.moves == []
        assert white.calls[0].timeout == 2.0

    @_UNBOUNDED_TCS
    @pytest.mark.asyncio
    async def test_exactly_at_move_timeout_is_not_a_flag(
        self, fake_time: _FakeTime, tc: TimeControl
    ) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [2_000], client_raises=False)
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=tc,
            adjudication=_NO_ADJUDICATION,
            move_timeout_ms=2_000,
            max_moves=1,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert white.calls[0].timeout == 2.0


class TestExplicitTimeoutAlways:
    """No search can reach ``UCIClient.default_timeout``."""

    @pytest.mark.parametrize(
        "tc",
        [
            FixedTimeControl(movetime_ms=100),
            IncrementTimeControl(wtime_ms=1000, btime_ms=1000),
            DepthTimeControl(depth=1),
            NodesTimeControl(nodes=1000),
        ],
        ids=["movetime", "clock", "depth", "nodes"],
    )
    @pytest.mark.parametrize("margin_ms", [0, 100], ids=["no_margin", "margin"])
    @pytest.mark.asyncio
    async def test_every_search_passes_a_timeout(
        self, fake_time: _FakeTime, tc: TimeControl, margin_ms: int
    ) -> None:
        """A margin of 0 is a real value, not a switch that disables the limit."""
        white = _TimedEngine(fake_time, _WHITE_PUSHES)
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=tc,
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=margin_ms,
            max_moves=3,
        )

        await _play(config, white, black)

        assert len(white.calls) == 3
        assert len(black.calls) == 3
        for call in [*white.calls, *black.calls]:
            assert call.timeout is not None
            assert call.timeout > 0

    @pytest.mark.asyncio
    async def test_timeout_falls_with_the_clock(self, fake_time: _FakeTime) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [200])
        black = _TimedEngine(fake_time, _BLACK_PUSHES, [100])
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=1000, btime_ms=800),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
            max_moves=3,
        )

        await _play(config, white, black)

        assert _timeouts_sent(white) == [1.1, 0.9, 0.7]
        assert _timeouts_sent(black) == [0.9, 0.8, 0.7]


class TestClientTimeoutMapping:
    """``UCITimeoutError`` from ``go`` forfeits the game for the mover."""

    @pytest.mark.asyncio
    async def test_white_loses_when_its_search_times_out(self, fake_time: _FakeTime) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [500])
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=300, btime_ms=9000),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
            max_moves=2,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.BLACK_WIN
        assert outcome.moves == []

    @pytest.mark.asyncio
    async def test_black_loses_when_its_search_times_out(self, fake_time: _FakeTime) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES)
        black = _TimedEngine(fake_time, _BLACK_PUSHES, [500])
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=9000, btime_ms=300),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
            max_moves=2,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.WHITE_WIN
        assert [move.uci for move in outcome.moves] == [_WHITE_PUSHES[0]]


class TestMoveTimeoutScope:
    """``move_timeout_ms`` limits ``depth`` and ``nodes`` searches and nothing else."""

    @_FLAG_PATHS
    @pytest.mark.asyncio
    async def test_it_does_not_cap_clock_searches(
        self, fake_time: _FakeTime, client_raises: bool
    ) -> None:
        """A long think on a long clock is legal, which a hidden cap would forfeit."""
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [5_000], client_raises=client_raises)
        black = _TimedEngine(fake_time, _BLACK_PUSHES, [3_000], client_raises=client_raises)
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=60_000, btime_ms=40_000),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
            move_timeout_ms=1_000,
            max_moves=1,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert len(outcome.moves) == 2
        assert white.calls[0].timeout == 60.1
        assert black.calls[0].timeout == 40.1

    @_FLAG_PATHS
    @pytest.mark.asyncio
    async def test_it_does_not_cap_movetime_searches(
        self, fake_time: _FakeTime, client_raises: bool
    ) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [4_000], client_raises=client_raises)
        black = _TimedEngine(fake_time, _BLACK_PUSHES, [4_000], client_raises=client_raises)
        config = GameConfig(
            time_control=FixedTimeControl(movetime_ms=5_000),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
            move_timeout_ms=1_000,
            max_moves=1,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert len(outcome.moves) == 2
        assert white.calls[0].timeout == 5.1
        assert black.calls[0].timeout == 5.1


class TestMeasuredTimeNotReportedTime:
    """Clocks and flags use the time measured around ``go``, not what the engine claims."""

    @pytest.mark.parametrize("reported_ms", [1, 999_999], ids=["under_reported", "over_reported"])
    @pytest.mark.asyncio
    async def test_clock_charges_measured_time(
        self, fake_time: _FakeTime, reported_ms: int
    ) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [300], reported_time_ms=reported_ms)
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=1000, btime_ms=1000),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
            max_moves=2,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert [move.clock_white_ms for move in outcome.moves[::2]] == [700, 400]
        # ``Move.time_ms`` stays the engine's own report; only the clock is measured.
        assert [move.time_ms for move in outcome.moves[::2]] == [reported_ms, reported_ms]

    @pytest.mark.parametrize(
        "tc",
        [
            IncrementTimeControl(wtime_ms=500, btime_ms=500),
            FixedTimeControl(movetime_ms=500),
            DepthTimeControl(depth=1),
        ],
        ids=["clock", "movetime", "depth"],
    )
    @pytest.mark.asyncio
    async def test_over_limit_move_flags_despite_small_reported_time(
        self, fake_time: _FakeTime, tc: TimeControl
    ) -> None:
        """Every limit here is 600 ms. The engine really takes 601 ms but claims 1 ms."""
        white = _TimedEngine(
            fake_time, _WHITE_PUSHES, [601], client_raises=False, reported_time_ms=1
        )
        black = _TimedEngine(fake_time, _BLACK_PUSHES)
        config = GameConfig(
            time_control=tc,
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
            move_timeout_ms=600,
            max_moves=1,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.BLACK_WIN
        assert outcome.moves == []


_MATE_IN_ONE = pytest.mark.parametrize(
    ("fen", "mate", "mover_is_white"),
    [
        ("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4", "h5f7", True),
        ("rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2", "d8h4", False),
    ],
    ids=["white_mates", "black_mates"],
)
_FAULTY_SIDES = pytest.mark.parametrize("faulty_is_white", [True, False], ids=["white", "black"])
# Illegal for either colour: three squares for White's pawn, and not Black's pawn.
_BAD_MOVES = pytest.mark.parametrize("bad_move", ["e2e5", "zzzz"], ids=["illegal", "unparseable"])


def _engines_with_faulty_side(
    fake_time: _FakeTime,
    *,
    faulty_is_white: bool,
    moves: Sequence[str],
    spends_ms: Sequence[float] = (0,),
    client_raises: bool = True,
    crash_on_search: int | None = None,
) -> tuple[_TimedEngine, _TimedEngine]:
    """Return (white, black) where the game turns on the faulty side's first search.

    If Black is the faulty side, White gets one legal move in first.
    """
    faulty = _TimedEngine(
        fake_time, moves, spends_ms, client_raises=client_raises, crash_on_search=crash_on_search
    )
    if faulty_is_white:
        return faulty, _TimedEngine(fake_time, [])
    return _TimedEngine(fake_time, _WHITE_PUSHES), faulty


def _opponent_wins(side_is_white: bool) -> GameResult:
    """Return the result when the given side loses."""
    return GameResult.BLACK_WIN if side_is_white else GameResult.WHITE_WIN


class TestFlagComesBeforeTheMove:
    """A move past the limit is a time loss whatever the move is, so what it does is moot."""

    @_MATE_IN_ONE
    @pytest.mark.asyncio
    async def test_late_mating_move_is_a_time_loss(
        self, fake_time: _FakeTime, fen: str, mate: str, mover_is_white: bool
    ) -> None:
        mover = _TimedEngine(fake_time, [mate], [1_101], client_raises=False)
        idle = _TimedEngine(fake_time, [])
        white, black = (mover, idle) if mover_is_white else (idle, mover)
        config = GameConfig(
            time_control=FixedTimeControl(movetime_ms=1_000),
            adjudication=_NO_ADJUDICATION,
            start_fen=fen,
            time_margin_ms=100,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == _opponent_wins(mover_is_white)
        assert outcome.moves == []

    @_MATE_IN_ONE
    @pytest.mark.asyncio
    async def test_mating_move_at_the_limit_is_checkmate(
        self, fake_time: _FakeTime, fen: str, mate: str, mover_is_white: bool
    ) -> None:
        """The same move and position stand when the move is within the limit."""
        mover = _TimedEngine(fake_time, [mate], [1_100], client_raises=False)
        idle = _TimedEngine(fake_time, [])
        white, black = (mover, idle) if mover_is_white else (idle, mover)
        config = GameConfig(
            time_control=FixedTimeControl(movetime_ms=1_000),
            adjudication=_NO_ADJUDICATION,
            start_fen=fen,
            time_margin_ms=100,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.CHECKMATE
        assert outcome.result == _opponent_wins(not mover_is_white)
        assert [move.uci for move in outcome.moves] == [mate]

    @_FAULTY_SIDES
    @_BAD_MOVES
    @pytest.mark.asyncio
    async def test_late_bad_move_is_a_time_loss(
        self, fake_time: _FakeTime, faulty_is_white: bool, bad_move: str
    ) -> None:
        white, black = _engines_with_faulty_side(
            fake_time,
            faulty_is_white=faulty_is_white,
            moves=[bad_move],
            spends_ms=[1_101],
            client_raises=False,
        )
        config = GameConfig(
            time_control=FixedTimeControl(movetime_ms=1_000),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == _opponent_wins(faulty_is_white)
        assert len(outcome.moves) == (0 if faulty_is_white else 1)


class TestEngineFaultIsNotATimeLoss:
    """A crash or a bad move inside the limit scores as a crash, never as a time loss."""

    @_FAULTY_SIDES
    @pytest.mark.asyncio
    async def test_crash_during_search_is_an_engine_crash(
        self, fake_time: _FakeTime, faulty_is_white: bool
    ) -> None:
        white, black = _engines_with_faulty_side(
            fake_time, faulty_is_white=faulty_is_white, moves=[], crash_on_search=0
        )
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=1000, btime_ms=1000),
            adjudication=_NO_ADJUDICATION,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.ENGINE_CRASH
        assert outcome.result == _opponent_wins(faulty_is_white)
        assert len(outcome.moves) == (0 if faulty_is_white else 1)

    @_FAULTY_SIDES
    @pytest.mark.asyncio
    async def test_crash_between_moves_is_an_engine_crash(
        self, fake_time: _FakeTime, faulty_is_white: bool
    ) -> None:
        """An engine that dies before ``go`` still loses the game.

        If this escaped ``play_game``, the runner would report a failed game and
        never count the loss in the SPRT score.
        """
        white, black = _engines_with_faulty_side(
            fake_time, faulty_is_white=faulty_is_white, moves=[]
        )
        faulty = white if faulty_is_white else black
        faulty.client.position = AsyncMock(side_effect=UCIEngineError("engine is not running"))
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=1000, btime_ms=1000),
            adjudication=_NO_ADJUDICATION,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.ENGINE_CRASH
        assert outcome.result == _opponent_wins(faulty_is_white)
        assert faulty.calls == []

    @_FAULTY_SIDES
    @_BAD_MOVES
    @pytest.mark.asyncio
    async def test_in_time_bad_move_is_an_engine_crash(
        self, fake_time: _FakeTime, faulty_is_white: bool, bad_move: str
    ) -> None:
        """The counterpart of ``test_late_bad_move_is_a_time_loss``, exactly at the limit."""
        white, black = _engines_with_faulty_side(
            fake_time,
            faulty_is_white=faulty_is_white,
            moves=[bad_move],
            spends_ms=[1_100],
            client_raises=False,
        )
        config = GameConfig(
            time_control=FixedTimeControl(movetime_ms=1_000),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
        )

        outcome = await _play(config, white, black)

        assert outcome.termination == TerminationReason.ENGINE_CRASH
        assert outcome.result == _opponent_wins(faulty_is_white)
        assert len(outcome.moves) == (0 if faulty_is_white else 1)


class TestMoveClockFields:
    """``Move.clock_*_ms`` record both clocks after the move, increment included."""

    @pytest.mark.asyncio
    async def test_clock_fields_after_every_move(self, fake_time: _FakeTime) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [100, 200, 300])
        black = _TimedEngine(fake_time, _BLACK_PUSHES, [50, 60, 70])
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=5000, btime_ms=3000, winc_ms=70, binc_ms=30),
            adjudication=_NO_ADJUDICATION,
            max_moves=3,
        )

        outcome = await _play(config, white, black)

        assert [(m.clock_white_ms, m.clock_black_ms) for m in outcome.moves] == [
            (4970, 3000),
            (4970, 2980),
            (4840, 2980),
            (4840, 2950),
            (4610, 2950),
            (4610, 2910),
        ]

    @pytest.mark.asyncio
    async def test_clock_fields_are_floored_to_milliseconds(self, fake_time: _FakeTime) -> None:
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [0.4])
        black = _TimedEngine(fake_time, _BLACK_PUSHES, [0.1])
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=1000, btime_ms=2000),
            adjudication=_NO_ADJUDICATION,
            max_moves=1,
        )

        outcome = await _play(config, white, black)

        assert outcome.moves[0].clock_white_ms == 999
        assert outcome.moves[0].clock_black_ms == 2000
        assert outcome.moves[1].clock_black_ms == 1999
        assert _clocks_sent(black) == [(999, 2000)]

    @pytest.mark.parametrize(
        "tc",
        [
            FixedTimeControl(movetime_ms=100),
            DepthTimeControl(depth=1),
            NodesTimeControl(nodes=1000),
        ],
        ids=["movetime", "depth", "nodes"],
    )
    @pytest.mark.asyncio
    async def test_non_clock_games_record_no_clocks(
        self, fake_time: _FakeTime, tc: TimeControl
    ) -> None:
        """Time passes and limits apply, but there is no clock to report."""
        white = _TimedEngine(fake_time, _WHITE_PUSHES, [50])
        black = _TimedEngine(fake_time, _BLACK_PUSHES, [50])
        config = GameConfig(time_control=tc, adjudication=_NO_ADJUDICATION, max_moves=2)

        outcome = await _play(config, white, black)

        assert white.calls[0].timeout is not None
        assert len(outcome.moves) == 4
        assert {move.clock_white_ms for move in outcome.moves} == {None}
        assert {move.clock_black_ms for move in outcome.moves} == {None}
