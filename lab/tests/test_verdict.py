"""Table-driven tests of the lab's verdicts, from fake runner streams.

Each case scripts what the runner writes and how it exits. The stream goes
through the real :class:`RunnerDriver`, as in a real attempt, and the report
it gives is judged.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from lab.runs import CompleteMessage, RunnerDriver, RunReport, RunRequest
from lab.verdict import Judgement, clearly_stronger, judge_match, judge_sprt

from tests import fakes
from tests.fakes import FakeProcess, FakeSpawn

Line = dict[str, Any]


def _request(*, early_stop: bool = True, timeout_s: float = 30.0) -> RunRequest:
    return RunRequest(
        label="test run",
        base="lab-engine:aaaa",
        test="lab-engine:bbbb",
        tc="movetime=10",
        concurrency=2,
        book="books/test.epd",
        max_games=100,
        timeout_s=timeout_s,
        early_stop=early_stop,
    )


def _report(tmp_path: Path, process: FakeProcess, *, early_stop: bool = True) -> RunReport:
    driver = RunnerDriver(tmp_path, spawn=FakeSpawn(process), grace_s=0.05)
    return asyncio.run(driver.run(_request(early_stop=early_stop, timeout_s=0.1), asyncio.Event()))


SPRT_CASES: list[Any] = [
    pytest.param([fakes.complete("H1")], 0, 0, "pass", (), id="h1"),
    pytest.param(
        [fakes.complete("H1", engine_crash=1)], 0, 0, "reject", ("engine_crash",), id="h1-crash"
    ),
    pytest.param([fakes.complete("H1", errors=2)], 0, 0, "reject", ("errors",), id="h1-errors"),
    pytest.param(
        [fakes.complete("H1", timeout=1)], 0, 0, "reject", ("time_losses",), id="h1-time-loss"
    ),
    pytest.param([fakes.complete("H1", timeout=2)], 0, 2, "pass", (), id="h1-at-time-limit"),
    pytest.param(
        [fakes.complete("H1", timeout=3)], 0, 2, "reject", ("time_losses",), id="h1-over-limit"
    ),
    pytest.param(
        [fakes.complete("H1", engine_crash=1, errors=1, timeout=1)],
        0,
        0,
        "reject",
        ("engine_crash", "errors", "time_losses"),
        id="h1-everything-wrong",
    ),
    pytest.param([fakes.complete("H0")], 0, 0, "reject", ("h0",), id="h0"),
    pytest.param(
        [fakes.complete("inconclusive")], 0, 0, "reject", ("inconclusive",), id="inconclusive"
    ),
    pytest.param(
        [fakes.complete("H0", engine_crash=2, errors=1, timeout=1)],
        0,
        5,
        "reject",
        ("engine_crash", "errors", "h0"),
        id="h0-with-crashes",
    ),
    pytest.param([fakes.complete("fixed")], 0, 0, "fail", ("unexpected_result",), id="fixed"),
    pytest.param([fakes.progress(3, 1, 1)], 0, 0, "fail", ("runner",), id="no-complete"),
    pytest.param(
        [fakes.error("Failed to resolve engines")], 1, 0, "fail", ("runner",), id="exit-1"
    ),
    pytest.param([fakes.complete("H1")], 1, 0, "fail", ("runner",), id="complete-then-exit-1"),
]


@pytest.mark.parametrize(
    ("lines", "exit_code", "max_time_losses", "outcome", "reasons"), SPRT_CASES
)
def test_judge_sprt(
    tmp_path: Path,
    lines: list[Line],
    exit_code: int,
    max_time_losses: int,
    outcome: str,
    reasons: tuple[str, ...],
) -> None:
    report = _report(tmp_path, FakeProcess([fakes.start(), *lines], exit_code))

    judgement = judge_sprt(report, max_time_losses)

    assert (judgement.outcome, judgement.reasons) == (outcome, reasons)


def test_a_run_stopped_at_its_time_limit_fails(tmp_path: Path) -> None:
    report = _report(tmp_path, FakeProcess([fakes.start()], hang=True))

    judgement = judge_sprt(report, 0)

    assert (judgement.outcome, judgement.reasons) == ("fail", ("time_limit",))
    assert "time limit" in judgement.message


def test_a_run_stopped_by_a_signal_fails(tmp_path: Path) -> None:
    process = FakeProcess([fakes.start()], hang=True, on_terminate=[fakes.interrupted(4)])
    driver = RunnerDriver(tmp_path, spawn=FakeSpawn(process), grace_s=0.05)
    stop = asyncio.Event()
    stop.set()

    report = asyncio.run(driver.run(_request(), stop))
    judgement = judge_sprt(report, 0)

    assert (judgement.outcome, judgement.reasons) == ("fail", ("interrupted",))


class TestMessages:
    def _judge(self, tmp_path: Path, line: Line, max_time_losses: int = 0) -> Judgement:
        return judge_sprt(_report(tmp_path, FakeProcess([fakes.start(), line])), max_time_losses)

    def test_a_pass_gives_the_decision_and_the_tally(self, tmp_path: Path) -> None:
        line = fakes.complete("H1", wins=50, losses=30, draws=20, llr=2.96)

        message = self._judge(tmp_path, line).message

        assert message == "H1 after 100 games: W 50 L 30 D 20, LLR 2.96"

    def test_each_reason_is_explained(self, tmp_path: Path) -> None:
        line = fakes.complete("H0", engine_crash=2, errors=1, timeout=3, llr=-2.95)

        message = self._judge(tmp_path, line, max_time_losses=1).message

        assert message.startswith("H0 after 100 games")
        assert "2 games ended in engine_crash" in message
        assert "1 game ended without a result" in message
        assert "3 games lost on time, more than the 1 allowed" in message
        assert "SPRT accepted H0" in message

    def test_inconclusive_is_explained(self, tmp_path: Path) -> None:
        message = self._judge(tmp_path, fakes.complete("inconclusive")).message

        assert "no SPRT decision by the game limit" in message

    def test_a_failure_quotes_the_runner(self, tmp_path: Path) -> None:
        report = _report(tmp_path, FakeProcess([fakes.error("engine build failed")], 1))

        message = judge_sprt(report, 0).message

        assert "engine build failed" in message


MATCH_CASES: list[Any] = [
    pytest.param([fakes.complete("fixed")], 0, 0, "pass", (), id="clean"),
    pytest.param(
        [fakes.complete("fixed", engine_crash=1)], 0, 0, "reject", ("engine_crash",), id="c"
    ),
    pytest.param([fakes.complete("fixed", errors=1)], 0, 0, "reject", ("errors",), id="errors"),
    pytest.param([fakes.complete("fixed", timeout=1)], 0, 0, "reject", ("time_losses",), id="t"),
    pytest.param([fakes.complete("fixed", timeout=1)], 0, 1, "pass", (), id="time-at-limit"),
    pytest.param([fakes.complete("H1")], 0, 0, "fail", ("unexpected_result",), id="h1"),
    pytest.param([fakes.complete("inconclusive")], 0, 0, "fail", ("unexpected_result",), id="i"),
    pytest.param([], 1, 0, "fail", ("runner",), id="exit-1"),
]


@pytest.mark.parametrize(
    ("lines", "exit_code", "max_time_losses", "outcome", "reasons"), MATCH_CASES
)
def test_judge_match(
    tmp_path: Path,
    lines: list[Line],
    exit_code: int,
    max_time_losses: int,
    outcome: str,
    reasons: tuple[str, ...],
) -> None:
    process = FakeProcess([fakes.start(), *lines], exit_code)
    report = _report(tmp_path, process, early_stop=False)

    judgement = judge_match(report, max_time_losses)

    assert (judgement.outcome, judgement.reasons) == (outcome, reasons)


def _complete(wins: int, losses: int, draws: int, elo_lower: float | None) -> CompleteMessage:
    line = fakes.complete("fixed", wins=wins, losses=losses, draws=draws, elo_lower=elo_lower)
    return CompleteMessage.model_validate(line)


@pytest.mark.parametrize(
    ("wins", "losses", "draws", "elo_lower", "expected"),
    [
        pytest.param(60, 20, 20, 12.0, True, id="interval-above-zero"),
        pytest.param(40, 30, 30, 0.0, False, id="interval-touches-zero"),
        pytest.param(40, 35, 25, -3.0, False, id="interval-straddles-zero"),
        pytest.param(20, 0, 0, None, True, id="every-game-won"),
        pytest.param(0, 20, 0, None, False, id="every-game-lost"),
        pytest.param(1, 1, 0, None, False, id="interval-below-minus-infinity"),
        pytest.param(19, 0, 1, None, False, id="lower-end-unknown-with-a-draw"),
        pytest.param(0, 0, 0, None, False, id="no-games"),
    ],
)
def test_clearly_stronger(
    wins: int, losses: int, draws: int, elo_lower: float | None, expected: bool
) -> None:
    assert clearly_stronger(_complete(wins, losses, draws, elo_lower)) is expected
