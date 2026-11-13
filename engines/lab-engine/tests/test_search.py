"""Tests for lab_engine.search: iterative-deepening alpha-beta.

The searches here run in the searcher's own thread, as in the engine. That
needs no subprocess, so these are unit tests.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest
from lab_engine.movegen import MAX_MOVES, NO_MOVE, generate_moves
from lab_engine.position import START_FEN, Position, move_to_uci
from lab_engine.search import MATE, MAX_DEPTH, Iteration, Searcher, SearchLimits, order_moves

from tests.fen_tools import mirror_fen
from tests.perft_table import PERFT_POSITIONS

KIWIPETE = PERFT_POSITIONS[1].fen

# Positions checked with python-chess before use.
MATE_IN_ONE = "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1"  # Ra8#
MATE_IN_TWO = "k7/8/2K5/8/8/8/8/7R w - - 0 1"  # Kc7 or Kb6, then Rh8#
MATED_IN_ONE = "k7/2K5/8/8/8/8/8/7R b - - 0 1"  # Ka7 is forced, then Ra1#
HANGING_QUEEN = "4k3/8/8/3q4/4P3/8/8/4K3 w - - 0 1"
CHECKMATED = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
STALEMATED = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"
# Qf8 mates; Qc4 and Qf7, among others, stalemate.
MATE_OR_STALEMATE = "7k/8/6K1/8/8/8/8/5Q2 w - - 0 1"
# White can take a queen with a pawn or a knight, a rook or a pawn with the
# knight, or promote; everything else is quiet.
CAPTURES = "4k3/2P5/8/1r1q4/p3P3/2N5/8/4K3 w - - 0 1"

FULL_TREE_TO_DEPTH_4 = 1 + 20 + 400 + 8_902 + 197_281
"""Nodes in the whole tree four plies deep from the start: minimax visits them all."""

WAIT_SECONDS = 10.0
"""The longest any test waits for a search; a search still running is a failure."""


def _search(fen: str, limits: SearchLimits) -> tuple[int, list[Iteration]]:
    """Search ``fen`` to completion; return the best move and every iteration."""
    iterations: list[Iteration] = []
    best = Searcher().search(Position.from_fen(fen), limits, iterations.append)
    return best, iterations


def _move(fen: str, text: str) -> int:
    """The legal move ``text`` (UCI notation) in ``fen``."""
    return next(m for m in Position.from_fen(fen).legal_moves() if move_to_uci(m) == text)


def _assert_legal(fen: str, line: tuple[int, ...]) -> None:
    """Assert that ``line`` is a sequence of legal moves from ``fen``."""
    position = Position.from_fen(fen)
    for move in line:
        assert move in position.legal_moves(), move_to_uci(move)
        position.push(move)


class _Recorder:
    """Collects a background search's reports and its result."""

    def __init__(self) -> None:
        self.iterations: list[Iteration] = []
        self.best: list[int] = []
        self.finished = threading.Event()

    def report(self, iteration: Iteration) -> None:
        assert not self.finished.is_set(), "reported after the search finished"
        self.iterations.append(iteration)

    def done(self, move: int) -> None:
        self.best.append(move)
        self.finished.set()


