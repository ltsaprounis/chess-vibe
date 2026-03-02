"""Async UCI (Universal Chess Interface) client for communicating with chess engines.

Provides an async wrapper around a UCI engine subprocess, handling protocol
commands, response parsing, timeouts, and engine crash detection.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import time
from dataclasses import dataclass, field

from shared.time_control import TimeControl

logger = logging.getLogger(__name__)


class UCIError(Exception):
    """Base exception for UCI client errors."""


class UCITimeoutError(UCIError):
    """Raised when a UCI command times out."""


class UCIEngineError(UCIError):
    """Raised when the engine process crashes or terminates unexpectedly."""


@dataclass(frozen=True)
class UCIScore:
    """A score reported by a UCI engine.

    Attributes:
        cp: Score in centipawns, or None if mate score.
        mate: Mate in N moves, or None if centipawn score.
    """

    cp: int | None = None
    mate: int | None = None

    def __post_init__(self) -> None:
        """Validate that exactly one of cp or mate is set."""
        if self.cp is None and self.mate is None:
            raise ValueError("Either cp or mate must be set")
        if self.cp is not None and self.mate is not None:
            raise ValueError("Only one of cp or mate can be set")


@dataclass(frozen=True)
class UCIInfo:
    """Parsed info line from a UCI engine.

    Attributes:
        depth: Search depth in plies.
        seldepth: Selective search depth.
        score: Engine evaluation score.
        pv: Principal variation (list of moves in UCI notation).
        nodes: Number of nodes searched.
        time_ms: Time spent searching in milliseconds.
        nps: Nodes per second.
        multipv: Multi-PV line number.
    """

    depth: int | None = None
    seldepth: int | None = None
    score: UCIScore | None = None
    pv: list[str] = field(default_factory=list[str])
    nodes: int | None = None
    time_ms: int | None = None
    nps: int | None = None
    multipv: int | None = None


@dataclass(frozen=True)
class BestMove:
    """Parsed bestmove response from a UCI engine.

    Attributes:
        move: Best move in UCI notation (e.g., "e2e4").
        ponder: Ponder move in UCI notation, or None.
    """

    move: str
    ponder: str | None = None


def parse_info_line(line: str) -> UCIInfo:
    """Parse a UCI info line into a UCIInfo dataclass.

    Args:
        line: A UCI info line (e.g., "info depth 20 score cp 35 pv e2e4 e7e5").

    Returns:
        Parsed UCIInfo with extracted fields.
    """
    tokens = line.split()
    if tokens and tokens[0] == "info":
        tokens = tokens[1:]

    depth: int | None = None
    seldepth: int | None = None
    score: UCIScore | None = None
    pv: list[str] = []
    nodes: int | None = None
    time_ms: int | None = None
    nps: int | None = None
    multipv: int | None = None

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "depth" and i + 1 < len(tokens):
            depth = int(tokens[i + 1])
            i += 2
        elif token == "seldepth" and i + 1 < len(tokens):
            seldepth = int(tokens[i + 1])
            i += 2
        elif token == "score" and i + 1 < len(tokens):
            score_type = tokens[i + 1]
            if score_type == "cp" and i + 2 < len(tokens):
                score = UCIScore(cp=int(tokens[i + 2]))
                i += 3
            elif score_type == "mate" and i + 2 < len(tokens):
                score = UCIScore(mate=int(tokens[i + 2]))
                i += 3
            else:
                i += 2
        elif token == "nodes" and i + 1 < len(tokens):
            nodes = int(tokens[i + 1])
            i += 2
        elif token == "time" and i + 1 < len(tokens):
            time_ms = int(tokens[i + 1])
            i += 2
        elif token == "nps" and i + 1 < len(tokens):
            nps = int(tokens[i + 1])
            i += 2
        elif token == "multipv" and i + 1 < len(tokens):
            multipv = int(tokens[i + 1])
            i += 2
        elif token == "pv":
            pv = tokens[i + 1 :]
            break
        else:
            i += 1

    return UCIInfo(
        depth=depth,
        seldepth=seldepth,
        score=score,
        pv=pv,
        nodes=nodes,
        time_ms=time_ms,
        nps=nps,
        multipv=multipv,
    )


def parse_bestmove(line: str) -> BestMove:
    """Parse a UCI bestmove line.

    Args:
        line: A UCI bestmove line (e.g., "bestmove e2e4 ponder e7e5").

    Returns:
        Parsed BestMove with move and optional ponder move.

    Raises:
        ValueError: If the line is not a valid bestmove response.
    """
    tokens = line.split()
    if len(tokens) < 2 or tokens[0] != "bestmove":
        raise ValueError(f"Invalid bestmove line: {line!r}")

    move = tokens[1]
    ponder: str | None = None
    if len(tokens) >= 4 and tokens[2] == "ponder":
        ponder = tokens[3]

    return BestMove(move=move, ponder=ponder)


class UCIClient:
    """Async UCI client for communicating with chess engine subprocesses.

    Manages the lifecycle of a UCI engine process and provides methods
    for sending commands and receiving typed responses.

    Attributes:
        engine_path: Path to the engine executable.
        default_timeout: Default timeout in seconds for commands.
    """

    def __init__(self, engine_path: str, *, default_timeout: float = 10.0) -> None:
        """Initialize the UCI client.

        Args:
            engine_path: Path to the engine executable.
            default_timeout: Default timeout in seconds for commands.
        """
        self.engine_path = engine_path
        self.default_timeout = default_timeout
        self._process: asyncio.subprocess.Process | None = None

    @property
    def is_running(self) -> bool:
        """Check if the engine process is currently running."""
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        """Start the engine subprocess.

        Raises:
            UCIEngineError: If the engine cannot be started.
        """
        if self.is_running:
            return

        try:
            argv = shlex.split(self.engine_path)
            self._process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info("Started engine process: %s (pid=%s)", self.engine_path, self._process.pid)
        except (FileNotFoundError, PermissionError, OSError) as e:
            raise UCIEngineError(f"Failed to start engine '{self.engine_path}': {e}") from e

    async def quit(self) -> None:
        """Send quit command and wait for the engine to terminate."""
        if not self.is_running:
            return

        assert self._process is not None
        try:
            await self._send("quit")
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except (asyncio.TimeoutError, UCIEngineError):
            logger.warning("Engine did not quit gracefully, terminating")
            self._process.kill()
            await self._process.wait()
        finally:
            self._process = None
            logger.info("Engine process terminated")

    async def uci(self, *, timeout: float | None = None) -> list[str]:
        """Send 'uci' command and wait for 'uciok'.

        Args:
            timeout: Timeout in seconds. Uses default_timeout if None.

        Returns:
            List of response lines received before uciok.

        Raises:
            UCITimeoutError: If the engine does not respond in time.
            UCIEngineError: If the engine process has terminated.
        """
        await self._send("uci")
        return await self._read_until("uciok", timeout=timeout)

    async def isready(self, *, timeout: float | None = None) -> None:
        """Send 'isready' command and wait for 'readyok'.

        Args:
            timeout: Timeout in seconds. Uses default_timeout if None.

        Raises:
            UCITimeoutError: If the engine does not respond in time.
            UCIEngineError: If the engine process has terminated.
        """
        await self._send("isready")
        await self._read_until("readyok", timeout=timeout)

    async def position(
        self,
        *,
        fen: str | None = None,
        moves: list[str] | None = None,
    ) -> None:
        """Send a position command.

        Args:
            fen: FEN string for the position. Uses startpos if None.
            moves: List of moves in UCI notation to apply after the position.
        """
        cmd = f"position fen {fen}" if fen is not None else "position startpos"

        if moves:
            cmd += " moves " + " ".join(moves)

        await self._send(cmd)

    async def go(
        self,
        time_control: TimeControl,
        *,
        timeout: float | None = None,
    ) -> tuple[BestMove, list[UCIInfo]]:
        """Send 'go' command with time control and wait for bestmove.

        Args:
            time_control: Time control parameters for the search.
            timeout: Timeout in seconds for the entire search.
                Uses default_timeout if None.

        Returns:
            Tuple of (BestMove, list of UCIInfo from info lines).

        Raises:
            UCITimeoutError: If the engine does not respond in time.
            UCIEngineError: If the engine process has terminated.
        """
        cmd = f"go {time_control.to_uci_params()}"
        await self._send(cmd)

        effective_timeout = timeout if timeout is not None else self.default_timeout
        deadline_ns = time.monotonic_ns() + int(effective_timeout * 1_000_000_000)

        infos: list[UCIInfo] = []
        while True:
            remaining_ns = deadline_ns - time.monotonic_ns()
            if remaining_ns <= 0:
                raise UCITimeoutError(f"Engine did not respond to 'go' within {effective_timeout}s")

            remaining_s = remaining_ns / 1_000_000_000
            line = await self._read_line(timeout=remaining_s)

            if line.startswith("info "):
                infos.append(parse_info_line(line))
            elif line.startswith("bestmove "):
                return parse_bestmove(line), infos

    async def stop(self) -> None:
        """Send 'stop' command to halt the engine's search."""
        await self._send("stop")

    async def _send(self, command: str) -> None:
        """Send a command to the engine.

        Args:
            command: UCI command string to send.

        Raises:
            UCIEngineError: If the engine process is not running.
        """
        if not self.is_running:
            raise UCIEngineError("Engine process is not running")

        assert self._process is not None
        assert self._process.stdin is not None

        logger.debug(">> %s", command)
        self._process.stdin.write(f"{command}\n".encode())
        await self._process.stdin.drain()

    async def _read_line(self, *, timeout: float | None = None) -> str:
        """Read a single line from the engine's stdout.

        Args:
            timeout: Timeout in seconds. Uses default_timeout if None.

        Returns:
            The line read from the engine, stripped of whitespace.

        Raises:
            UCITimeoutError: If no line is received in time.
            UCIEngineError: If the engine process has terminated.
        """
        if not self.is_running:
            raise UCIEngineError("Engine process is not running")

        assert self._process is not None
        assert self._process.stdout is not None

        effective_timeout = timeout if timeout is not None else self.default_timeout

        try:
            raw = await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            raise UCITimeoutError(f"Engine did not respond within {effective_timeout}s") from None

        if not raw:
            returncode = self._process.returncode
            raise UCIEngineError(
                f"Engine process terminated unexpectedly (returncode={returncode})"
            )

        line = raw.decode().strip()
        logger.debug("<< %s", line)
        return line

    async def _read_until(self, sentinel: str, *, timeout: float | None = None) -> list[str]:
        """Read lines until a sentinel line is received.

        Args:
            sentinel: The line to wait for (exact match).
            timeout: Overall timeout in seconds.

        Returns:
            List of lines received before the sentinel.

        Raises:
            UCITimeoutError: If the sentinel is not received in time.
            UCIEngineError: If the engine process has terminated.
        """
        effective_timeout = timeout if timeout is not None else self.default_timeout
        deadline_ns = time.monotonic_ns() + int(effective_timeout * 1_000_000_000)
        lines: list[str] = []

        while True:
            remaining_ns = deadline_ns - time.monotonic_ns()
            if remaining_ns <= 0:
                raise UCITimeoutError(
                    f"Engine did not send '{sentinel}' within {effective_timeout}s"
                )

            remaining_s = remaining_ns / 1_000_000_000
            line = await self._read_line(timeout=remaining_s)

            if line == sentinel:
                return lines
            lines.append(line)
