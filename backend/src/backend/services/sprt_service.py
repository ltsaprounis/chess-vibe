"""SPRT test service — launches and monitors SPRT runner subprocesses.

Invokes the SPRT runner as a CLI subprocess via
``asyncio.create_subprocess_exec``, consumes its JSON-lines stdout,
and updates the test repository. Never imports SPRT runner internals.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shared.storage.models import SPRTOutcome, SPRTStatus, SPRTTest, SPRTTestFilter
from shared.storage.repository import SPRTTestRepository

from backend.converters import time_control_from_string

logger = logging.getLogger(__name__)


@dataclass
class SPRTProgress:
    """Latest progress snapshot for a running SPRT test.

    Attributes:
        wins: Wins for engine_a.
        losses: Losses for engine_a.
        draws: Number of drawn games.
        llr: Current log-likelihood ratio.
        lower_bound: Lower SPRT boundary.
        upper_bound: Upper SPRT boundary.
        games_total: Total games played.
    """

    wins: int = 0
    losses: int = 0
    draws: int = 0
    llr: float = 0.0
    lower_bound: float | None = None
    upper_bound: float | None = None
    games_total: int = 0


@dataclass
class _RunningTest:
    """Internal tracker for a running SPRT subprocess.

    Attributes:
        test_id: SPRT test identifier.
        process: The subprocess handle.
        progress: Latest progress snapshot.
        subscribers: WebSocket queues awaiting updates.
    """

    test_id: str
    process: asyncio.subprocess.Process
    progress: SPRTProgress = field(default_factory=SPRTProgress)
    subscribers: list[asyncio.Queue[dict[str, Any]]] = field(
        default_factory=list[asyncio.Queue[dict[str, Any]]]
    )


class SPRTService:
    """Service for managing SPRT test subprocesses.

    Launches the SPRT runner CLI, reads JSON-lines from its stdout, and
    updates the test repository as results arrive.
    """

    def __init__(
        self,
        test_repo: SPRTTestRepository,
        *,
        runner_python: str = "python",
        repo_root: Path | None = None,
        data_dir: Path | None = None,
    ) -> None:
        """Initialise the service.

        Args:
            test_repo: Repository for persisting test metadata.
            runner_python: Path to the Python interpreter in the
                sprt-runner virtualenv (e.g.
                ``"sprt-runner/.venv/bin/python"``).
            repo_root: Root of the repository for resolving paths.
            data_dir: Root data directory. When set, game files are
                written to ``data_dir/sprt-tests/{test_id}/games/``.
        """
        self._test_repo = test_repo
        self._runner_python = runner_python
        self._repo_root = repo_root or Path.cwd()
        self._data_dir = data_dir
        self._running: dict[str, _RunningTest] = {}

    @property
    def running_tests(self) -> list[str]:
        """IDs of currently running SPRT tests."""
        return list(self._running.keys())

    async def start_test(
        self,
        engine_a: str,
        engine_b: str,
        time_control_str: str,
        *,
        elo0: float = 0.0,
        elo1: float = 5.0,
        alpha: float = 0.05,
        beta: float = 0.05,
        book_path: str | None = None,
        concurrency: int = 1,
    ) -> str:
        """Start a new SPRT test as a background subprocess.

        Args:
            engine_a: First engine identifier.
            engine_b: Second engine identifier.
            time_control_str: Time control string (e.g. ``"movetime=1000"``).
            elo0: Null-hypothesis Elo bound.
            elo1: Alternative-hypothesis Elo bound.
            alpha: Type-I error rate.
            beta: Type-II error rate.
            book_path: Path to opening book, or ``None``.
            concurrency: Number of concurrent workers.

        Returns:
            The new test ID.
        """
        test_id = str(uuid.uuid4())
        tc = time_control_from_string(time_control_str)

        test = SPRTTest(
            id=test_id,
            engine_a=engine_a,
            engine_b=engine_b,
            time_control=tc,
            elo0=elo0,
            elo1=elo1,
            alpha=alpha,
            beta=beta,
            created_at=datetime.now(UTC),
            status=SPRTStatus.RUNNING,
        )
        self._test_repo.save_sprt_test(test)

        cmd = [
            self._runner_python,
            "-m",
            "sprt_runner",
            "run",
            "--base",
            engine_a,
            "--test",
            engine_b,
            "--tc",
            time_control_str,
            "--elo0",
            str(elo0),
            "--elo1",
            str(elo1),
            "--alpha",
            str(alpha),
            "--beta",
            str(beta),
            "--concurrency",
            str(concurrency),
        ]
        if book_path is not None:
            cmd.extend(["--book", book_path])
        if self._data_dir is not None:
            games_dir = self._data_dir / "sprt-tests" / test_id / "games"
            cmd.extend(["--output-dir", str(games_dir)])
            cmd.extend(["--test-id", test_id])

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._repo_root),
        )

        running = _RunningTest(test_id=test_id, process=process)
        self._running[test_id] = running

        asyncio.get_event_loop().create_task(self._monitor(running))

        logger.info("Started SPRT test %s (pid=%s)", test_id, process.pid)
        return test_id

    def get_progress(self, test_id: str) -> SPRTProgress | None:
        """Get latest progress for a running test.

        Args:
            test_id: The SPRT test identifier.

        Returns:
            Latest progress, or ``None`` if the test is not running.
        """
        running = self._running.get(test_id)
        if running is None:
            return None
        return running.progress

    def subscribe(self, test_id: str) -> asyncio.Queue[dict[str, Any]] | None:
        """Subscribe to live progress updates for a test.

        Args:
            test_id: The SPRT test identifier.

        Returns:
            An asyncio Queue that receives JSON messages, or ``None``
            if the test is not running.
        """
        running = self._running.get(test_id)
        if running is None:
            return None
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        running.subscribers.append(queue)
        return queue

    def unsubscribe(self, test_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber queue.

        Args:
            test_id: The SPRT test identifier.
            queue: The queue to remove.
        """
        running = self._running.get(test_id)
        if running is not None and queue in running.subscribers:
            running.subscribers.remove(queue)

    async def cancel_test(self, test_id: str) -> bool:
        """Send SIGTERM to the runner subprocess for graceful cancellation.

        Args:
            test_id: The SPRT test identifier.

        Returns:
            ``True`` if the signal was sent, ``False`` if the test was
            not found or already finished.
        """
        running = self._running.get(test_id)
        if running is None:
            return False

        try:
            running.process.send_signal(signal.SIGTERM)
            logger.info("Sent SIGTERM to SPRT test %s", test_id)
            return True
        except ProcessLookupError:
            logger.warning("SPRT test %s process already gone", test_id)
            return False

    async def recover_on_startup(self) -> int:
        """Mark any ``RUNNING`` tests as ``CANCELLED`` on startup.

        When the backend restarts, subprocess handles are lost, so any
        tests still marked as running cannot be resumed.

        Returns:
            Number of tests marked as cancelled.
        """
        running_tests = self._test_repo.list_sprt_tests(SPRTTestFilter(status=SPRTStatus.RUNNING))
        count = 0
        for test in running_tests:
            updated = SPRTTest(
                id=test.id,
                engine_a=test.engine_a,
                engine_b=test.engine_b,
                time_control=test.time_control,
                elo0=test.elo0,
                elo1=test.elo1,
                alpha=test.alpha,
                beta=test.beta,
                created_at=test.created_at,
                status=SPRTStatus.CANCELLED,
                wins=test.wins,
                losses=test.losses,
                draws=test.draws,
                llr=test.llr,
                result=test.result,
                completed_at=datetime.now(UTC),
            )
            self._test_repo.update_sprt_results(updated)
            count += 1
            logger.info("Marked stale SPRT test %s as cancelled", test.id)

        return count

    async def shutdown(self) -> None:
        """Terminate all running SPRT subprocesses."""
        for running in list(self._running.values()):
            try:
                running.process.terminate()
                await asyncio.wait_for(running.process.wait(), timeout=5.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                running.process.kill()
            logger.info("Terminated SPRT test %s", running.test_id)
        self._running.clear()

    # -- internal ---------------------------------------------------------

    async def _monitor(self, running: _RunningTest) -> None:
        """Read JSON-lines from the subprocess stdout and dispatch updates."""
        assert running.process.stdout is not None

        try:
            while True:
                raw = await running.process.stdout.readline()
                if not raw:
                    break

                line = raw.decode().strip()
                if not line:
                    continue

                try:
                    msg: dict[str, Any] = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Non-JSON line from SPRT runner: %s", line)
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "progress":
                    running.progress = SPRTProgress(
                        wins=msg.get("wins", 0),
                        losses=msg.get("losses", 0),
                        draws=msg.get("draws", 0),
                        llr=msg.get("llr", 0.0),
                        lower_bound=msg.get("lower_bound"),
                        upper_bound=msg.get("upper_bound"),
                        games_total=msg.get("games_total", 0),
                    )
                    self._update_test_from_progress(running)

                elif msg_type == "complete":
                    result_str = msg.get("result", "")
                    self._complete_test(running, result_str)

                # Broadcast to subscribers
                for queue in running.subscribers:
                    await queue.put(msg)

        except Exception:
            logger.exception("Error monitoring SPRT test %s", running.test_id)
        finally:
            await running.process.wait()
            self._running.pop(running.test_id, None)

    def _update_test_from_progress(self, running: _RunningTest) -> None:
        """Persist progress to the test repository."""
        test = self._test_repo.get_sprt_test(running.test_id)
        if test is None:
            return

        updated = SPRTTest(
            id=test.id,
            engine_a=test.engine_a,
            engine_b=test.engine_b,
            time_control=test.time_control,
            elo0=test.elo0,
            elo1=test.elo1,
            alpha=test.alpha,
            beta=test.beta,
            created_at=test.created_at,
            status=SPRTStatus.RUNNING,
            wins=running.progress.wins,
            losses=running.progress.losses,
            draws=running.progress.draws,
            llr=running.progress.llr,
        )
        try:
            self._test_repo.update_sprt_results(updated)
        except KeyError:
            logger.warning("Test %s not found during progress update", running.test_id)

    def _complete_test(self, running: _RunningTest, result_str: str) -> None:
        """Mark a test as completed in the repository."""
        test = self._test_repo.get_sprt_test(running.test_id)
        if test is None:
            return

        outcome: SPRTOutcome | None = None
        if result_str in ("H0", "H1"):
            outcome = SPRTOutcome(result_str)

        updated = SPRTTest(
            id=test.id,
            engine_a=test.engine_a,
            engine_b=test.engine_b,
            time_control=test.time_control,
            elo0=test.elo0,
            elo1=test.elo1,
            alpha=test.alpha,
            beta=test.beta,
            created_at=test.created_at,
            status=SPRTStatus.COMPLETED,
            wins=running.progress.wins,
            losses=running.progress.losses,
            draws=running.progress.draws,
            llr=running.progress.llr,
            result=outcome,
            completed_at=datetime.now(UTC),
        )
        try:
            self._test_repo.update_sprt_results(updated)
        except KeyError:
            logger.warning("Test %s not found during completion", running.test_id)
