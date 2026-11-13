"""Tests for lab_engine.uci: the UCI protocol, driven in-process.

``UciSession`` takes one command line at a time and writes its answers
through a callback, so these tests feed it lines and read what it wrote.
Searches run in the session's background thread, as in the engine.
"""

from __future__ import annotations

import io
import time
from unittest.mock import patch

import pytest
from lab_engine import board as b
from lab_engine.position import START_FEN, Position
from lab_engine.search import MATE, MAX_DEPTH, Iteration, SearchLimits
from lab_engine.timeman import clock_budget, movetime_budget
from lab_engine.uci import (
    ENGINE_AUTHOR,
    ENGINE_NAME,
    GoCommand,
    UciSession,
    format_info,
    format_score,
    main,
    parse_go,
    search_limits,
)

from tests.test_search import MATE_IN_ONE, MATED_IN_ONE, WAIT_SECONDS

CHECKMATED = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"


class _Engine:
    """A session that records what it writes."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.session = UciSession(self.lines.append)

    def send(self, *commands: str) -> list[str]:
        """Send commands, wait for any search to finish, and return all output."""
        for command in commands:
            assert self.session.handle(command), command
        assert self.session.wait(WAIT_SECONDS), "the search didn't finish"
        return self.lines

    def bestmoves(self) -> list[str]:
        return [line.split()[1] for line in self.lines if line.startswith("bestmove ")]

    def infos(self) -> list[dict[str, str]]:
        return [_info_fields(line) for line in self.lines if line.startswith("info ")]


def _info_fields(line: str) -> dict[str, str]:
    """Split an info line into its fields. ``score`` keeps its type: ``"cp 35"``."""
    tokens = line.split()[1:]
    fields: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        key = tokens[i]
        if key == "pv":
            fields["pv"] = " ".join(tokens[i + 1 :])
            break
        width = 2 if key == "score" else 1
        fields[key] = " ".join(tokens[i + 1 : i + 1 + width])
        i += 1 + width
    return fields


def _legal(fen: str, moves: str = "") -> set[str]:
    """The legal moves, in UCI notation, after playing ``moves`` from ``fen``."""
    from lab_engine.position import move_to_uci

    position = Position.from_fen(fen)
    for move in moves.split():
        position.push_uci(move)
    return {move_to_uci(m) for m in position.legal_moves()}


class TestHandshake:
    """``uci`` and ``isready``."""

    def test_uci_names_the_engine_then_says_uciok(self) -> None:
        assert _Engine().send("uci") == [
            f"id name {ENGINE_NAME}",
            f"id author {ENGINE_AUTHOR}",
            "uciok",
        ]

    def test_isready_says_readyok(self) -> None:
        assert _Engine().send("uci", "isready")[-1] == "readyok"

    def test_isready_is_answered_during_a_search(self) -> None:
        engine = _Engine()
        engine.session.handle("go infinite")
        engine.session.handle("isready")
        assert "readyok" in engine.lines
        assert engine.bestmoves() == []
        engine.send("stop")
        assert len(engine.bestmoves()) == 1


class TestPositionAndGo:
    """``position`` sets the board, and ``go`` searches it."""

    def test_go_from_the_start(self) -> None:
        engine = _Engine()
        engine.send("position startpos", "go depth 2")
        assert engine.bestmoves()[0] in _legal(START_FEN)

    def test_go_after_moves(self) -> None:
        engine = _Engine()
        engine.send("position startpos moves e2e4 e7e5 g1f3", "go depth 2")
        assert engine.bestmoves()[0] in _legal(START_FEN, "e2e4 e7e5 g1f3")

    def test_go_from_a_fen_with_moves(self) -> None:
        fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
        engine = _Engine()
        engine.send(f"position fen {fen} moves e1g1", "go depth 2")
        assert engine.bestmoves()[0] in _legal(fen, "e1g1")

    def test_each_position_replaces_the_last(self) -> None:
        engine = _Engine()
        engine.send("position startpos moves e2e4", "position startpos", "go depth 1")
        assert engine.bestmoves()[0] in _legal(START_FEN)

    def test_ucinewgame_starts_again_from_the_initial_position(self) -> None:
        engine = _Engine()
        engine.send("position startpos moves e2e4", "ucinewgame", "go depth 1")
        assert engine.bestmoves()[0] in _legal(START_FEN)

    def test_go_with_no_position_searches_the_start(self) -> None:
        engine = _Engine()
        engine.send("go depth 1")
        assert engine.bestmoves()[0] in _legal(START_FEN)

    def test_a_bad_fen_leaves_the_position_as_it_was(self) -> None:
        engine = _Engine()
        engine.send("position startpos moves e2e4", "position fen not a fen", "go depth 1")
        assert engine.bestmoves()[0] in _legal(START_FEN, "e2e4")

    def test_an_illegal_move_leaves_the_position_as_it_was(self) -> None:
        engine = _Engine()
        engine.send("position startpos moves e2e4", "position startpos moves e2e4 e2e4")
        engine.send("go depth 1")
        assert engine.bestmoves()[0] in _legal(START_FEN, "e2e4")

    @pytest.mark.parametrize("command", ["position", "position nonsense", "position fen"])
    def test_a_malformed_position_leaves_the_position_as_it_was(self, command: str) -> None:
        engine = _Engine()
        engine.send("position startpos moves d2d4", command, "go depth 1")
        assert engine.bestmoves()[0] in _legal(START_FEN, "d2d4")

    def test_no_legal_move_gives_bestmove_0000(self) -> None:
        engine = _Engine()
        engine.send(f"position fen {CHECKMATED}", "go depth 3")
        assert engine.lines == ["bestmove 0000"]

    def test_the_search_does_not_change_the_position(self) -> None:
        engine = _Engine()
        engine.send("position startpos moves e2e4", "go depth 3", "go depth 1")
        assert len(engine.bestmoves()) == 2
        assert all(m in _legal(START_FEN, "e2e4") for m in engine.bestmoves())


class TestLimits:
    """Each kind of ``go`` limit ends the search."""

    def test_depth(self) -> None:
        engine = _Engine()
        engine.send("go depth 3")
        assert [int(info["depth"]) for info in engine.infos()] == [1, 2, 3]

    def test_nodes(self) -> None:
        engine = _Engine()
        engine.send("go nodes 3000")
        assert engine.infos()
        assert all(int(info["nodes"]) <= 3000 for info in engine.infos())

    def test_movetime(self) -> None:
        engine = _Engine()
        start = time.perf_counter()
        engine.send("go movetime 200")
        assert time.perf_counter() - start < 1.0
        assert len(engine.bestmoves()) == 1

    def test_clocks(self) -> None:
        engine = _Engine()
        start = time.perf_counter()
        engine.send("position startpos moves e2e4", "go wtime 900 btime 900 winc 10 binc 10")
        assert time.perf_counter() - start < 1.0
        assert engine.bestmoves()[0] in _legal(START_FEN, "e2e4")

    @pytest.mark.parametrize("command", ["go mate 1", "go movestogo 5", "go btime 1000"])
    def test_a_go_that_is_not_infinite_answers_at_a_mate(self, command: str) -> None:
        engine = _Engine()
        engine.send(f"position fen {MATE_IN_ONE}", command)
        assert engine.bestmoves() == ["a1a8"]

    def test_go_on_its_own_waits_for_stop(self) -> None:
        engine = _Engine()
        engine.session.handle(f"position fen {MATE_IN_ONE}")
        engine.session.handle("go")
        assert not engine.session.wait(0.5)
        engine.send("stop")
        assert engine.bestmoves() == ["a1a8"]

    def test_infinite_waits_for_stop_even_after_finding_mate(self) -> None:
        engine = _Engine()
        engine.session.handle(f"position fen {MATE_IN_ONE}")
        engine.session.handle("go infinite")
        assert not engine.session.wait(0.5)
        assert engine.bestmoves() == []
        engine.send("stop")
        assert engine.bestmoves() == ["a1a8"]

    def test_infinite_runs_until_stop(self) -> None:
        engine = _Engine()
        engine.session.handle("go infinite")
        time.sleep(0.3)
        assert engine.bestmoves() == []
        engine.send("stop")
        assert len(engine.bestmoves()) == 1


class TestInfo:
    """Each completed depth is reported with score, depth, nodes, time and PV."""

    def test_every_depth_reports_its_fields_and_pv_comes_last(self) -> None:
        engine = _Engine()
        engine.send("go depth 3")
        lines = [line for line in engine.lines if line.startswith("info ")]
        assert len(lines) == 3
        for line in lines:
            fields = _info_fields(line)
            assert {"depth", "score", "nodes", "nps", "time", "pv"} <= set(fields)
            assert line.split()[-1 - int(fields["depth"])] == "pv"

    def test_infos_come_before_bestmove(self) -> None:
        engine = _Engine()
        engine.send("go depth 2")
        assert engine.lines[-1].startswith("bestmove ")
        assert all(line.startswith("info ") for line in engine.lines[:-1])

    def test_the_pv_starts_with_the_bestmove(self) -> None:
        engine = _Engine()
        engine.send("go depth 3")
        assert engine.infos()[-1]["pv"].split()[0] == engine.bestmoves()[0]

    def test_the_score_is_from_the_engines_point_of_view(self) -> None:
        engine = _Engine()
        engine.send("position fen 3qk3/8/8/8/8/8/8/4K3 b - - 0 1", "go depth 2")
        kind, value = engine.infos()[-1]["score"].split()
        assert kind == "cp"
        assert int(value) > 500

    def test_a_mate_it_gives_is_positive_moves(self) -> None:
        engine = _Engine()
        engine.send(f"position fen {MATE_IN_ONE}", "go depth 3")
        assert engine.infos()[-1]["score"] == "mate 1"
        assert engine.bestmoves() == ["a1a8"]

    def test_a_mate_it_gets_is_negative_moves(self) -> None:
        engine = _Engine()
        engine.send(f"position fen {MATED_IN_ONE}", "go depth 3")
        assert engine.infos()[-1]["score"] == "mate -1"


class TestFormatting:
    """Scores and info lines in UCI's form."""

    @pytest.mark.parametrize(
        ("score", "text"),
        [
            (0, "cp 0"),
            (35, "cp 35"),
            (-120, "cp -120"),
            (MATE - 1, "mate 1"),
            (MATE - 3, "mate 2"),
            (MATE - 9, "mate 5"),
            (-(MATE - 2), "mate -1"),
            (-(MATE - 4), "mate -2"),
            (MATE - MAX_DEPTH, f"mate {(MAX_DEPTH + 1) // 2}"),
            (MATE - MAX_DEPTH - 1, f"mate {(MAX_DEPTH + 2) // 2}"),
            (-(MATE - MAX_DEPTH - 1), f"mate -{(MAX_DEPTH + 1) // 2}"),
            (MATE - 2 * MAX_DEPTH, f"cp {MATE - 2 * MAX_DEPTH}"),
        ],
    )
    def test_format_score(self, score: int, text: str) -> None:
        assert format_score(score) == text

    def test_format_info(self) -> None:
        start = Position.start()
        e2e4 = _move(start, "e2e4")
        e7e5 = _move(_after(start, "e2e4"), "e7e5")
        info = format_info(Iteration(depth=2, score=15, nodes=1234, time_ms=50, pv=(e2e4, e7e5)))
        assert info == "info depth 2 score cp 15 nodes 1234 nps 24680 time 50 pv e2e4 e7e5"

    def test_format_info_at_zero_milliseconds(self) -> None:
        e2e4 = _move(Position.start(), "e2e4")
        info = format_info(Iteration(depth=1, score=0, nodes=20, time_ms=0, pv=(e2e4,)))
        assert info == "info depth 1 score cp 0 nodes 20 nps 20000 time 0 pv e2e4"


