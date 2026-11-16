"""Unit tests for the ``lab`` command line, run in-process with typer's test runner."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from collections.abc import Sequence
from pathlib import Path

import lab.cli as cli
import pytest
from lab.anchor import run_anchor
from lab.attempt import AttemptRequest, run_attempt
from lab.cli import Session, app, with_signals
from lab.lock import exclusive
from lab.outcome import Outcome
from typer.testing import CliRunner

from tests.doubles import World, broken, finished, make_world
from tests.fake_repo import FakeRepo

RUNNER = CliRunner()


def _plain(text: str) -> bool:
    """No colour codes and no box drawing: output an agent can read as it is."""
    return "\x1b[" not in text and not any(c in text for c in "╭╮╰╯│─")


@pytest.fixture
def lock_dir(tmp_path: Path) -> Path:
    path = tmp_path / "git"
    path.mkdir()
    return path


def _use(monkeypatch: pytest.MonkeyPatch, world: World, lock_dir: Path) -> None:
    def open_session(repo: Path, config: Path | None) -> Session:
        assert repo == Path("repo-path")
        return Session(world.lab, lock_dir)

    monkeypatch.setattr(cli, "open_session", open_session)


def _attempt_args(world: World) -> list[str]:
    return [
        "attempt",
        *("--repo", "repo-path"),
        *("--base", world.base),
        *("--candidate", world.candidate),
        *("--hypothesis", "Search captures first"),
    ]


class TestUsage:
    def test_help_lists_the_commands_plainly(self) -> None:
        result = RUNNER.invoke(app, ["--help"])

        assert result.exit_code == 0
        for command in ("attempt", "anchor", "report"):
            assert command in result.stdout
        assert "--install-completion" not in result.stdout
        assert _plain(result.output)

    def test_no_command_shows_the_help(self) -> None:
        result = RUNNER.invoke(app, [])

        assert result.exit_code == 2
        assert "attempt" in result.output
        assert _plain(result.output)

    @pytest.mark.parametrize(
        "argv",
        [
            pytest.param(["attempt"], id="attempt-without-options"),
            pytest.param(
                ["attempt", "--repo", "r", "--base", "b", "--candidate", "c"], id="no-hypothesis"
            ),
            pytest.param(
                ["attempt", "--repo", "r", "--base", "b", "--candidate", "c", "--hypothesis", " "],
                id="blank-hypothesis",
            ),
            pytest.param(
                ["attempt", "--base", "b", "--candidate", "c", "--hypothesis", "h"], id="r"
            ),
            pytest.param(["anchor"], id="anchor-without-repo"),
            pytest.param(["report"], id="report-without-repo"),
            pytest.param(["report", "--repo", "r", "--surprise"], id="unknown-option"),
            pytest.param(["nonsense"], id="unknown-command"),
        ],
    )
    def test_bad_flags_exit_2_plainly(
        self, argv: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_session(repo: Path, config: Path | None) -> Session:
            raise AssertionError("a usage error mustn't open the repository")

        monkeypatch.setattr(cli, "open_session", no_session)

        result = RUNNER.invoke(app, argv)

        assert result.exit_code == 2
        assert result.stdout == ""
        assert "Usage:" in result.stderr
        assert _plain(result.stderr)


class TestAttempt:
    @pytest.mark.parametrize(
        ("reports", "outcome", "code"),
        [
            pytest.param([finished("H1"), finished("H1")], "accepted", 0, id="accepted"),
            pytest.param([finished("H0")], "rejected", 4, id="rejected"),
            pytest.param([broken()], "failed", 5, id="failed"),
        ],
    )
    def test_prints_one_json_line_and_exits_with_the_outcome(
        self,
        tmp_path: Path,
        lock_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        reports: list[object],
        outcome: str,
        code: int,
    ) -> None:
        world = make_world(tmp_path, *reports)  # type: ignore[arg-type]
        _use(monkeypatch, world, lock_dir)

        result = RUNNER.invoke(app, _attempt_args(world))

        assert result.exit_code == code
        assert result.stdout.count("\n") == 1
        printed = json.loads(result.stdout)
        assert printed["outcome"] == outcome
        assert printed["check"] is None
        assert printed["entry"]["kind"] == "attempt"
        assert printed["entry"]["hypothesis"] == "Search captures first"
        assert printed["entry"]["verdict"] == outcome
        assert printed["message"]

    def test_a_refusal_exits_3(
        self, tmp_path: Path, lock_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = make_world(tmp_path)
        world.git.dirty_paths = ["shared/lib.py"]
        _use(monkeypatch, world, lock_dir)

        result = RUNNER.invoke(app, _attempt_args(world))

        assert result.exit_code == 3
        printed = json.loads(result.stdout)
        assert (printed["outcome"], printed["check"], printed["entry"]) == (
            "refused",
            "harness",
            None,
        )

    def test_a_running_lab_command_is_refused(
        self, tmp_path: Path, lock_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = make_world(tmp_path, finished(), finished())
        _use(monkeypatch, world, lock_dir)

        with exclusive(lock_dir):
            result = RUNNER.invoke(app, _attempt_args(world))

        assert result.exit_code == 3
        assert json.loads(result.stdout)["check"] == "busy"
        assert world.runner.requests == []

    def test_a_refused_session_exits_3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(repo: Path, config: Path | None) -> Session:
            raise cli.Refused("config", "lab.toml: fast.tc: missing")

        monkeypatch.setattr(cli, "open_session", refuse)

        result = RUNNER.invoke(
            app,
            ["attempt", "--repo", "r", "--base", "b", "--candidate", "c", "--hypothesis", "h"],
        )

        assert result.exit_code == 3
        assert json.loads(result.stdout)["message"] == "lab.toml: fast.tc: missing"

    def test_passes_the_config_path(
        self, tmp_path: Path, lock_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = make_world(tmp_path, finished(), finished())
        seen: list[Path | None] = []

        def open_session(repo: Path, config: Path | None) -> Session:
            seen.append(config)
            return Session(world.lab, lock_dir)

        monkeypatch.setattr(cli, "open_session", open_session)

        RUNNER.invoke(app, [*_attempt_args(world), "--config", "other.toml"])

        assert seen == [Path("other.toml")]

    def test_a_crash_is_a_plain_traceback_and_exit_1(
        self, tmp_path: Path, lock_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = make_world(tmp_path)
        _use(monkeypatch, world, lock_dir)

        async def crash(*args: object) -> Outcome:
            raise RuntimeError("the lab broke")

        monkeypatch.setattr(cli, "run_attempt", crash)

        result = RUNNER.invoke(app, _attempt_args(world))

        assert result.exit_code == 1
        assert isinstance(result.exception, RuntimeError)
        assert result.stdout == ""


class TestAnchor:
    @pytest.mark.parametrize(
        ("lower", "outcome", "code"),
        [
            pytest.param(50.0, "milestone", 0, id="milestone"),
            pytest.param(-1.0, "flagged", 4, id="f"),
        ],
    )
    def test_exits_with_the_outcome(
        self,
        tmp_path: Path,
        lock_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        lower: float,
        outcome: str,
        code: int,
    ) -> None:
        world = make_world(tmp_path, finished("fixed", elo_lower=lower))
        _use(monkeypatch, world, lock_dir)

        result = RUNNER.invoke(app, ["anchor", "--repo", "repo-path"])

        assert result.exit_code == code
        printed = json.loads(result.stdout)
        assert printed["outcome"] == outcome
        assert printed["entry"]["kind"] == "anchor"


class TestReport:
    def test_prints_the_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        git = FakeRepo()
        git.refs["refs/heads/lab/main"] = git.add_commit({"x": "x"})

        def open_repo(repo: Path) -> FakeRepo:
            return git

        monkeypatch.setattr(cli, "open_repo", open_repo)

        result = RUNNER.invoke(app, ["report", "--repo", "repo-path"])

        assert result.exit_code == 0
        assert result.stdout.startswith("lab/main at ")
        assert "Machine hours: 0.0" in result.stdout

    def test_a_refusal_exits_3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def open_repo(repo: Path) -> FakeRepo:
            return FakeRepo()

        monkeypatch.setattr(cli, "open_repo", open_repo)

        result = RUNNER.invoke(app, ["report", "--repo", "repo-path"])

        assert result.exit_code == 3
        assert json.loads(result.stdout)["check"] == "lab-main"


class TestSignals:
    """Each test signals pytest itself, and only after checking a handler is in place."""

    @staticmethod
    def _handled(sig: signal.Signals) -> bool:
        return signal.getsignal(sig) not in (signal.SIG_DFL, signal.SIG_IGN, None)

    @pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
    def test_a_signal_sets_the_stop_request(self, sig: signal.Signals) -> None:
        if signal.getsignal(sig) == signal.SIG_IGN:
            pytest.skip(f"pytest itself runs ignoring {sig.name}, as under nohup")

        async def work(stop: asyncio.Event) -> Outcome:
            # asyncio.run handles SIGINT itself, so being handled isn't enough:
            # the handler must be the loop's, the one SIGTERM has. Otherwise a
            # real SIGINT would end the whole test session.
            assert self._handled(signal.SIGTERM), "SIGTERM isn't handled"
            assert signal.getsignal(sig) is signal.getsignal(signal.SIGTERM), (
                f"{sig.name} doesn't have the lab's handler"
            )
            os.kill(os.getpid(), sig)
            await asyncio.wait_for(stop.wait(), 5)
            return Outcome("failed", "stopped")

        outcome = asyncio.run(with_signals(work))

        assert outcome.kind == "failed"

    @pytest.mark.parametrize("command", ["attempt", "anchor"])
    def test_a_signal_during_the_checks_starts_nothing(self, tmp_path: Path, command: str) -> None:
        """A signal sent while the lab runs its git checks, before anything awaits, still counts.

        asyncio sets the stop request only when its loop next runs, so the lab
        must let it run before starting a process.
        """
        world = make_world(tmp_path, finished("H1"), finished("H1"), finished("fixed"))
        real_dirty = world.git.dirty
        sent: list[bool] = []

        def dirty_and_signal(paths: Sequence[str]) -> list[str]:
            if not sent:
                assert self._handled(signal.SIGTERM), "SIGTERM isn't handled"
                os.kill(os.getpid(), signal.SIGTERM)
                sent.append(True)
            return real_dirty(paths)

        world.git.dirty = dirty_and_signal  # type: ignore[method-assign]

        async def work(stop: asyncio.Event) -> Outcome:
            if command == "attempt":
                request = AttemptRequest("h", world.base, world.candidate)
                return await run_attempt(world.lab, request, stop)
            return await run_anchor(world.lab, stop)

        outcome = asyncio.run(with_signals(work))

        assert sent == [True]
        assert (outcome.kind, outcome.entry) == ("failed", None)
        assert world.check.calls == []
        assert world.runner.requests == []
        assert world.git.refs["refs/heads/lab/main"] == world.base

    def test_a_signal_at_the_end_of_the_checks_starts_no_sprt(self, tmp_path: Path) -> None:
        """A signal sent as step 4 ends, with nothing awaited since, stops the attempt.

        The checks pass, and the fake doesn't await, so only the lab's own
        yield before the fast SPRT lets the stop request through.
        """
        world = make_world(tmp_path, finished("H1"), finished("H1"))

        def signal_on_the_way_out(stop: asyncio.Event) -> None:
            assert self._handled(signal.SIGTERM), "SIGTERM isn't handled"
            os.kill(os.getpid(), signal.SIGTERM)

        world.check.on_check = signal_on_the_way_out

        async def work(stop: asyncio.Event) -> Outcome:
            return await run_attempt(
                world.lab, AttemptRequest("h", world.base, world.candidate), stop
            )

        outcome = asyncio.run(with_signals(work))

        assert (outcome.kind, outcome.entry) == ("failed", None)
        assert world.check.calls == [world.candidate]
        assert world.runner.requests == []
        assert world.git.refs["refs/heads/lab/main"] == world.base

    def test_handlers_are_removed_afterwards(self) -> None:
        async def work(stop: asyncio.Event) -> Outcome:
            return Outcome("accepted", "done")

        before = signal.getsignal(signal.SIGTERM)
        asyncio.run(with_signals(work))

        assert signal.getsignal(signal.SIGTERM) == before

    def test_handlers_go_when_the_work_ends_not_when_the_loop_closes(self) -> None:
        async def work(stop: asyncio.Event) -> Outcome:
            return Outcome("accepted", "done")

        async def outer() -> object:
            await with_signals(work)
            return signal.getsignal(signal.SIGTERM)

        assert asyncio.run(outer()) == signal.SIG_DFL

    def test_an_ignored_hangup_stays_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
        try:

            async def work(stop: asyncio.Event) -> Outcome:
                assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
                return Outcome("accepted", "done")

            asyncio.run(with_signals(work))

            assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
        finally:
            signal.signal(signal.SIGHUP, previous)
