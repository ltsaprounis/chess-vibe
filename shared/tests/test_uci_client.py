"""Tests for UCI client with mock subprocess."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.time_control import DepthTimeControl, FixedTimeControl
from shared.uci_client import (
    BestMove,
    UCIClient,
    UCIEngineError,
    UCIInfo,
    UCIScore,
    UCITimeoutError,
    parse_bestmove,
    parse_info_line,
)

# ---------------------------------------------------------------------------
# Parsing tests (no subprocess needed)
# ---------------------------------------------------------------------------


class TestParseInfoLine:
    """Tests for parse_info_line."""

    def test_basic_info(self) -> None:
        line = (
            "info depth 20 seldepth 25 score cp 35 nodes 1000000 time 500 nps 2000000 pv e2e4 e7e5"
        )
        info = parse_info_line(line)
        assert info.depth == 20
        assert info.seldepth == 25
        assert info.score == UCIScore(cp=35)
        assert info.nodes == 1000000
        assert info.time_ms == 500
        assert info.nps == 2000000
        assert info.pv == ["e2e4", "e7e5"]

    def test_mate_score(self) -> None:
        line = "info depth 15 score mate 3 pv e2e4"
        info = parse_info_line(line)
        assert info.score == UCIScore(mate=3)

    def test_negative_mate_score(self) -> None:
        line = "info depth 15 score mate -2"
        info = parse_info_line(line)
        assert info.score == UCIScore(mate=-2)

    def test_negative_cp_score(self) -> None:
        line = "info depth 10 score cp -150"
        info = parse_info_line(line)
        assert info.score == UCIScore(cp=-150)

    def test_depth_only(self) -> None:
        line = "info depth 5"
        info = parse_info_line(line)
        assert info.depth == 5
        assert info.score is None
        assert info.pv == []

    def test_multipv(self) -> None:
        line = "info depth 10 multipv 2 score cp -30 pv d2d4"
        info = parse_info_line(line)
        assert info.multipv == 2
        assert info.pv == ["d2d4"]

    def test_empty_pv(self) -> None:
        line = "info depth 1 score cp 0"
        info = parse_info_line(line)
        assert info.pv == []

    def test_without_info_prefix(self) -> None:
        line = "depth 10 score cp 50"
        info = parse_info_line(line)
        assert info.depth == 10
        assert info.score == UCIScore(cp=50)


class TestParseBestMove:
    """Tests for parse_bestmove."""

    def test_simple_bestmove(self) -> None:
        result = parse_bestmove("bestmove e2e4")
        assert result == BestMove(move="e2e4")

    def test_bestmove_with_ponder(self) -> None:
        result = parse_bestmove("bestmove e2e4 ponder e7e5")
        assert result == BestMove(move="e2e4", ponder="e7e5")

    def test_invalid_bestmove_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid bestmove line"):
            parse_bestmove("info depth 10")

    def test_empty_bestmove_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid bestmove line"):
            parse_bestmove("bestmove")

    def test_null_move(self) -> None:
        result = parse_bestmove("bestmove (none)")
        assert result == BestMove(move="(none)")


class TestUCIScore:
    """Tests for UCIScore validation."""

    def test_cp_score(self) -> None:
        score = UCIScore(cp=100)
        assert score.cp == 100
        assert score.mate is None

    def test_mate_score(self) -> None:
        score = UCIScore(mate=3)
        assert score.mate == 3
        assert score.cp is None

    def test_neither_raises(self) -> None:
        with pytest.raises(ValueError, match="Either cp or mate must be set"):
            UCIScore()

    def test_both_raises(self) -> None:
        with pytest.raises(ValueError, match="Only one of cp or mate can be set"):
            UCIScore(cp=100, mate=3)


class TestUCIInfo:
    """Tests for UCIInfo dataclass."""

    def test_defaults(self) -> None:
        info = UCIInfo()
        assert info.depth is None
        assert info.seldepth is None
        assert info.score is None
        assert info.pv == []
        assert info.nodes is None
        assert info.time_ms is None
        assert info.nps is None
        assert info.multipv is None


# ---------------------------------------------------------------------------
# Mock subprocess helpers
# ---------------------------------------------------------------------------


def _make_mock_process(responses: list[str]) -> MagicMock:
    """Create a mock asyncio subprocess with scripted responses.

    Args:
        responses: List of lines the mock engine will output.

    Returns:
        A MagicMock configured to simulate an engine subprocess.
    """
    process = MagicMock()
    process.returncode = None
    process.pid = 12345

    # Mock stdin
    stdin = MagicMock()
    stdin.write = MagicMock()
    stdin.drain = AsyncMock()
    process.stdin = stdin

    # Mock stdout with scripted responses
    encoded_responses = [f"{line}\n".encode() for line in responses]
    stdout = MagicMock()
    readline_mock = AsyncMock(side_effect=encoded_responses)
    stdout.readline = readline_mock
    process.stdout = stdout

    # Mock stderr
    process.stderr = MagicMock()

    # Mock wait/kill
    process.wait = AsyncMock(return_value=0)
    process.kill = MagicMock()

    return process


# ---------------------------------------------------------------------------
# UCIClient integration tests with mock subprocess
# ---------------------------------------------------------------------------


class TestUCIClientStartStop:
    """Tests for UCI client start and quit."""

    @pytest.mark.asyncio
    async def test_start_creates_process(self) -> None:
        client = UCIClient("/fake/engine")
        mock_proc = _make_mock_process([])

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            assert client.is_running
            mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_splits_command_with_arguments(self) -> None:
        client = UCIClient("python -m random_engine")
        mock_proc = _make_mock_process([])

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            assert client.is_running
            mock_exec.assert_called_once_with(
                "python",
                "-m",
                "random_engine",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

    @pytest.mark.asyncio
    async def test_start_when_already_running(self) -> None:
        client = UCIClient("/fake/engine")
        mock_proc = _make_mock_process([])

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            await client.start()  # Should be a no-op
            mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_file_not_found(self) -> None:
        client = UCIClient("/nonexistent/engine")

        with (
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                side_effect=FileNotFoundError("not found"),
            ),
            pytest.raises(UCIEngineError, match="Failed to start engine"),
        ):
            await client.start()

    @pytest.mark.asyncio
    async def test_start_permission_error(self) -> None:
        client = UCIClient("/no/permission")

        with (
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                side_effect=PermissionError("denied"),
            ),
            pytest.raises(UCIEngineError, match="Failed to start engine"),
        ):
            await client.start()

    @pytest.mark.asyncio
    async def test_quit_sends_quit_command(self) -> None:
        client = UCIClient("/fake/engine")
        mock_proc = _make_mock_process([])

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            await client.quit()
            assert not client.is_running

    @pytest.mark.asyncio
    async def test_quit_when_not_running(self) -> None:
        client = UCIClient("/fake/engine")
        await client.quit()  # Should be a no-op

    @pytest.mark.asyncio
    async def test_is_running_false_initially(self) -> None:
        client = UCIClient("/fake/engine")
        assert not client.is_running


class TestUCIClientProtocol:
    """Tests for UCI protocol commands."""

    @pytest.mark.asyncio
    async def test_uci_command(self) -> None:
        responses = [
            "id name TestEngine",
            "id author TestAuthor",
            "uciok",
        ]
        client = UCIClient("/fake/engine")
        mock_proc = _make_mock_process(responses)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            lines = await client.uci()
            assert "id name TestEngine" in lines
            assert "id author TestAuthor" in lines

    @pytest.mark.asyncio
    async def test_isready_command(self) -> None:
        responses = ["readyok"]
        client = UCIClient("/fake/engine")
        mock_proc = _make_mock_process(responses)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            await client.isready()  # Should not raise

    @pytest.mark.asyncio
    async def test_position_startpos(self) -> None:
        client = UCIClient("/fake/engine")
        mock_proc = _make_mock_process([])

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            await client.position()
            mock_proc.stdin.write.assert_called_with(b"position startpos\n")

    @pytest.mark.asyncio
    async def test_position_with_fen(self) -> None:
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        client = UCIClient("/fake/engine")
        mock_proc = _make_mock_process([])

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            await client.position(fen=fen)
            mock_proc.stdin.write.assert_called_with(f"position fen {fen}\n".encode())

    @pytest.mark.asyncio
    async def test_position_with_moves(self) -> None:
        client = UCIClient("/fake/engine")
        mock_proc = _make_mock_process([])

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            await client.position(moves=["e2e4", "e7e5"])
            mock_proc.stdin.write.assert_called_with(b"position startpos moves e2e4 e7e5\n")

    @pytest.mark.asyncio
    async def test_go_with_depth(self) -> None:
        responses = [
            "info depth 5 score cp 30 pv e2e4 e7e5",
            "info depth 10 score cp 25 nodes 50000 time 100 nps 500000 pv e2e4 e7e5 g1f3",
            "bestmove e2e4 ponder e7e5",
        ]
        client = UCIClient("/fake/engine")
        mock_proc = _make_mock_process(responses)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            bestmove, infos = await client.go(DepthTimeControl(depth=10))

            assert bestmove == BestMove(move="e2e4", ponder="e7e5")
            assert len(infos) == 2
            assert infos[0].depth == 5
            assert infos[1].depth == 10
            assert infos[1].nodes == 50000

    @pytest.mark.asyncio
    async def test_go_with_movetime(self) -> None:
        responses = ["bestmove d2d4"]
        client = UCIClient("/fake/engine")
        mock_proc = _make_mock_process(responses)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            bestmove, infos = await client.go(FixedTimeControl(movetime_ms=1000))

            assert bestmove == BestMove(move="d2d4")
            assert infos == []
            mock_proc.stdin.write.assert_called_with(b"go movetime 1000\n")

    @pytest.mark.asyncio
    async def test_stop_command(self) -> None:
        client = UCIClient("/fake/engine")
        mock_proc = _make_mock_process([])

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            await client.stop()
            mock_proc.stdin.write.assert_called_with(b"stop\n")


class TestUCIClientErrorHandling:
    """Tests for UCI client error handling."""

    @pytest.mark.asyncio
    async def test_send_when_not_running_raises(self) -> None:
        client = UCIClient("/fake/engine")
        with pytest.raises(UCIEngineError, match="Engine process is not running"):
            await client.uci()

    @pytest.mark.asyncio
    async def test_timeout_on_uci(self) -> None:
        client = UCIClient("/fake/engine", default_timeout=0.01)
        mock_proc = _make_mock_process([])

        # Make readline hang (timeout)
        mock_proc.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            with pytest.raises(UCITimeoutError):
                await client.uci(timeout=0.01)

    @pytest.mark.asyncio
    async def test_engine_crash_detected(self) -> None:
        client = UCIClient("/fake/engine")
        mock_proc = _make_mock_process([])

        # Simulate crash: readline returns empty bytes, returncode becomes set
        async def crash_readline() -> bytes:
            mock_proc.returncode = 1
            return b""

        mock_proc.stdout.readline = AsyncMock(side_effect=crash_readline)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            with pytest.raises(UCIEngineError, match="terminated unexpectedly"):
                await client.uci()

    @pytest.mark.asyncio
    async def test_custom_timeout(self) -> None:
        client = UCIClient("/fake/engine", default_timeout=30.0)
        mock_proc = _make_mock_process(["readyok"])

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()
            await client.isready(timeout=5.0)  # Custom timeout, should succeed

    @pytest.mark.asyncio
    async def test_quit_force_kills_on_timeout(self) -> None:
        client = UCIClient("/fake/engine")
        mock_proc = _make_mock_process([])
        mock_proc.wait = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await client.start()

            # Need a second wait mock for after kill
            mock_proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError(), 0])
            # Override to succeed after kill
            killed = False

            async def wait_side_effect() -> int:
                nonlocal killed
                if not killed:
                    killed = True
                    raise asyncio.TimeoutError
                return 0

            mock_proc.wait = AsyncMock(side_effect=wait_side_effect)

            await client.quit()
            mock_proc.kill.assert_called_once()
            assert not client.is_running
