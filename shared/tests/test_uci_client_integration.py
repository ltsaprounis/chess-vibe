"""Integration tests for UCIClient with real engine subprocess.

These tests start random-engine as a real subprocess and verify the full
UCI protocol works end-to-end. They are marked with @pytest.mark.integration
so they can be run or skipped separately.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import signal
import time
from pathlib import Path

import chess
import pytest
from shared.time_control import DepthTimeControl
from shared.uci_client import UCIClient, UCIEngineError

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_uci_handshake(random_engine_command: str) -> None:
    """Verify UCI handshake: uci → uciok, isready → readyok."""
    client = UCIClient(random_engine_command)
    await client.start()
    try:
        assert client.is_running

        lines = await client.uci()
        # Engine must identify itself before uciok
        assert any("id name" in line for line in lines)

        # isready → readyok (no exception means success)
        await client.isready()
    finally:
        await client.quit()


@pytest.mark.asyncio
async def test_go_returns_legal_bestmove(random_engine_command: str) -> None:
    """Send a position and go command, verify the bestmove is legal."""
    client = UCIClient(random_engine_command)
    await client.start()
    try:
        await client.uci()
        await client.isready()

        # Set starting position
        await client.position()
        bestmove, _infos = await client.go(DepthTimeControl(depth=1))

        # Validate the returned move is legal in the starting position
        board = chess.Board()
        move = chess.Move.from_uci(bestmove.move)
        assert move in board.legal_moves, (
            f"Engine returned illegal move {bestmove.move} from starting position"
        )
    finally:
        await client.quit()


@pytest.mark.asyncio
async def test_go_from_custom_fen(random_engine_command: str) -> None:
    """Send a custom FEN position and verify the bestmove is legal."""
    fen = "r1bqkbnr/pppppppp/2n5/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 1 2"
    client = UCIClient(random_engine_command)
    await client.start()
    try:
        await client.uci()
        await client.isready()

        await client.position(fen=fen)
        bestmove, _infos = await client.go(DepthTimeControl(depth=1))

        board = chess.Board(fen)
        move = chess.Move.from_uci(bestmove.move)
        assert move in board.legal_moves, (
            f"Engine returned illegal move {bestmove.move} from FEN {fen}"
        )
    finally:
        await client.quit()


@pytest.mark.asyncio
async def test_quit_terminates_subprocess(random_engine_command: str) -> None:
    """Verify quit command cleanly terminates the engine subprocess."""
    client = UCIClient(random_engine_command)
    await client.start()
    assert client.is_running

    await client.uci()
    await client.isready()
    await client.quit()

    assert not client.is_running


@pytest.mark.asyncio
async def test_engine_command_with_shlex_split(random_engine_command: str) -> None:
    """Verify engine path with arguments works via shlex.split."""
    # random_engine_command is e.g. "/path/to/.venv/bin/python -m random_engine"
    # which requires shlex.split to separate program from arguments.
    assert " " in random_engine_command, (
        "Expected engine command to contain spaces (program + arguments)"
    )

    client = UCIClient(random_engine_command)
    await client.start()
    try:
        assert client.is_running

        lines = await client.uci()
        assert any("id name" in line for line in lines)

        await client.isready()
    finally:
        await client.quit()


def _process_exists(pid: int) -> bool:
    """Return True while ``pid`` is running or hasn't been reaped."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.asyncio
async def test_quit_kills_an_engine_that_closed_its_pipes(tmp_path: Path) -> None:
    """An engine that closed its input and output but still runs is killed and reaped (#209).

    The shell writes its pid, closes stdin and stdout, and becomes ``sleep``.
    ``quit`` can't be written into its closed input, so quit() kills it at
    once, without the 5 s it gives an engine to exit, and doesn't raise.
    """
    pid_file = tmp_path / "engine.pid"
    script = f'echo $$ > "{pid_file}"; exec 0<&- 1>&-; exec sleep 60'
    client = UCIClient(shlex.join(["sh", "-c", script]))
    await client.start()
    pid: int | None = None
    try:
        deadline = time.monotonic() + 10
        while not (pid_file.exists() and pid_file.read_text().strip()):
            assert time.monotonic() < deadline, "the engine didn't write its pid within 10 s"
            await asyncio.sleep(0.02)
        pid = int(pid_file.read_text())
        # Fails one way or the other once the engine has closed its pipes.
        with pytest.raises((UCIEngineError, ConnectionError)):
            await client.uci(timeout=10)

        started = time.monotonic()
        raised: ConnectionError | None = None
        try:
            await asyncio.wait_for(client.quit(), timeout=10)
        except ConnectionError as e:
            raised = e
        took = time.monotonic() - started

        assert not _process_exists(pid), "the engine is still running"
        pid = None  # reaped, so the number may belong to someone else now
        assert raised is None
        assert took < 4, f"quit() took {took:.1f} s"
    finally:
        if pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