class TestBestMove:
    """The search finds the best move at its depth."""

    def test_mates_in_one(self) -> None:
        best, iterations = _search(MATE_IN_ONE, SearchLimits(depth=2))
        assert move_to_uci(best) == "a1a8"
        assert iterations[-1].score == MATE - 1

    def test_mates_in_two(self) -> None:
        best, iterations = _search(MATE_IN_TWO, SearchLimits(depth=4))
        assert move_to_uci(best) in {"c6c7", "c6b6"}
        assert iterations[-1].score == MATE - 3

    def test_sees_that_it_is_mated(self) -> None:
        best, iterations = _search(MATED_IN_ONE, SearchLimits(depth=3))
        assert move_to_uci(best) == "a8a7"
        assert iterations[-1].score == -(MATE - 2)

    def test_mates_rather_than_stalemates(self) -> None:
        best, iterations = _search(MATE_OR_STALEMATE, SearchLimits(depth=2))
        assert move_to_uci(best) == "f1f8"
        assert iterations[-1].score == MATE - 1

    def test_takes_a_hanging_queen(self) -> None:
        best, _ = _search(HANGING_QUEEN, SearchLimits(depth=2))
        assert move_to_uci(best) == "e4d5"

    def test_scores_from_the_side_to_moves_point_of_view(self) -> None:
        _, iterations = _search("3qk3/8/8/8/8/8/8/4K3 b - - 0 1", SearchLimits(depth=2))
        assert iterations[-1].score > 500
        _, iterations = _search("3qk3/8/8/8/8/8/8/4K3 w - - 0 1", SearchLimits(depth=2))
        assert iterations[-1].score < -500

    @pytest.mark.parametrize("fen", [START_FEN, KIWIPETE, PERFT_POSITIONS[5].fen])
    def test_a_mirrored_position_gets_the_same_score(self, fen: str) -> None:
        _, iterations = _search(fen, SearchLimits(depth=3))
        _, mirrored = _search(mirror_fen(fen), SearchLimits(depth=3))
        assert [i.score for i in mirrored] == [i.score for i in iterations]

    @pytest.mark.parametrize("fen", [CHECKMATED, STALEMATED])
    def test_finds_no_move_when_there_is_none(self, fen: str) -> None:
        best, iterations = _search(fen, SearchLimits(depth=3))
        assert best == NO_MOVE
        assert iterations == []

    def test_leaves_the_position_unchanged(self) -> None:
        position = Position.from_fen(KIWIPETE)
        Searcher().search(position, SearchLimits(depth=3))
        assert position.fen() == Position.from_fen(KIWIPETE).fen()


class TestIterations:
    """Each completed depth is reported, deepest last."""

    def test_reports_each_depth_in_turn(self) -> None:
        _, iterations = _search(START_FEN, SearchLimits(depth=4))
        assert [i.depth for i in iterations] == [1, 2, 3, 4]

    def test_each_line_is_legal_and_starts_with_the_best_move(self) -> None:
        best, iterations = _search(KIWIPETE, SearchLimits(depth=4))
        for iteration in iterations:
            assert len(iteration.pv) == iteration.depth
            _assert_legal(KIWIPETE, iteration.pv)
        assert iterations[-1].pv[0] == best

    def test_nodes_and_time_accumulate(self) -> None:
        _, iterations = _search(KIWIPETE, SearchLimits(depth=4))
        nodes = [i.nodes for i in iterations]
        times = [i.time_ms for i in iterations]
        assert nodes == sorted(nodes)
        assert len(set(nodes)) == len(nodes)
        assert times == sorted(times)
        assert times[0] >= 0

    def test_alpha_beta_prunes(self) -> None:
        _, iterations = _search(START_FEN, SearchLimits(depth=4))
        assert iterations[-1].nodes < FULL_TREE_TO_DEPTH_4 // 10

    @pytest.mark.parametrize(
        ("fen", "found_at"),
        [
            # Giving mate in one shows at depth 2, when the mated side's move
            # generation finds no move; being mated after one reply, at depth 3.
            (MATE_IN_ONE, 2),
            (MATED_IN_ONE, 3),
        ],
    )
    def test_a_forced_mate_for_either_side_ends_the_search(self, fen: str, found_at: int) -> None:
        # The node limit only stops a search that ignores the mate.
        _, iterations = _search(fen, SearchLimits(depth=40, nodes=3_000_000))
        assert iterations[-1].depth == found_at
        assert abs(iterations[-1].score) >= MATE - found_at


