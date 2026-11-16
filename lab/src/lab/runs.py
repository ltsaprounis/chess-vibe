"""Runs of the SPRT runner: its command line, its JSON lines and its process.

The lab drives the runner only as a CLI subprocess, as the backend does: the
runner's own interpreter, ``sprt-runner/.venv/bin/python -m sprt_runner run``,
started from the repository root, whose working tree it plays from. It never
imports the runner. The runner writes JSON lines to stdout (its module
docstring lists them), and this module reads the ones the lab uses into typed
messages.

A run counts only if the runner exits 0 after a ``complete`` line. Anything
else is a harness failure: another exit code, a run with no ``complete``, or
one the lab stops at its time limit or on a signal.
:meth:`RunReport.failure` says which.

The runner stays in the lab's process group. So a signal sent to the group,
such as Ctrl-C in a terminal, or a kill of the whole group, reaches both, and
the runner's own handling ends its workers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from lab.git import clean_environment

logger = logging.getLogger(__name__)

# The runner's interpreter, relative to the repository root.
RUNNER_PYTHON = Path("sprt-runner") / ".venv" / "bin" / "python"

# How long the runner has to exit after SIGTERM before the lab kills it. Its
# own cleanup ends its workers and removes the worktrees it built in.
DEFAULT_GRACE_S = 60.0

# How often a run's progress is logged, at most.
PROGRESS_EVERY_S = 60.0

# How much of the runner's stderr a failure quotes when the runner wrote no
# error line, as when it stops on a traceback.
STDERR_TAIL_LINES = 20

# The longest line read from the runner. Its lines are well under a kilobyte.
LINE_LIMIT = 1 << 20

# The terminations the verdicts read. A complete line without them breaks the
# runner's contract (it lists all seven, zeros included), so it isn't used.
ENGINE_CRASH = "engine_crash"
TIMEOUT = "timeout"

RunEnding = Literal["exited", "timeout", "stopped"]


class _Message(BaseModel):
    """A runner message: strict on the fields the lab reads, ignoring the rest."""

    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")


class StartMessage(_Message):
    """The run's first line, with the seed that orders the book's positions."""

    seed: int


class ProgressMessage(_Message):
    """The running tally, after each game."""

    wins: int
    losses: int
    draws: int
    llr: float
    games_total: int
    errors: int


class CompleteMessage(_Message):
    """The run's result.

    Attributes:
        result: ``H0`` or ``H1`` for an SPRT decision, ``inconclusive`` at
            ``--max-games`` without one, or ``fixed`` with ``--no-early-stop``.
        total_games: Games scored.
        llr: Final log-likelihood ratio.
        wins: Wins for the test engine.
        losses: Losses for the test engine.
        draws: Drawn games.
        errors: Games that ended without a result.
        terminations: Scored games per way of ending.
        elo: Elo estimate for the test engine, None where infinite.
        elo_lower: Lower end of its 95% interval, None where infinite.
        elo_upper: Upper end of its 95% interval, None where infinite.
    """

    result: str
    total_games: int
    llr: float
    wins: int
    losses: int
    draws: int
    errors: int
    terminations: dict[str, int]
    elo: float | None
    elo_lower: float | None
    elo_upper: float | None

    @field_validator("terminations")
    @classmethod
    def _counts_what_verdicts_read(cls, counts: dict[str, int]) -> dict[str, int]:
        missing = [name for name in (ENGINE_CRASH, TIMEOUT) if name not in counts]
        if missing:
            raise ValueError(f"no count for {', '.join(missing)}")
        return counts


class ErrorMessage(_Message):
    """An error. A fatal one is the run's last line."""

    message: str


class InterruptedMessage(_Message):
    """The run's last line when a signal stopped it after its games began."""

    games_played: int


Message = StartMessage | ProgressMessage | CompleteMessage | ErrorMessage | InterruptedMessage

_MESSAGE_TYPES: dict[str, type[Message]] = {
    "start": StartMessage,
    "progress": ProgressMessage,
    "complete": CompleteMessage,
    "error": ErrorMessage,
    "interrupted": InterruptedMessage,
}


