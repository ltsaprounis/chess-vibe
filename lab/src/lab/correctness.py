"""Step 4 of ``lab attempt``: the candidate's own checks, at the candidate commit.

The commands come from the config's ``[correctness]`` table: the engine's
unit tests and its deep perft, in the repository's config. Each runs without
a shell, in ``engines/lab-engine/`` of a detached worktree at the candidate.
The worktree is made in a scratch directory outside the repository and
removed afterwards, whatever happens. So the checks see exactly the
candidate's code, never the working tree's, and leave nothing behind.

Each command runs in a session of its own, so the lab can end it and every
process it started (``uv run`` starts Python, which pytest may fork) at the
time limit or on a stop request. A failed check refuses the attempt: nothing
about the candidate's strength was measured, so nothing is recorded.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
import shutil
import signal
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from lab.config import CorrectnessSettings
from lab.git import Git, GitError, clean_environment
from lab.layout import ENGINE_DIR

logger = logging.getLogger(__name__)

# How much of a failed command's output the log quotes.
OUTPUT_TAIL_LINES = 20

# How long a command has to exit after SIGTERM before its group gets SIGKILL.
DEFAULT_GRACE_S = 10.0

# Variables a check mustn't inherit, beside those that point git at another
# repository. VIRTUAL_ENV names the lab's own venv when the lab runs under
# ``uv run``, and uv warns about it in another project.
_DROPPED_VARIABLES = ("VIRTUAL_ENV",)


@dataclass(frozen=True)
class CorrectnessResult:
    """How the checks went.

    Attributes:
        passed: Whether every command exited 0 in time.
        stopped: Whether a stop request ended them.
        duration_s: Wall-clock time, from checkout to cleanup.
        message: What happened, for a person.
    """

    passed: bool
    stopped: bool
    duration_s: float
    message: str


@dataclass(frozen=True)
class _Failure:
    stopped: bool
    message: str


def _limit(minutes: float) -> str:
    """Describe a time limit, such as ``20-minute limit``."""
    if minutes >= 1.0:
        return f"{minutes:g}-minute limit"
    return f"{minutes * 60:g}-second limit"


def _environment() -> dict[str, str]:
    return clean_environment(drop=_DROPPED_VARIABLES)


def _tail(data: bytes) -> list[str]:
    lines = [line.rstrip() for line in data.decode(errors="replace").splitlines()]
    return [line for line in lines if line][-OUTPUT_TAIL_LINES:]


def _signal_group(pid: int, sig: signal.Signals) -> None:
    """Signal the session a command leads, ignoring one that has already gone.

    macOS answers EPERM for a group whose only member is an exited process not
    yet reaped, and ESRCH once it is. While any member lives, its group id
    can't be handed to another process; once none does, reusing it would take
    the system's pids to wrap round within the grace period.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


async def _wait(
    process: asyncio.subprocess.Process, deadline: float, stop: asyncio.Event
) -> Literal["timeout", "stopped"] | None:
    """Wait for a command to exit (None), its time limit, or a stop request."""
    loop = asyncio.get_running_loop()
    exited = asyncio.ensure_future(process.wait())
    stopping = asyncio.ensure_future(stop.wait())
    try:
        done, _ = await asyncio.wait(
            {exited, stopping},
            timeout=max(0.0, deadline - loop.time()),
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        stopping.cancel()
    if exited in done:
        return None
    exited.cancel()
    return "stopped" if stopping in done else "timeout"


async def _end_group(process: asyncio.subprocess.Process, grace_s: float) -> None:
    """SIGTERM the command's whole group, then SIGKILL whatever is left."""
    _signal_group(process.pid, signal.SIGTERM)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), grace_s)
    _signal_group(process.pid, signal.SIGKILL)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), grace_s)


async def _run(
    command: Sequence[str],
    workdir: Path,
    deadline: float,
    limit: str,
    stop: asyncio.Event,
    grace_s: float,
) -> _Failure | None:
    """Run one command to the end: None if it exits 0, else why it failed."""
    shown = shlex.join(command)
    with tempfile.TemporaryFile() as output:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workdir,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=output,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                env=_environment(),
            )
        except OSError as e:
            return _Failure(False, f"`{shown}` couldn't start: {e}")
        logger.info("correctness: running `%s`", shown)
        ending = await _wait(process, deadline, stop)
        if ending is not None:
            await _end_group(process, grace_s)
            if ending == "stopped":
                return _Failure(True, f"stopped by a signal during `{shown}`")
            return _Failure(False, f"`{shown}` ran past the checks' {limit}")
        if process.returncode == 0:
            return None
        output.seek(0)
        tail = _tail(output.read())
    logger.error("correctness: `%s` exited %s; its output ended:", shown, process.returncode)
    for line in tail:
        logger.error("  %s", line)
    exited = f"`{shown}` exited {process.returncode}"
    return _Failure(False, f"{exited}: {tail[-1]}" if tail else exited)


async def check_correctness(
    git: Git,
    candidate: str,
    settings: CorrectnessSettings,
    stop: asyncio.Event,
    *,
    scratch: Path | None = None,
    grace_s: float = DEFAULT_GRACE_S,
    clock: Callable[[], float] = time.monotonic,
) -> CorrectnessResult:
    """Run the configured checks in a worktree at the candidate.

    Args:
        git: The repository.
        candidate: The commit to check.
        settings: The commands, and the time allowed for all of them.
        stop: Set to stop the checks early, as a signal handler does.
        scratch: Where to make the worktree's directory; the system's
            temporary directory by default.
        grace_s: How long a command has to exit after SIGTERM.
        clock: Seconds, for timing the checks.

    Returns:
        Whether every command exited 0 in time.
    """
    started = clock()
    workspace = Path(tempfile.mkdtemp(prefix="lab-correctness-", dir=scratch))
    tree = workspace / "candidate"

    def result(passed: bool, stopped: bool, message: str) -> CorrectnessResult:
        return CorrectnessResult(passed, stopped, clock() - started, message)

    try:
        try:
            git.add_worktree(tree, candidate)
        except GitError as e:
            return result(False, False, f"couldn't check out the candidate: {e}")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.timeout_minutes * 60.0
        limit = _limit(settings.timeout_minutes)
        for command in settings.commands:
            if stop.is_set():
                return result(False, True, "stopped by a signal")
            failure = await _run(command, tree / ENGINE_DIR, deadline, limit, stop, grace_s)
            if failure is not None:
                return result(False, failure.stopped, failure.message)
        count = len(settings.commands)
        noun = "command" if count == 1 else "commands"
        return result(True, False, f"{count} {noun} passed in {clock() - started:.1f} s")
    finally:
        if tree.exists():
            try:
                git.remove_worktree(tree)
            except GitError as e:
                logger.warning("correctness: couldn't remove the worktree at %s: %s", tree, e)
        shutil.rmtree(workspace, ignore_errors=True)
