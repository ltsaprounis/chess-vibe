"""Stand-ins for the runner, the correctness check and the clock, for the commands' unit tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lab.config import CorrectnessSettings, LabConfig
from lab.correctness import CorrectnessResult
from lab.runs import (
    CompleteMessage,
    InterruptedMessage,
    ProgressMessage,
    RunReport,
    RunRequest,
)
from lab.services import Lab

from tests import fakes
from tests.fake_repo import FakeRepo
from tests.helpers import config_data

BOOK_TEXT = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq -\n"
CONFIG_SHA256 = "c0" * 32


def finished(result: str = "H1", *, seed: int = 4321, **complete: Any) -> RunReport:
    """A run that ended on its own with a complete line."""
    return RunReport(
        ending="exited",
        timeout_s=1800.0,
        exit_code=0,
        seed=seed,
        complete=CompleteMessage.model_validate(fakes.complete(result, **complete)),
        progress=ProgressMessage.model_validate(fakes.progress(1, 0, 0)),
        duration_s=300.0,
    )


def broken(message: str = "Failed to resolve engines: build failed") -> RunReport:
    """A run whose runner stopped on a fatal error."""
    return RunReport(
        ending="exited",
        timeout_s=1800.0,
        exit_code=1,
        seed=None,
        errors=[message],
        duration_s=12.0,
    )


def stopped(games: int = 5) -> RunReport:
    """A run the lab stopped on a signal, after one error, at LLR 0.42."""
    return RunReport(
        ending="stopped",
        timeout_s=1800.0,
        exit_code=1,
        seed=777,
        progress=ProgressMessage.model_validate(
            fakes.progress(3, 1, games - 4, llr=0.42, errors=1)
        ),
        interrupted=InterruptedMessage(games_played=games),
        duration_s=40.0,
    )


@dataclass
class FakeRunner:
    """Hands out scripted reports, and remembers what it was asked to run."""

    reports: list[RunReport]
    on_run: Callable[[RunRequest, asyncio.Event], None] | None = None
    requests: list[RunRequest] = field(default_factory=lambda: list[RunRequest]())

    async def run(self, request: RunRequest, stop: asyncio.Event) -> RunReport:
        self.requests.append(request)
        if self.on_run is not None:
            self.on_run(request, stop)
        return self.reports.pop(0)


@dataclass
class FakeCheck:
    """Step 4, scripted."""

    result: CorrectnessResult = field(
        default_factory=lambda: CorrectnessResult(True, False, 61.5, "2 commands passed in 61.5 s")
    )
    on_check: Callable[[asyncio.Event], None] | None = None
    calls: list[str] = field(default_factory=lambda: list[str]())

    async def __call__(
        self, candidate: str, settings: CorrectnessSettings, stop: asyncio.Event
    ) -> CorrectnessResult:
        self.calls.append(candidate)
        if self.on_check is not None:
            self.on_check(stop)
        return self.result


class Clock:
    """UTC times an hour apart, from 2026-11-13 15:00."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return datetime(2026, 11, 13, 14, 0, tzinfo=UTC) + timedelta(hours=self.calls)


@dataclass
class World:
    """A lab over a fake repository.

    lab/main is at ``base``, and ``candidate`` is one commit on top. The
    checkout, ``head``, is a later commit on main, as it is when the lab runs
    from the primary checkout.
    """

    lab: Lab
    git: FakeRepo
    runner: FakeRunner
    check: FakeCheck
    base: str
    candidate: str
    head: str
    repo: Path


def make_world(tmp_path: Path, *reports: RunReport, **config: Any) -> World:
    """Build a world whose runner gives ``reports`` in turn.

    The repository directory holds what the lab looks for on disk: the
    runner's interpreter and the book.
    """
    repo = tmp_path / "repo"
    interpreter = repo / "sprt-runner" / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("")
    book = repo / "books" / "test.epd"
    book.parent.mkdir(parents=True)
    book.write_text(BOOK_TEXT)
    git = FakeRepo()
    base = git.add_commit(
        {
            "engines.json": "[]\n",
            "engines/lab-engine/engine.py": "v1\n",
            "engines/lab-engine-notes/readme.md": "n\n",
            "sprt-runner/runner.py": "r\n",
            "shared/lib.py": "s\n",
            "books/test.epd": BOOK_TEXT,
        },
        message="baseline",
    )
    head = git.add_commit({"frontend/app.tsx": "ui\n"}, [base], "later work on main")
    git.refs["refs/heads/main"] = head
    git.refs["refs/heads/lab/main"] = base
    git.head_commit = head
    candidate = git.add_commit({"engines/lab-engine/engine.py": "v2\n"}, [base], "candidate")
    runner = FakeRunner(list(reports))
    check = FakeCheck()
    lab = Lab(
        repo=repo,
        git=git,
        config=LabConfig.model_validate(config_data(**config)),
        runner=runner,
        check=check,
        config_sha256=CONFIG_SHA256,
        now=Clock(),
    )
    return World(lab, git, runner, check, base, candidate, head, repo)
