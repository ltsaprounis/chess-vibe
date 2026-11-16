"""Tests for driving the SPRT runner: its command, its JSON lines and its process."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO

import pytest
from lab.runs import (
    CompleteMessage,
    ErrorMessage,
    InterruptedMessage,
    ProgressMessage,
    RunnerDriver,
    RunReport,
    RunRequest,
    StartMessage,
    parse_line,
    runner_command,
)

from tests import fakes
from tests.fakes import FakeProcess, FakeSpawn


def _request(**changes: object) -> RunRequest:
    values: dict[str, object] = {
        "label": "attempt 3 fast SPRT",
        "base": "lab-engine:aaaa",
        "test": "lab-engine:bbbb",
        "tc": "wtime=1000,btime=1000,winc=10,binc=10",
        "concurrency": 3,
        "book": "books/test.epd",
        "max_games": 50,
        "timeout_s": 30.0,
        "elo0": 0.0,
        "elo1": 10.0,
        "alpha": 0.05,
        "beta": 0.1,
        "early_stop": True,
    }
    values.update(changes)
    return RunRequest(**values)  # type: ignore[arg-type]


def _flags(argv: list[str]) -> dict[str, str]:
    """The ``--flag value`` pairs after ``run``."""
    pairs = argv[4:]
    return dict(zip(pairs[::2], pairs[1::2], strict=True))


class TestRunnerCommand:
    def test_runs_the_runner_with_its_own_interpreter(self, tmp_path: Path) -> None:
        argv = runner_command(tmp_path, _request())

        assert argv[:4] == [
            str(tmp_path / "sprt-runner" / ".venv" / "bin" / "python"),
            "-m",
            "sprt_runner",
            "run",
        ]

    def test_passes_every_setting_of_an_sprt(self, tmp_path: Path) -> None:
        argv = runner_command(tmp_path, _request())

        assert _flags(argv) == {
            "--base": "lab-engine:aaaa",
            "--test": "lab-engine:bbbb",
            "--tc": "wtime=1000,btime=1000,winc=10,binc=10",
            "--elo0": "0.0",
            "--elo1": "10.0",
            "--alpha": "0.05",
            "--beta": "0.1",
            "--concurrency": "3",
            "--book": "books/test.epd",
            "--max-games": "50",
        }

    def test_a_fixed_match_plays_every_game(self, tmp_path: Path) -> None:
        argv = runner_command(tmp_path, _request(early_stop=False))

        assert argv[-1] == "--no-early-stop"
        assert _flags(argv[:-1])["--max-games"] == "50"


class TestFailure:
    @pytest.mark.parametrize(
        ("exit_code", "expected"),
        [
            pytest.param(-9, "the runner was killed by signal 9", id="killed"),
            pytest.param(None, "the runner closed its output but didn't exit", id="no-exit-code"),
            pytest.param(2, "the runner exited 2", id="bad-flags"),
        ],
    )
    def test_how_the_runner_ended(self, exit_code: int | None, expected: str) -> None:
        report = RunReport(ending="exited", timeout_s=60.0, exit_code=exit_code)

        assert report.failure() == expected

    def test_a_timeout_names_the_limit(self) -> None:
        report = RunReport(ending="timeout", timeout_s=5400.0, exit_code=1)

        assert report.failure() == "the runner was stopped at its time limit of 90 minutes"


class TestParseLine:
    def test_start(self) -> None:
        assert parse_line(json.dumps(fakes.start(42))) == StartMessage(seed=42)

    def test_progress(self) -> None:
        message = parse_line(json.dumps(fakes.progress(3, 2, 1, llr=0.5, errors=1)))

        assert message == ProgressMessage(
            wins=3, losses=2, draws=1, llr=0.5, games_total=6, errors=1
        )

    def test_complete(self) -> None:
        message = parse_line(json.dumps(fakes.complete("H0", wins=1, losses=5, draws=4, timeout=2)))

        assert isinstance(message, CompleteMessage)
        assert message.result == "H0"
        assert (message.wins, message.losses, message.draws) == (1, 5, 4)
        assert message.total_games == 10
        assert message.terminations["timeout"] == 2
        assert message.terminations["checkmate"] == 8
        assert (message.elo, message.elo_lower, message.elo_upper) == (141.0, 70.0, 220.0)

    def test_complete_with_infinite_elo(self) -> None:
        line = fakes.complete(elo=None, elo_lower=None, elo_upper=None)

        message = parse_line(json.dumps(line))

        assert isinstance(message, CompleteMessage)
        assert message.elo is None

    def test_error(self) -> None:
        assert parse_line(json.dumps(fakes.error("boom"))) == ErrorMessage(message="boom")

    def test_interrupted(self) -> None:
        assert parse_line(json.dumps(fakes.interrupted(7))) == InterruptedMessage(games_played=7)

    @pytest.mark.parametrize(
        "line",
        [
            pytest.param("not json", id="not-json"),
            pytest.param("[1, 2]", id="not-an-object"),
            pytest.param('{"type": "game_result", "game_id": "g"}', id="game-result"),
            pytest.param('{"type": "surprise"}', id="unknown-type"),
            pytest.param('{"seed": 1}', id="no-type"),
            pytest.param('{"type": "start", "seed": "1"}', id="wrong-field-type"),
        ],
    )
    def test_lines_the_lab_does_not_use(self, line: str) -> None:
        assert parse_line(line) is None

    @pytest.mark.parametrize("reason", ["engine_crash", "timeout"])
    def test_a_complete_line_must_count_the_terminations_the_verdict_reads(
        self, reason: str
    ) -> None:
        line = fakes.complete()
        del line["terminations"][reason]

        assert parse_line(json.dumps(line)) is None


async def _drive(
    tmp_path: Path,
    process: FakeProcess,
    *,
    stop: asyncio.Event | None = None,
    request: RunRequest | None = None,
    grace_s: float = 5.0,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[RunReport, FakeSpawn]:
    spawn = FakeSpawn(process)
    driver = RunnerDriver(tmp_path, spawn=spawn, grace_s=grace_s, clock=clock)
    report = await driver.run(request or _request(), stop or asyncio.Event())
    return report, spawn


@pytest.mark.asyncio
class TestDriver:
    async def test_a_finished_run(self, tmp_path: Path) -> None:
        process = FakeProcess([fakes.start(7), fakes.progress(1, 0, 0), fakes.complete("H1")])

        report, spawn = await _drive(tmp_path, process)

        assert report.succeeded
        assert report.ending == "exited"
        assert report.exit_code == 0
        assert report.seed == 7
        assert report.complete is not None
        assert report.complete.result == "H1"
        assert report.failure() is None
        assert spawn.calls == [(runner_command(tmp_path, _request()), tmp_path)]

    async def test_times_the_run(self, tmp_path: Path) -> None:
        times = iter([100.0, 112.5])

        report, _ = await _drive(
            tmp_path, FakeProcess([fakes.start(), fakes.complete()]), clock=lambda: next(times)
        )

        assert report.duration_s == 12.5

    async def test_exit_zero_without_a_complete_line_fails(self, tmp_path: Path) -> None:
        report, _ = await _drive(tmp_path, FakeProcess([fakes.start(), fakes.progress(1, 0, 0)]))

        assert not report.succeeded
        failure = report.failure()
        assert failure is not None
        assert "without a complete line" in failure

    async def test_a_fatal_error_line_explains_the_exit(self, tmp_path: Path) -> None:
        process = FakeProcess(
            [fakes.start(), fakes.error("one bad game"), fakes.error("Failed to resolve engines")],
            exit_code=1,
        )

        report, _ = await _drive(tmp_path, process)

        assert not report.succeeded
        assert report.exit_code == 1
        assert report.errors == ["one bad game", "Failed to resolve engines"]
        failure = report.failure()
        assert failure is not None
        assert "exited 1" in failure
        assert "Failed to resolve engines" in failure
        assert "one bad game" not in failure

    async def test_a_traceback_explains_an_exit_with_no_error_line(self, tmp_path: Path) -> None:
        stderr = b"INFO starting\nTraceback (most recent call last):\nValueError: bad engine id\n"

        report, _ = await _drive(tmp_path, FakeProcess([], exit_code=1, stderr=stderr))

        failure = report.failure()
        assert failure is not None
        assert "exited 1" in failure
        assert "ValueError: bad engine id" in failure

    async def test_a_complete_line_does_not_excuse_a_non_zero_exit(self, tmp_path: Path) -> None:
        report, _ = await _drive(tmp_path, FakeProcess([fakes.start(), fakes.complete()], 1))

        assert not report.succeeded
        assert report.failure() is not None

    async def test_the_time_limit_stops_the_run(self, tmp_path: Path) -> None:
        process = FakeProcess(
            [fakes.start(), fakes.progress(2, 1, 0)],
            hang=True,
            on_terminate=[fakes.interrupted(3)],
        )

        report, _ = await _drive(tmp_path, process, request=_request(timeout_s=0.05))

        assert process.terminated == 1
        assert process.killed == 0
        assert report.ending == "timeout"
        assert not report.succeeded
        assert report.interrupted == InterruptedMessage(games_played=3)
        assert report.progress is not None
        assert (report.progress.wins, report.progress.losses) == (2, 1)
        failure = report.failure()
        assert failure is not None
        assert "time limit" in failure

    async def test_a_stop_request_ends_the_run(self, tmp_path: Path) -> None:
        process = FakeProcess([fakes.start()], hang=True, on_terminate=[fakes.interrupted(0)])
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.05, stop.set)

        report, _ = await _drive(tmp_path, process, stop=stop)

        assert process.terminated == 1
        assert report.ending == "stopped"
        assert report.interrupted == InterruptedMessage(games_played=0)
        failure = report.failure()
        assert failure is not None
        assert "stopped" in failure

    async def test_a_stop_requested_before_the_start_still_stops_it(self, tmp_path: Path) -> None:
        process = FakeProcess([fakes.start()], hang=True)
        stop = asyncio.Event()
        stop.set()

        report, _ = await _drive(tmp_path, process, stop=stop)

        assert process.terminated == 1
        assert report.ending == "stopped"

    async def test_a_result_written_while_being_stopped_does_not_count(
        self, tmp_path: Path
    ) -> None:
        process = FakeProcess(
            [fakes.start()], hang=True, on_terminate=[fakes.complete("H1")], terminate_code=0
        )

        report, _ = await _drive(tmp_path, process, request=_request(timeout_s=0.05))

        assert report.complete is not None
        assert report.exit_code == 0
        assert report.ending == "timeout"
        assert not report.succeeded

    async def test_a_runner_that_ignores_sigterm_is_killed(self, tmp_path: Path) -> None:
        process = FakeProcess([fakes.start()], hang=True, ignore_terminate=True)

        report, _ = await _drive(tmp_path, process, request=_request(timeout_s=0.05), grace_s=0.05)

        assert process.terminated == 1
        assert process.killed == 1
        assert report.ending == "timeout"
        assert report.exit_code == -9

    async def test_a_runner_that_cannot_be_stopped_is_given_up_on(self, tmp_path: Path) -> None:
        process = FakeProcess([fakes.start()], hang=True, ignore_terminate=True, ignore_kill=True)

        report, _ = await _drive(tmp_path, process, request=_request(timeout_s=0.05), grace_s=0.05)

        assert process.terminated >= 1
        assert process.killed >= 1
        assert report.ending == "timeout"
        assert report.exit_code is None
        assert not report.succeeded

    async def test_a_runner_without_a_stdout_pipe(self, tmp_path: Path) -> None:
        process = FakeProcess([fakes.start()])
        process.stdout = None  # type: ignore[assignment]

        with pytest.raises(ValueError, match="stdout"):
            await _drive(tmp_path, process)

    async def test_progress_is_logged_at_most_once_a_minute(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="lab")
        lines = [fakes.start(), fakes.progress(1, 0, 0), fakes.progress(2, 0, 0), fakes.complete()]
        times = iter([0.0, 10.0, 59.0, 70.0])

        await _drive(tmp_path, FakeProcess(lines), clock=lambda: next(times))

        assert "1 games" in caplog.text
        assert "2 games" not in caplog.text

    async def test_the_runner_gets_no_repository_variables(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
            monkeypatch.setenv(name, "/another/repository")
        monkeypatch.setenv("LAB_TEST_KEPT", "yes")

        _, spawn = await _drive(tmp_path, FakeProcess([fakes.start(), fakes.complete()]))

        (env,) = spawn.envs
        assert not {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"} & env.keys()
        assert env["LAB_TEST_KEPT"] == "yes"

    async def test_a_line_over_the_limit_is_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        overlong = ValueError("Separator is not found, and chunk exceed the limit")
        process = FakeProcess([fakes.start(), fakes.complete()], fail_read=overlong)

        report, _ = await _drive(tmp_path, process)

        assert report.succeeded
        assert "longer than" in caplog.text

    async def test_an_unexpected_error_still_stops_the_runner(self, tmp_path: Path) -> None:
        process = FakeProcess([fakes.start()], hang=True, fail_read=RuntimeError("a bug"))

        with pytest.raises(RuntimeError, match="a bug"):
            await _drive(tmp_path, process, grace_s=0.05)

        assert process.terminated == 1
        assert process.returncode is not None

    async def test_after_an_error_a_runner_that_ignores_sigterm_is_killed(
        self, tmp_path: Path
    ) -> None:
        process = FakeProcess(
            [fakes.start()], hang=True, ignore_terminate=True, fail_read=RuntimeError("a bug")
        )

        with pytest.raises(RuntimeError, match="a bug"):
            await _drive(tmp_path, process, grace_s=0.05)

        assert (process.terminated, process.killed) == (1, 1)
        assert process.returncode == -9

    async def test_a_runner_that_closes_its_output_but_lives_on_is_stopped(
        self, tmp_path: Path
    ) -> None:
        process = FakeProcess([fakes.start()], hang=True, close_output=True)

        report, _ = await _drive(tmp_path, process, grace_s=0.05)

        assert process.terminated == 1
        assert report.failure() == "the runner closed its output but didn't exit"

    async def test_a_runner_that_cannot_start(self, tmp_path: Path) -> None:
        async def cannot_start(
            argv: Sequence[str], cwd: Path, stderr: IO[bytes], env: Mapping[str, str]
        ) -> FakeProcess:
            raise FileNotFoundError(2, "No such file or directory", argv[0])

        driver = RunnerDriver(tmp_path, spawn=cannot_start, grace_s=0.05)
        report = await driver.run(_request(), asyncio.Event())

        assert not report.succeeded
        failure = report.failure()
        assert failure is not None
        assert failure.startswith("the runner couldn't start: ")
        assert "No such file or directory" in failure

    async def test_a_long_stderr_is_quoted_from_its_end(self, tmp_path: Path) -> None:
        stderr = "".join(f"INFO line {n}\n" for n in range(30)) + "ValueError: the real cause\n"
        process = FakeProcess([], exit_code=1, stderr=stderr.encode())

        report, _ = await _drive(tmp_path, process)

        assert report.failure() == "the runner exited 1: ValueError: the real cause"
        assert report.stderr_tail.splitlines()[-1] == "ValueError: the real cause"
        assert len(report.stderr_tail.splitlines()) == 20

    async def test_unreadable_lines_are_skipped(self, tmp_path: Path) -> None:
        process = FakeProcess(["not json", fakes.start(), "{}", fakes.complete()])

        report, _ = await _drive(tmp_path, process)

        assert report.succeeded

    async def test_logs_the_seed_and_progress(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="lab")
        process = FakeProcess(
            [fakes.start(99), fakes.progress(4, 3, 2, llr=1.25), fakes.complete()]
        )

        await _drive(tmp_path, process)

        text = caplog.text
        assert "attempt 3 fast SPRT" in text
        assert "seed 99" in text
        assert "9 games" in text
        assert "LLR 1.25" in text