def _move(position: Position, text: str) -> int:
    from lab_engine.position import move_to_uci

    return next(m for m in position.legal_moves() if move_to_uci(m) == text)


def _after(position: Position, text: str) -> Position:
    copy = position.copy()
    copy.push_uci(text)
    return copy


def _go(command: str) -> GoCommand:
    return parse_go(command.split())


class TestParseGo:
    """``go``'s parameters."""

    def test_reads_every_limit(self) -> None:
        go = _go("go wtime 1 btime 2 winc 3 binc 4 movestogo 5 movetime 6 depth 7 nodes 8")
        assert go == GoCommand(
            wtime=1, btime=2, winc=3, binc=4, movestogo=5, movetime=6, depth=7, nodes=8
        )

    def test_reads_infinite(self) -> None:
        assert parse_go(["go", "infinite"]) == GoCommand(infinite=True)

    def test_go_on_its_own_means_infinite(self) -> None:
        assert parse_go(["go"]) == GoCommand(infinite=True)

    def test_reads_mate(self) -> None:
        assert _go("go mate 3") == GoCommand(mate=3)

    def test_skips_what_it_does_not_know_and_values_that_are_not_numbers(self) -> None:
        go = _go("go ponder searchmoves e2e4 depth x nodes 9 mate y movetime")
        assert go == GoCommand(nodes=9)

    def test_go_with_only_words_it_skips_is_not_infinite(self) -> None:
        assert _go("go ponder") == GoCommand()
        assert _go("go depth x") == GoCommand()


