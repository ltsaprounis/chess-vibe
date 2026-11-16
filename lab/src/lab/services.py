"""What a lab command works with: the repository, the config, the runner and the clock."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from lab.config import CorrectnessSettings, LabConfig
from lab.correctness import CorrectnessResult
from lab.git import Repo
from lab.runs import RunReport, RunRequest


class Runner(Protocol):
    """Runs the SPRT runner: :class:`lab.runs.RunnerDriver`, or a fake in unit tests."""

    async def run(self, request: RunRequest, stop: asyncio.Event) -> RunReport:
        """Run it once."""
        ...


# Step 4 of an attempt: the candidate commit, the settings, and a stop request.
CorrectnessCheck = Callable[[str, CorrectnessSettings, asyncio.Event], Awaitable[CorrectnessResult]]


# How long a command yields to the event loop before it starts a process. A
# signal reaches the stop request only when the loop next runs, so one that
# arrived during the synchronous git checks would otherwise go unseen until
# the process had started.
SIGNAL_GRACE_S = 0.01


async def stop_requested(stop: asyncio.Event) -> bool:
    """Whether a stop has been requested, counting any signal already sent.

    Args:
        stop: The command's stop request, which signal handlers set.
    """
    await asyncio.sleep(SIGNAL_GRACE_S)
    return stop.is_set()


def utc_now() -> datetime:
    """The time now, in UTC."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class Lab:
    """Everything a lab command works with.

    Attributes:
        repo: The repository's top-level directory.
        git: Git in that repository.
        config: The settings.
        runner: Runs the SPRT runner.
        check: Step 4 of an attempt.
        config_sha256: The config file's SHA-256, recorded with every entry.
        now: The time, in UTC.
    """

    repo: Path
    git: Repo
    config: LabConfig
    runner: Runner
    check: CorrectnessCheck
    config_sha256: str
    now: Callable[[], datetime] = field(default=utc_now)
