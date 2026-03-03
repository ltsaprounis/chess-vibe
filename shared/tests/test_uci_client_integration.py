"""Integration tests for UCIClient with real engine subprocess.

These tests start random-engine as a real subprocess and verify the full
UCI protocol works end-to-end. They are marked with @pytest.mark.integration
so they can be run or skipped separately.
"""

from __future__ import annotations

import chess
import pytest
from shared.time_control import DepthTimeControl
from shared.uci_client import UCIClient

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