class TestSearchLimits:
    """``go``'s parameters become search limits for the side to move."""

    def test_no_limits_given_means_none_set_but_not_infinite(self) -> None:
        assert search_limits(GoCommand(), b.WHITE) == SearchLimits()

    def test_infinite(self) -> None:
        assert search_limits(GoCommand(infinite=True), b.WHITE) == SearchLimits(infinite=True)

    def test_infinite_overrides_every_other_limit(self) -> None:
        go = GoCommand(depth=3, movetime=100, mate=2, infinite=True)
        assert search_limits(go, b.WHITE) == SearchLimits(infinite=True)

    @pytest.mark.parametrize(
        ("go", "depth"),
        [
            (GoCommand(mate=1), 2),
            (GoCommand(mate=3), 6),
            (GoCommand(mate=0), 1),
            (GoCommand(mate=-2), 1),
            (GoCommand(mate=100), MAX_DEPTH),
            (GoCommand(mate=3, depth=4), 4),
            (GoCommand(mate=2, depth=9), 4),
        ],
    )
    def test_mate_in_n_searches_2n_plies(self, go: GoCommand, depth: int) -> None:
        # A mate n moves away shows at depth 2n, when the mated side's move
        # generation finds nothing.
        assert search_limits(go, b.WHITE) == SearchLimits(depth=depth)

    @pytest.mark.parametrize(
        ("depth", "limit"), [(1, 1), (5, 5), (0, 1), (-2, 1), (MAX_DEPTH + 50, MAX_DEPTH)]
    )
    def test_depth_is_kept_in_range(self, depth: int, limit: int) -> None:
        assert search_limits(GoCommand(depth=depth), b.WHITE) == SearchLimits(depth=limit)

    @pytest.mark.parametrize(("nodes", "limit"), [(1, 1), (5000, 5000), (0, 1), (-7, 1)])
    def test_nodes_are_at_least_one(self, nodes: int, limit: int) -> None:
        assert search_limits(GoCommand(nodes=nodes), b.WHITE) == SearchLimits(nodes=limit)

    def test_movetime(self) -> None:
        budget = movetime_budget(500)
        assert search_limits(GoCommand(movetime=500), b.BLACK) == SearchLimits(
            soft_ms=budget.soft_ms, hard_ms=budget.hard_ms
        )

    @pytest.mark.parametrize(
        ("side", "remaining", "increment"), [(b.WHITE, 60_000, 500), (b.BLACK, 30_000, 250)]
    )
    def test_the_side_to_moves_clock(self, side: int, remaining: int, increment: int) -> None:
        go = GoCommand(wtime=60_000, btime=30_000, winc=500, binc=250, movestogo=20)
        budget = clock_budget(remaining, increment, 20)
        assert search_limits(go, side) == SearchLimits(
            soft_ms=budget.soft_ms, hard_ms=budget.hard_ms
        )

    def test_a_clock_without_an_increment(self) -> None:
        budget = clock_budget(10_000, 0, None)
        assert search_limits(GoCommand(wtime=10_000, btime=10_000), b.WHITE) == SearchLimits(
            soft_ms=budget.soft_ms, hard_ms=budget.hard_ms
        )

    def test_a_negative_clock_counts_as_empty(self) -> None:
        budget = clock_budget(0, 0, None)
        go = GoCommand(wtime=-50, btime=-50, winc=-5, binc=-5)
        assert search_limits(go, b.WHITE) == SearchLimits(
            soft_ms=budget.soft_ms, hard_ms=budget.hard_ms
        )

    def test_movetime_wins_over_the_clock(self) -> None:
        budget = movetime_budget(300)
        go = GoCommand(wtime=60_000, btime=60_000, movetime=300)
        assert search_limits(go, b.WHITE) == SearchLimits(
            soft_ms=budget.soft_ms, hard_ms=budget.hard_ms
        )

    def test_only_the_other_sides_clock_means_no_time_limit(self) -> None:
        assert search_limits(GoCommand(btime=1_000), b.WHITE) == SearchLimits()

    def test_limits_combine(self) -> None:
        budget = movetime_budget(400)
        assert search_limits(GoCommand(depth=4, nodes=100, movetime=400), b.WHITE) == (
            SearchLimits(depth=4, nodes=100, soft_ms=budget.soft_ms, hard_ms=budget.hard_ms)
        )