class TestLimits:
    """Node, depth and time limits each end the search."""

    def test_a_node_limit_ends_an_otherwise_endless_search(self) -> None:
        searcher = Searcher()
        for _ in range(2):  # the count starts again for each search
            iterations: list[Iteration] = []
            best = searcher.search(Position.start(), SearchLimits(nodes=5_000), iterations.append)
            assert best in Position.start().legal_moves()
            assert all(i.nodes <= 5_000 for i in iterations)
            assert len(iterations) >= 2

    def test_depth_one_always_completes(self) -> None:
        best, iterations = _search(KIWIPETE, SearchLimits(nodes=1))
        assert [i.depth for i in iterations] == [1]
        assert best in Position.from_fen(KIWIPETE).legal_moves()

    def test_a_depth_cut_short_is_not_reported(self) -> None:
        # Black is a queen up, so every completed depth scores well above 0,
        # the score an abandoned depth returns.
        _, iterations = _search("3qk3/8/8/8/8/8/8/4K3 b - - 0 1", SearchLimits(nodes=20_000))
        assert [i.depth for i in iterations] == list(range(1, len(iterations) + 1))
        assert all(i.score > 500 for i in iterations)

    @pytest.mark.parametrize("soft_ms", [5, 10, 20, 40, 80])
    def test_no_depth_starts_after_the_soft_limit(self, soft_ms: int) -> None:
        # Together the limits cover 5 to 160 ms, so some depth ends inside
        # [soft_ms, 2 * soft_ms) for one of them, wherever the depths end.
        _, iterations = _search(KIWIPETE, SearchLimits(soft_ms=soft_ms, nodes=20_000_000))
        assert all(i.time_ms < soft_ms for i in iterations[:-1])
        assert iterations[-1].time_ms >= soft_ms

    def test_the_soft_limit_stops_between_depths(self) -> None:
        # The node limit only stops a search that ignores the soft limit.
        _, iterations = _search(KIWIPETE, SearchLimits(soft_ms=0, nodes=3_000_000))
        assert [i.depth for i in iterations] == [1]

    def test_the_hard_limit_cuts_a_depth_short(self) -> None:
        start = time.perf_counter()
        best, iterations = _search(KIWIPETE, SearchLimits(hard_ms=100, nodes=50_000_000))
        elapsed = time.perf_counter() - start
        assert 0.1 <= elapsed < 1.0
        assert iterations
        assert best == iterations[-1].pv[0]

    def test_a_search_that_ends_early_does_not_wait_for_its_hard_limit(self) -> None:
        start = time.perf_counter()
        _search(KIWIPETE, SearchLimits(depth=1, hard_ms=5_000))
        assert time.perf_counter() - start < 1.0

    def test_nodes_count_every_position_searched_since_go(self) -> None:
        # Black's only move is Ka7. Depth 1 searches the root and that one
        # move. Depth 2 searches them again and, with no bound to cut on,
        # every reply; the count goes on from depth 1's.
        after_ka7 = Position.from_fen(MATED_IN_ONE)
        after_ka7.push_uci("a8a7")
        replies = len(after_ka7.legal_moves())
        _, iterations = _search(MATED_IN_ONE, SearchLimits(depth=2))
        assert [i.nodes for i in iterations] == [2, 2 + 2 + replies]

    def test_the_node_limit_counts_every_depth(self) -> None:
        _, to_depth_3 = _search(START_FEN, SearchLimits(depth=3))
        budget = to_depth_3[-1].nodes + 1
        _, iterations = _search(START_FEN, SearchLimits(nodes=budget))
        assert [i.depth for i in iterations] == [1, 2, 3]
        assert [i.nodes for i in iterations] == [i.nodes for i in to_depth_3]

    @pytest.mark.parametrize(
        "limits",
        [
            {"depth": 0},
            {"depth": MAX_DEPTH + 1},
            {"nodes": 0},
            {"soft_ms": -1},
            {"hard_ms": -1},
        ],
    )
    def test_limits_out_of_range_are_refused(self, limits: dict[str, int]) -> None:
        # The search's arrays hold MAX_DEPTH plies, so a deeper search would
        # write past them.
        with pytest.raises(ValueError, match="must be"):
            SearchLimits(
                depth=limits.get("depth"),
                nodes=limits.get("nodes"),
                soft_ms=limits.get("soft_ms"),
                hard_ms=limits.get("hard_ms"),
            )

    @pytest.mark.parametrize(
        "other", [{"depth": 3}, {"nodes": 10}, {"soft_ms": 0}, {"hard_ms": 100}]
    )
    def test_infinite_takes_no_other_limit(self, other: dict[str, int]) -> None:
        with pytest.raises(ValueError, match="must be"):
            SearchLimits(infinite=True, **other)

    def test_the_deepest_depth_and_zero_times_are_accepted(self) -> None:
        limits = SearchLimits(depth=MAX_DEPTH, nodes=1, soft_ms=0, hard_ms=0)
        assert limits.depth == MAX_DEPTH