def parse_line(line: str) -> Message | None:
    """Read one line of the runner's output.

    Args:
        line: The line, without its newline.

    Returns:
        The message, or None for a line the lab doesn't use: one that isn't a
        JSON object, a type the lab doesn't read (``game_result`` among them),
        or a known type with a missing or mistyped field. Those last are
        logged, since the runner shouldn't write them.
    """
    try:
        data: object = json.loads(line)
    except json.JSONDecodeError:
        logger.warning("Skipped a runner line that isn't JSON: %.200s", line)
        return None
    if not isinstance(data, dict):
        logger.warning("Skipped a runner line that isn't a JSON object: %.200s", line)
        return None
    fields = cast("dict[str, object]", data)
    kind = fields.get("type")
    if not isinstance(kind, str):
        return None
    model = _MESSAGE_TYPES.get(kind)
    if model is None:
        return None
    try:
        return model.model_validate(fields)
    except ValidationError as e:
        logger.warning("Skipped a %s line the lab can't read (%s): %.200s", kind, e, line)
        return None


@dataclass(frozen=True)
class RunRequest:
    """One run of the runner.

    Attributes:
        label: How log lines name the run, such as ``attempt 7 fast SPRT``.
        base: The base engine, as ``ENGINE:COMMIT``.
        test: The engine under test, as ``ENGINE:COMMIT``.
        tc: Time control, as ``--tc`` takes it.
        concurrency: Games played at once.
        book: Opening book, relative to the repository root.
        max_games: Scored games after which the run ends.
        timeout_s: Wall-clock limit, after which the lab stops the run.
        elo0: Elo bound of the null hypothesis.
        elo1: Elo bound of the alternative hypothesis.
        alpha: Type-I error rate.
        beta: Type-II error rate.
        early_stop: False for a fixed-length match, which plays every game
            whatever the LLR.
    """

    label: str
    base: str
    test: str
    tc: str
    concurrency: int
    book: str
    max_games: int
    timeout_s: float
    elo0: float = 0.0
    elo1: float = 5.0
    alpha: float = 0.05
    beta: float = 0.05
    early_stop: bool = True


def runner_command(repo: Path, request: RunRequest) -> list[str]:
    """Build the runner's command line, to run from the repository root.

    The seed is left to the runner, which picks one at random and reports it
    on its ``start`` line.

    Args:
        repo: The repository root.
        request: The run.

    Returns:
        The argv.
    """
    argv = [
        str(repo / RUNNER_PYTHON),
        "-m",
        "sprt_runner",
        "run",
        *("--base", request.base),
        *("--test", request.test),
        *("--tc", request.tc),
        *("--elo0", str(request.elo0)),
        *("--elo1", str(request.elo1)),
        *("--alpha", str(request.alpha)),
        *("--beta", str(request.beta)),
        *("--concurrency", str(request.concurrency)),
        *("--book", request.book),
        *("--max-games", str(request.max_games)),
    ]
    if not request.early_stop:
        argv.append("--no-early-stop")
    return argv


@dataclass
class RunReport:
    """What a run said, and how it ended.

    Attributes:
        ending: ``exited`` if the runner ended on its own, ``timeout`` if the
            lab stopped it at its time limit, ``stopped`` if the lab stopped it
            on a signal.
        timeout_s: The run's time limit.
        exit_code: The runner's exit code, negative for a signal, or None if
            it couldn't be had.
        seed: From the ``start`` line.
        complete: The ``complete`` line.
        progress: The last ``progress`` line.
        interrupted: The ``interrupted`` line.
        errors: Every ``error`` line's message, in order.
        duration_s: Wall-clock time from start to exit.
        stderr_tail: The end of the runner's stderr.
        start_error: Why the runner couldn't be started, if it couldn't.
    """

    ending: RunEnding
    timeout_s: float
    exit_code: int | None = None
    seed: int | None = None
    complete: CompleteMessage | None = None
    progress: ProgressMessage | None = None
    interrupted: InterruptedMessage | None = None
    errors: list[str] = field(default_factory=lambda: list[str]())
    duration_s: float = 0.0
    stderr_tail: str = ""
    start_error: str | None = None

    @property
    def succeeded(self) -> bool:
        """Whether the runner exited 0, on its own, after a ``complete`` line."""
        return self.ending == "exited" and self.exit_code == 0 and self.complete is not None

    def failure(self) -> str | None:
        """Say why the run doesn't count, or None if it does."""
        if self.succeeded:
            return None
        if self.start_error is not None:
            return f"the runner couldn't start: {self.start_error}"
        if self.ending == "timeout":
            return f"the runner was stopped at its time limit of {self.timeout_s / 60:g} minutes"
        if self.ending == "stopped":
            return "the runner was stopped by a signal"
        if self.exit_code is None:
            return "the runner closed its output but didn't exit"
        if self.exit_code < 0:
            exited = f"the runner was killed by signal {-self.exit_code}"
        elif self.exit_code != 0:
            exited = f"the runner exited {self.exit_code}"
        else:
            return "the runner exited 0 without a complete line"
        detail = self.errors[-1] if self.errors else _last_line(self.stderr_tail)
        return f"{exited}: {detail}" if detail else exited


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _tail(data: bytes) -> str:
    lines = data.decode(errors="replace").splitlines()
    return "\n".join(lines[-STDERR_TAIL_LINES:])


