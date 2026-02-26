"""Tests for random_engine.uci — UCI protocol compliance.

These tests mock stdin/stdout to simulate a chess GUI talking to the engine
over the UCI text protocol.  They verify that:

* The UCI handshake (``uci`` → ``id`` + ``uciok``) works.
* ``isready`` → ``readyok`` works.
* ``position startpos`` and ``position fen`` set the board correctly.
* ``go`` returns ``info score cp 0 depth 0`` followed by ``bestmove <legal>``.
* ``quit`` terminates the loop.
* Unknown commands are silently ignored.
"""

from __future__ import annotations

import io
from unittest.mock import patch

import chess
from random_engine.uci import ENGINE_AUTHOR, ENGINE_NAME, run_uci_loop


def _run_uci_session(commands: list[str]) -> list[str]:
    """Feed *commands* to the UCI loop and return the output lines.

    Each command string is joined with newlines and piped to stdin.
    The function captures stdout and returns the non-empty output lines.

    Args:
        commands: UCI commands to send (one per list element).

    Returns:
        Non-empty output lines written by the engine.
    """
    stdin_text = "\n".join(commands) + "\n"
    fake_stdin = io.StringIO(stdin_text)
    fake_stdout = io.StringIO()

    with patch("sys.stdin", fake_stdin), patch("sys.stdout", fake_stdout):
        run_uci_loop()

    return [line for line in fake_stdout.getvalue().splitlines() if line]


# ---------------------------------------------------------------------------
# UCI handshake
# ---------------------------------------------------------------------------


class TestUCIHandshake:
    """Tests for the ``uci`` command."""

    def test_uci_reports_id_name(self) -> None:
        output = _run_uci_session(["uci", "quit"])
        assert f"id name {ENGINE_NAME}" in output

    def test_uci_reports_id_author(self) -> None:
        output = _run_uci_session(["uci", "quit"])
        assert f"id author {ENGINE_AUTHOR}" in output

    def test_uci_ends_with_uciok(self) -> None:
        output = _run_uci_session(["uci", "quit"])
        # uciok must appear after id lines.
        uciok_idx = output.index("uciok")
        name_idx = output.index(f"id name {ENGINE_NAME}")
        author_idx = output.index(f"id author {ENGINE_AUTHOR}")
        assert uciok_idx > name_idx
        assert uciok_idx > author_idx


# ---------------------------------------------------------------------------
# isready
# ---------------------------------------------------------------------------


class TestIsReady:
    """Tests for the ``isready`` command."""

    def test_isready_responds_readyok(self) -> None:
        output = _run_uci_session(["isready", "quit"])
        assert "readyok" in output

    def test_isready_after_uci(self) -> None:
        output = _run_uci_session(["uci", "isready", "quit"])
        assert "readyok" in output


# ---------------------------------------------------------------------------
# position + go
# ---------------------------------------------------------------------------


class TestPositionAndGo:
    """Tests for ``position`` and ``go`` commands."""

    def test_go_from_startpos_returns_legal_move(self) -> None:
        output = _run_uci_session(["position startpos", "go", "quit"])
        bestmove_lines = [line for line in output if line.startswith("bestmove")]
        assert len(bestmove_lines) == 1
        move_str = bestmove_lines[0].split()[1]
        board = chess.Board()
        parsed = chess.Move.from_uci(move_str)
        assert parsed in board.legal_moves

    def test_go_reports_info_before_bestmove(self) -> None:
        output = _run_uci_session(["position startpos", "go", "quit"])
        info_lines = [line for line in output if line.startswith("info")]
        bestmove_lines = [line for line in output if line.startswith("bestmove")]
        assert len(info_lines) >= 1
        assert "score cp 0 depth 0" in info_lines[0]
        # info must come before bestmove.
        info_idx = output.index(info_lines[0])
        bestmove_idx = output.index(bestmove_lines[0])
        assert info_idx < bestmove_idx

    def test_go_after_startpos_with_moves(self) -> None:
        output = _run_uci_session(["position startpos moves e2e4 e7e5", "go", "quit"])
        bestmove_lines = [line for line in output if line.startswith("bestmove")]
        assert len(bestmove_lines) == 1
        move_str = bestmove_lines[0].split()[1]
        board = chess.Board()
        board.push_uci("e2e4")
        board.push_uci("e7e5")
        parsed = chess.Move.from_uci(move_str)
        assert parsed in board.legal_moves

    def test_go_after_fen(self) -> None:
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        output = _run_uci_session([f"position fen {fen}", "go", "quit"])
        bestmove_lines = [line for line in output if line.startswith("bestmove")]
        assert len(bestmove_lines) == 1
        move_str = bestmove_lines[0].split()[1]
        board = chess.Board(fen)
        parsed = chess.Move.from_uci(move_str)
        assert parsed in board.legal_moves

    def test_go_after_fen_with_moves(self) -> None:
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        output = _run_uci_session([f"position fen {fen} moves e7e5", "go", "quit"])
        bestmove_lines = [line for line in output if line.startswith("bestmove")]
        assert len(bestmove_lines) == 1
        move_str = bestmove_lines[0].split()[1]
        board = chess.Board(fen)
        board.push_uci("e7e5")
        parsed = chess.Move.from_uci(move_str)
        assert parsed in board.legal_moves

    def test_go_with_time_control_params_ignored(self) -> None:
        """The random engine ignores time-control params but must not crash."""
        output = _run_uci_session(
            ["position startpos", "go wtime 300000 btime 300000 winc 0 binc 0", "quit"]
        )
        bestmove_lines = [line for line in output if line.startswith("bestmove")]
        assert len(bestmove_lines) == 1


# ---------------------------------------------------------------------------
# quit
# ---------------------------------------------------------------------------


class TestQuit:
    """Tests for the ``quit`` command."""

    def test_quit_exits_loop(self) -> None:
        # If quit doesn't work, the loop would hang; this test just
        # verifies it terminates cleanly.
        output = _run_uci_session(["quit"])
        # No bestmove or error expected.
        assert all(not line.startswith("bestmove") for line in output)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge-case tests for the UCI loop."""

    def test_unknown_command_is_ignored(self) -> None:
        output = _run_uci_session(["xyzzy", "isready", "quit"])
        assert "readyok" in output

    def test_empty_lines_are_ignored(self) -> None:
        output = _run_uci_session(["", "isready", "", "quit"])
        assert "readyok" in output

    def test_multiple_go_commands(self) -> None:
        output = _run_uci_session(["position startpos", "go", "position startpos", "go", "quit"])
        bestmove_lines = [line for line in output if line.startswith("bestmove")]
        assert len(bestmove_lines) == 2