class TestStopping:
    """``stop`` ends a running search, from any thread."""

    def test_stop_ends_an_unlimited_search(self) -> None:
        searcher = Searcher()
        recorder = _Recorder()
        searcher.start(Position.start(), SearchLimits(), recorder.report, recorder.done)
        time.sleep(0.2)
        assert searcher.is_searching
        searcher.stop()
        assert searcher.wait(WAIT_SECONDS)
        assert recorder.best[0] in Position.start().legal_moves()
        assert recorder.iterations
        assert recorder.best == [recorder.iterations[-1].pv[0]]

    def test_a_stop_right_after_start_is_not_lost(self) -> None:
        searcher = Searcher()
        recorder = _Recorder()
        searcher.start(Position.start(), SearchLimits(), recorder.report, recorder.done)
        searcher.stop()
        assert searcher.wait(WAIT_SECONDS)
        assert recorder.best[0] in Position.start().legal_moves()

    def test_a_stop_before_the_search_thread_runs_is_not_lost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Hold the search thread back until stop() has been called, which is
        # what a GUI sending go and stop together can do.
        gate = threading.Event()
        real_thread = threading.Thread

        class GatedThread(real_thread):
            def run(self) -> None:
                gate.wait(WAIT_SECONDS)
                super().run()

        monkeypatch.setattr(threading, "Thread", GatedThread)
        searcher = Searcher()
        recorder = _Recorder()
        searcher.start(Position.start(), SearchLimits(), recorder.report, recorder.done)
        searcher.stop()
        gate.set()
        assert searcher.wait(WAIT_SECONDS)
        assert recorder.best[0] in Position.start().legal_moves()

    def test_a_late_stop_does_not_cut_the_next_search_short(self) -> None:
        searcher = Searcher()
        searcher.search(Position.start(), SearchLimits(depth=1))
        searcher.stop()
        iterations: list[Iteration] = []
        searcher.search(Position.start(), SearchLimits(depth=3), iterations.append)
        assert [i.depth for i in iterations] == [1, 2, 3]

    @pytest.mark.parametrize("fen", [MATE_IN_ONE, CHECKMATED])
    def test_infinite_holds_its_move_until_stop(self, fen: str) -> None:
        # UCI's go infinite: don't stop before stop, even with a mate found or
        # no move to play.
        searcher = Searcher()
        recorder = _Recorder()
        limits = SearchLimits(infinite=True)
        searcher.start(Position.from_fen(fen), limits, recorder.report, recorder.done)
        assert not searcher.wait(0.5)
        assert recorder.best == []
        searcher.stop()
        assert searcher.wait(WAIT_SECONDS)
        expected = NO_MOVE if fen == CHECKMATED else _move(fen, "a1a8")
        assert recorder.best == [expected]

    def test_infinite_holds_again_on_a_searcher_that_was_stopped(self) -> None:
        searcher = Searcher()
        for _ in range(2):
            recorder = _Recorder()
            searcher.start(
                Position.from_fen(MATE_IN_ONE),
                SearchLimits(infinite=True),
                recorder.report,
                recorder.done,
            )
            assert not searcher.wait(0.5)
            searcher.stop()
            assert searcher.wait(WAIT_SECONDS)
            assert recorder.best == [_move(MATE_IN_ONE, "a1a8")]

    def test_no_limits_without_infinite_ends_at_a_mate(self) -> None:
        # Only go infinite holds its move. A go whose limits the engine didn't
        # understand still answers once a forced mate is found.
        searcher = Searcher()
        recorder = _Recorder()
        searcher.start(
            Position.from_fen(MATE_IN_ONE), SearchLimits(), recorder.report, recorder.done
        )
        assert searcher.wait(2.0)
        assert recorder.best == [_move(MATE_IN_ONE, "a1a8")]

    def test_a_time_limit_alone_does_not_wait_for_stop(self) -> None:
        searcher = Searcher()
        recorder = _Recorder()
        searcher.start(
            Position.from_fen(MATE_IN_ONE),
            SearchLimits(hard_ms=5_000),
            recorder.report,
            recorder.done,
        )
        assert searcher.wait(2.0)
        assert recorder.best == [_move(MATE_IN_ONE, "a1a8")]

    def test_a_search_cut_short_does_not_shorten_the_next(self) -> None:
        searcher = Searcher()
        recorder = _Recorder()
        searcher.start(Position.from_fen(KIWIPETE), SearchLimits(), recorder.report, recorder.done)
        time.sleep(0.2)
        searcher.stop()
        assert searcher.wait(WAIT_SECONDS)
        iterations: list[Iteration] = []
        searcher.search(Position.start(), SearchLimits(depth=3), iterations.append)
        assert [i.depth for i in iterations] == [1, 2, 3]

    def test_only_one_search_runs_at_a_time(self) -> None:
        searcher = Searcher()
        recorder = _Recorder()
        searcher.start(Position.start(), SearchLimits(), recorder.report, recorder.done)
        with pytest.raises(RuntimeError, match="already"):
            searcher.start(Position.start(), SearchLimits(), recorder.report, recorder.done)
        searcher.stop()
        assert searcher.wait(WAIT_SECONDS)
        assert not searcher.is_searching

    def test_done_comes_once_after_every_report(self) -> None:
        searcher = Searcher()
        recorder = _Recorder()
        searcher.start(Position.start(), SearchLimits(depth=3), recorder.report, recorder.done)
        assert searcher.wait(WAIT_SECONDS)
        assert recorder.finished.is_set()
        assert len(recorder.best) == 1
        assert [i.depth for i in recorder.iterations] == [1, 2, 3]

    def test_wait_without_a_search_returns_at_once(self) -> None:
        assert Searcher().wait(0.0)

    # The faked exit ends the search thread with SystemExit, which pytest reports.
    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    @pytest.mark.parametrize("error", [ValueError, IndexError, ZeroDivisionError])
    def test_a_failed_search_ends_the_process(
        self, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
    ) -> None:
        exits: list[int] = []

        def fail(*_: object) -> int:
            raise error("boom")

        def fake_exit(code: int) -> None:
            exits.append(code)
            raise SystemExit(code)

        monkeypatch.setattr(Searcher, "_search", fail)
        monkeypatch.setattr("os._exit", fake_exit)
        searcher = Searcher()
        recorder = _Recorder()
        searcher.start(Position.start(), SearchLimits(), recorder.report, recorder.done)
        assert searcher.wait(WAIT_SECONDS)
        assert exits == [1]
        assert recorder.best == []