class TestStopAndQuit:
    """``stop`` and ``quit`` end a search promptly; ``quit`` ends the session."""

    def test_stop_ends_the_search_with_a_bestmove(self) -> None:
        engine = _Engine()
        engine.session.handle("go infinite")
        time.sleep(0.2)
        start = time.perf_counter()
        engine.send("stop")
        assert time.perf_counter() - start < 1.0
        assert engine.bestmoves()[0] in _legal(START_FEN)

    def test_stop_without_a_search_does_nothing(self) -> None:
        assert _Engine().send("stop") == []

    def test_quit_ends_the_session(self) -> None:
        engine = _Engine()
        assert engine.session.handle("quit") is False
        assert engine.lines == []

    def test_quit_during_a_search_stops_it_first(self) -> None:
        engine = _Engine()
        engine.session.handle("go infinite")
        start = time.perf_counter()
        assert engine.session.handle("quit") is False
        assert time.perf_counter() - start < 1.0
        assert len(engine.bestmoves()) == 1
        assert not engine.session.searching

    def test_go_during_a_search_stops_it_first(self) -> None:
        engine = _Engine()
        engine.session.handle("go infinite")
        engine.send("go depth 1")
        assert len(engine.bestmoves()) == 2

    def test_position_during_a_search_stops_it_first(self) -> None:
        engine = _Engine()
        engine.session.handle("go infinite")
        engine.send("position startpos moves e2e4")
        assert len(engine.bestmoves()) == 1
        engine.send("go depth 1")
        assert engine.bestmoves()[1] in _legal(START_FEN, "e2e4")

    def test_ucinewgame_during_a_search_stops_it_first(self) -> None:
        engine = _Engine()
        engine.session.handle("go infinite")
        engine.send("ucinewgame")
        assert len(engine.bestmoves()) == 1