class LineReader(Protocol):
    """A process's stdout."""

    async def readline(self) -> bytes:
        """Read one line, or b"" at the end."""
        ...


class RunnerProcess(Protocol):
    """The parts of :class:`asyncio.subprocess.Process` the lab uses."""

    @property
    def stdout(self) -> LineReader | None:
        """The process's stdout."""
        ...

    @property
    def returncode(self) -> int | None:
        """The exit code, once it has exited."""
        ...

    async def wait(self) -> int:
        """Wait for the process to exit."""
        ...

    def terminate(self) -> None:
        """Send SIGTERM."""
        ...

    def kill(self) -> None:
        """Send SIGKILL."""
        ...


Spawn = Callable[[Sequence[str], Path, IO[bytes], Mapping[str, str]], Awaitable[RunnerProcess]]


async def spawn_process(
    argv: Sequence[str], cwd: Path, stderr: IO[bytes], env: Mapping[str, str]
) -> RunnerProcess:
    """Start a process with its stdout piped, its stderr to a file, and no stdin."""
    return await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
        env=dict(env),
        limit=LINE_LIMIT,
    )


class _Run:
    """One runner process, and what it has said so far."""

    def __init__(
        self,
        process: RunnerProcess,
        request: RunRequest,
        report: RunReport,
        clock: Callable[[], float],
        progress_every_s: float,
    ) -> None:
        stdout = process.stdout
        if stdout is None:
            raise ValueError("the runner's stdout isn't a pipe")
        self.process = process
        self.report = report
        self._stdout = stdout
        self._label = request.label
        self._clock = clock
        self._progress_every_s = progress_every_s
        self._last_progress_log: float | None = None
        self._read: asyncio.Future[bytes] | None = None

    async def next_line(
        self, others: set[asyncio.Task[Literal[True]]], timeout: float
    ) -> bytes | None:
        """The next line, b"" at the end, or None if ``others`` finished or time ran out."""
        if self._read is None:
            self._read = asyncio.ensure_future(self._stdout.readline())
        read = self._read
        done, _ = await asyncio.wait(
            {read, *others}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if read not in done:
            return None
        self._read = None
        try:
            return read.result()
        except ValueError:
            # asyncio raises this for a line over the limit, having dropped
            # what it had of it. If the line arrived in pieces, its last piece
            # comes back next as a line of its own, which parse_line skips as
            # non-JSON.
            logger.warning("Skipped a runner line longer than %d bytes", LINE_LIMIT)
            return b"\n"

    def cancel_read(self) -> None:
        if self._read is not None:
            self._read.cancel()
            self._read = None

    def handle(self, raw: bytes) -> None:
        text = raw.decode(errors="replace").strip()
        if not text:
            return
        report = self.report
        message = parse_line(text)
        if isinstance(message, StartMessage):
            report.seed = message.seed
            logger.info("%s: started, seed %d", self._label, message.seed)
        elif isinstance(message, ProgressMessage):
            report.progress = message
            self._log_progress(message)
        elif isinstance(message, CompleteMessage):
            report.complete = message
            logger.info(
                "%s: %s after %d games (W %d L %d D %d, errors %d, LLR %.2f)",
                self._label,
                message.result,
                message.total_games,
                message.wins,
                message.losses,
                message.draws,
                message.errors,
                message.llr,
            )
        elif isinstance(message, ErrorMessage):
            report.errors.append(message.message)
            logger.warning("%s: the runner reported an error: %s", self._label, message.message)
        elif isinstance(message, InterruptedMessage):
            report.interrupted = message

    def _log_progress(self, message: ProgressMessage) -> None:
        now = self._clock()
        last = self._last_progress_log
        if last is not None and now - last < self._progress_every_s:
            return
        self._last_progress_log = now
        logger.info(
            "%s: %d games, W %d L %d D %d, errors %d, LLR %.2f",
            self._label,
            message.games_total,
            message.wins,
            message.losses,
            message.draws,
            message.errors,
            message.llr,
        )


class RunnerDriver:
    """Starts the runner, reads its lines, and stops it at its time limit or on request."""

    def __init__(
        self,
        repo: Path,
        *,
        spawn: Spawn = spawn_process,
        grace_s: float = DEFAULT_GRACE_S,
        clock: Callable[[], float] = time.monotonic,
        progress_every_s: float = PROGRESS_EVERY_S,
    ) -> None:
        """Set up the driver.

        Args:
            repo: The repository root, which the runner runs from.
            spawn: Starts a process.
            grace_s: How long the runner has to exit after SIGTERM, and again
                after SIGKILL, before the lab stops waiting.
            clock: Seconds, for timing the run and spacing progress logs.
            progress_every_s: Least time between two progress log lines.
        """
        self._repo = repo
        self._spawn = spawn
        self._grace_s = grace_s
        self._clock = clock
        self._progress_every_s = progress_every_s

    async def run(self, request: RunRequest, stop: asyncio.Event) -> RunReport:
        """Run the runner once.

        Args:
            request: The run.
            stop: Set to stop the run early, as a signal handler does.

        Returns:
            What the run said, and how it ended.
        """
        argv = runner_command(self._repo, request)
        logger.info("%s: %s vs %s at %s", request.label, request.test, request.base, request.tc)
        started = self._clock()
        report = RunReport(ending="exited", timeout_s=request.timeout_s)
        with tempfile.TemporaryFile() as stderr:
            try:
                process = await self._spawn(argv, self._repo, stderr, clean_environment())
            except OSError as e:
                logger.error("%s: the runner couldn't start: %s", request.label, e)
                report.start_error = str(e)
                report.duration_s = self._clock() - started
                return report
            stop_wait = asyncio.create_task(stop.wait())
            run: _Run | None = None
            try:
                run = _Run(process, request, report, self._clock, self._progress_every_s)
                report.ending = await self._follow(run, stop_wait, request.timeout_s)
                if report.ending != "exited":
                    logger.warning("%s: stopping the runner (%s)", request.label, report.ending)
                    await self._shut_down(run)
                report.exit_code = await self._exit_code(process)
            finally:
                stop_wait.cancel()
                if run is not None:
                    run.cancel_read()
                await self._make_sure_stopped(process, request.label)
            stderr.seek(0)
            report.stderr_tail = _tail(stderr.read())
        report.duration_s = self._clock() - started
        return report

    async def _make_sure_stopped(self, process: RunnerProcess, label: str) -> None:
        """Leave no runner behind, however the run ended: SIGTERM, then SIGKILL.

        A runner left running would hold its worktrees and the machine's cores
        after the lab had released its lock.
        """
        for send in (process.terminate, process.kill):
            if process.returncode is not None:
                return
            logger.warning("%s: the runner is still running; stopping it", label)
            with contextlib.suppress(ProcessLookupError):
                send()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), self._grace_s)

    async def _follow(
        self, run: _Run, stop_wait: asyncio.Task[Literal[True]], timeout_s: float
    ) -> RunEnding:
        """Read lines until the runner's output ends, the time limit, or a stop request."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            line = await run.next_line({stop_wait}, max(0.0, deadline - loop.time()))
            if line is None:
                return "stopped" if stop_wait.done() else "timeout"
            if not line:
                return "exited"
            run.handle(line)

    async def _shut_down(self, run: _Run) -> None:
        """Send SIGTERM, read what the runner says as it stops, and kill it if it won't."""
        with contextlib.suppress(ProcessLookupError):
            run.process.terminate()
        if await self._read_to_end(run):
            return
        logger.warning("The runner didn't stop within %g s of SIGTERM; killing it", self._grace_s)
        with contextlib.suppress(ProcessLookupError):
            run.process.kill()
        await self._read_to_end(run)

    async def _read_to_end(self, run: _Run) -> bool:
        """Read lines until the output ends (True) or the grace period does (False)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._grace_s
        while True:
            line = await run.next_line(set(), max(0.0, deadline - loop.time()))
            if line is None:
                return False
            if not line:
                return True
            run.handle(line)

    async def _exit_code(self, process: RunnerProcess) -> int | None:
        """The exit code, waiting up to the grace period for it."""
        try:
            return await asyncio.wait_for(process.wait(), self._grace_s)
        except TimeoutError:
            return process.returncode