class TestOrderMoves:
    """Moves are tried PV move first, then captures and promotions, then the rest."""

    def _ordered(self, fen: str, pv_move: str | None = None) -> list[str]:
        position = Position.from_fen(fen)
        moves = np.zeros(MAX_MOVES, dtype=np.int64)
        keys = np.zeros(MAX_MOVES, dtype=np.int64)
        count = generate_moves(position.board, position.state, moves)
        by_name = {move_to_uci(int(m)): int(m) for m in moves[:count]}
        first = NO_MOVE if pv_move is None else by_name[pv_move]
        order_moves(position.board, moves, keys, count, first)
        return [move_to_uci(int(m)) for m in moves[:count]]

    def test_most_valuable_victim_then_least_valuable_attacker_then_promotions(self) -> None:
        ordered = self._ordered(CAPTURES)
        assert ordered[:8] == [
            "e4d5",  # pawn takes queen
            "c3d5",  # knight takes queen
            "c3b5",  # knight takes rook
            "c7c8q",  # a queen is worth more than the next capture
            "c3a4",  # knight takes pawn
            "c7c8r",
            "c7c8b",
            "c7c8n",
        ]

    def test_quiet_moves_keep_their_generated_order(self) -> None:
        position = Position.from_fen(CAPTURES)
        moves = np.zeros(MAX_MOVES, dtype=np.int64)
        count = generate_moves(position.board, position.state, moves)
        generated = [move_to_uci(int(m)) for m in moves[:count]]
        ordered = self._ordered(CAPTURES)
        quiet = [m for m in generated if m not in ordered[:8]]
        assert ordered[8:] == quiet

    def test_the_previous_depths_move_comes_first(self) -> None:
        ordered = self._ordered(CAPTURES, pv_move="e1f1")
        assert ordered[0] == "e1f1"
        assert ordered[1:4] == ["e4d5", "c3d5", "c3b5"]

    def test_en_passant_counts_as_taking_a_pawn(self) -> None:
        ordered = self._ordered("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
        assert ordered[0] == "e5d6"