class TestOtherInput:
    """Anything else is ignored, as UCI asks."""

    @pytest.mark.parametrize(
        "line", ["", "   ", "xyzzy", "debug on", "setoption name Hash value 16", "ponderhit"]
    )
    def test_is_ignored(self, line: str) -> None:
        assert _Engine().send(line) == []

    def test_surrounding_whitespace_is_ignored(self) -> None:
        assert _Engine().send("  isready \n") == ["readyok"]


class TestMain:
    """``main`` runs the protocol on stdin and stdout."""

    def _run(self, stdin: str) -> list[str]:
        out = io.StringIO()
        with patch("sys.stdin", io.StringIO(stdin)), patch("sys.stdout", out):
            main()
        return out.getvalue().splitlines()

    def test_answers_the_handshake_and_stops_at_quit(self) -> None:
        lines = self._run("uci\nisready\nquit\nisready\n")
        assert lines == [f"id name {ENGINE_NAME}", f"id author {ENGINE_AUTHOR}", "uciok", "readyok"]

    def test_the_end_of_input_ends_a_search_and_the_session(self) -> None:
        lines = self._run("position startpos\ngo infinite\n")
        assert lines[-1].startswith("bestmove ")
        assert lines[-1].split()[1] in _legal(START_FEN)

    def test_plays_a_move(self) -> None:
        lines = self._run("position startpos moves e2e4\ngo depth 2\nquit\n")
        assert lines[-1].split()[1] in _legal(START_FEN, "e2e4")

    def test_writes_nothing_before_the_first_command(self) -> None:
        assert self._run("") == []

    def test_warms_up_once_before_reading_commands(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def warm_up(_: UciSession) -> None:
            calls.append("warm_up")

        monkeypatch.setattr(UciSession, "warm_up", warm_up)
        assert self._run("isready\nquit\n") == ["readyok"]
        assert calls == ["warm_up"]
