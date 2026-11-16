"""Fake processes and runner streams for the lab's unit tests.

Nothing here starts a process or sends a signal: a fake process only records
that it was asked to stop, and its pid is past any real one.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import IO, Any

# Past the largest pid either macOS or Linux hands out, so a pid from a fake
# that reached a real signal call could never name a real process.
FAKE_PID = 4_200_001

ALL_TERMINATIONS = (
    "checkmate",
    "stalemate",
    "draw_rule",
    "adjudication",
    "timeout",
    "engine_crash",
    "max_moves",
)


def start(seed: int = 1234) -> dict[str, Any]:
    """The runner's first line."""
    return {"type": "start", "seed": seed}


def progress(
    wins: int, losses: int, draws: int, llr: float = 0.0, errors: int = 0
) -> dict[str, Any]:
    """A ``progress`` line."""
    return {
        "type": "progress",
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "llr": llr,
        "lower_bound": -2.94,
        "upper_bound": 2.94,
        "games_total": wins + losses + draws,
        "errors": errors,
    }


def complete(
    result: str = "H1",
    *,
    wins: int = 60,
    losses: int = 20,
    draws: int = 20,
    errors: int = 0,
    llr: float = 2.95,
    elo: float | None = 141.0,
    elo_lower: float | None = 70.0,
    elo_upper: float | None = 220.0,
    **terminations: int,
) -> dict[str, Any]:
    """A ``complete`` line. Unnamed terminations count 0, except checkmate, which takes the rest."""
    counts: dict[str, int] = dict.fromkeys(ALL_TERMINATIONS, 0)
    counts.update(terminations)
    if "checkmate" not in terminations:
        counts["checkmate"] = wins + losses + draws - sum(counts.values())
    return {
        "type": "complete",
        "result": result,
        "total_games": wins + losses + draws,
        "llr": llr,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "errors": errors,
        "terminations": counts,
        "elo": elo,
        "elo_lower": elo_lower,
        "elo_upper": elo_upper,
    }


def error(message: str) -> dict[str, Any]:
    """An ``error`` line."""
    return {"type": "error", "message": message}


def interrupted(games_played: int) -> dict[str, Any]:
    """An ``interrupted`` line."""
    return {"type": "interrupted", "games_played": games_played}


def _encode(line: dict[str, Any] | str) -> bytes:
    text = line if isinstance(line, str) else json.dumps(line)
    return f"{text}\n".encode()


class FakeStream:
    """The stdout of a :class:`FakeProcess`."""

    def __init__(self, process: FakeProcess) -> None:
        self._process = process

    async def readline(self) -> bytes:
        return await self._process.next_line()


class FakeProcess:
    """A runner process that writes scripted lines.

    Without ``hang``, it writes ``lines`` and exits with ``exit_code``. With
    ``hang``, it writes ``lines`` and then waits, writing nothing more, until
    it's told to stop. ``terminate()`` then writes ``on_terminate`` and exits
    with ``terminate_code``, unless ``ignore_terminate`` is set, when only
    ``kill()`` ends it; with ``ignore_kill`` too, nothing does. With
    ``close_output``, its output ends after ``lines`` while it goes on
    running. ``fail_read`` is raised by the first read, as asyncio raises
    ValueError for a line over its limit; the lines are still there after.
    """

    def __init__(
        self,
        lines: Iterable[dict[str, Any] | str] = (),
        exit_code: int = 0,
        *,
        hang: bool = False,
        on_terminate: Iterable[dict[str, Any] | str] = (),
        terminate_code: int = 1,
        ignore_terminate: bool = False,
        ignore_kill: bool = False,
        close_output: bool = False,
        fail_read: Exception | None = None,
        stderr: bytes = b"",
    ) -> None:
        self.pid = FAKE_PID
        self.returncode: int | None = None
        self.stdout = FakeStream(self)
        self.stderr_text = stderr
        self.terminated = 0
        self.killed = 0
        self._pending: deque[bytes] = deque(_encode(line) for line in lines)
        self._exit_code = exit_code
        self._hang = hang
        self._on_terminate = [_encode(line) for line in on_terminate]
        self._terminate_code = terminate_code
        self._ignore_terminate = ignore_terminate
        self._ignore_kill = ignore_kill
        self._close_output = close_output
        self._fail_read = fail_read
        self._wake = asyncio.Event()

    def _exit(self, code: int) -> None:
        if self.returncode is None:
            self.returncode = code
        self._hang = False
        self._wake.set()

    async def next_line(self) -> bytes:
        """The next line, or b"" once the output has ended.

        As with a real process, the output ends before the exit is reported:
        ``returncode`` stays None until ``wait()``.
        """
        if self._fail_read is not None:
            failure, self._fail_read = self._fail_read, None
            raise failure
        while True:
            if self._pending:
                return self._pending.popleft()
            if self.returncode is not None or not self._hang or self._close_output:
                return b""
            self._wake.clear()
            await self._wake.wait()

    async def wait(self) -> int:
        while True:
            if self.returncode is not None:
                return self.returncode
            if not self._hang:
                self._exit(self._exit_code)
                continue
            self._wake.clear()
            await self._wake.wait()

    def terminate(self) -> None:
        self.terminated += 1
        if self._ignore_terminate or self.returncode is not None:
            return
        self._pending.extend(self._on_terminate)
        self._exit(self._terminate_code)

    def kill(self) -> None:
        self.killed += 1
        if not self._ignore_kill:
            self._exit(-9)


class FakeSpawn:
    """Stands in for starting the runner: hands out scripted processes in turn."""

    def __init__(self, *processes: FakeProcess) -> None:
        self._processes = list(processes)
        self.calls: list[tuple[list[str], Path]] = []
        self.envs: list[dict[str, str]] = []

    async def __call__(
        self, argv: Sequence[str], cwd: Path, stderr: IO[bytes], env: Mapping[str, str]
    ) -> FakeProcess:
        self.calls.append((list(argv), cwd))
        self.envs.append(dict(env))
        process = self._processes.pop(0)
        stderr.write(process.stderr_text)
        return process
