"""Tests for UCI protocol compliance (mock stdin/stdout)."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import io
from unittest.mock import patch

from random_engine.engine import RandomEngine
from random_engine.uci import UCIHandler, extract_moves

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_handler(commands: list[str]) -> list[str]:
    """Run the UCI handler against a scripted list of input commands.

    Returns all lines printed to stdout (via print()).

    Args:
        commands: Lines to send as stdin input.

    Returns:
        List of output lines (stripped) written to stdout.
    """
    engine = RandomEngine()
    handler = UCIHandler(engine)

    stdin_text = "\n".join(commands) + "\n"
    output_lines: list[str] = []

    with (
        patch("sys.stdin", io.StringIO(stdin_text)),
        patch("builtins.print") as mock_print,
    ):
        handler.run()

    # Collect positional args from each print() call.
    for call in mock_print.call_args_list:
        output_lines.append(str(call.args[0]))

    return output_lines


# ---------------------------------------------------------------------------
# _extract_moves helper
# ---------------------------------------------------------------------------


class TestExtractMoves:
    """Tests for the _extract_moves module-level helper."""

    def test_returns_empty_when_no_moves_keyword(self) -> None:
        assert extract_moves([]) == []
        assert extract_moves(["e2e4"]) == []

    def test_returns_moves_after_keyword(self) -> None:
        assert extract_moves(["moves", "e2e4", "e7e5"]) == ["e2e4", "e7e5"]

    def test_moves_keyword_only_returns_empty(self) -> None:
        assert extract_moves(["moves"]) == []


# ---------------------------------------------------------------------------
# UCI handshake
# ---------------------------------------------------------------------------


class TestUCIHandshake:
    """Tests for the uci / uciok handshake."""

    def test_uci_sends_id_and_uciok(self) -> None:
        lines = _run_handler(["uci", "quit"])
        assert any(line.startswith("id name ") for line in lines)
        assert any(line.startswith("id author ") for line in lines)
        assert "uciok" in lines

    def test_isready_sends_readyok(self) -> None:
        lines = _run_handler(["isready", "quit"])
        assert "readyok" in lines

    def test_uci_then_isready(self) -> None:
        lines = _run_handler(["uci", "isready", "quit"])
        assert "uciok" in lines
        assert "readyok" in lines


# ---------------------------------------------------------------------------
# Position command
# ---------------------------------------------------------------------------


class TestPositionCommand:
    """Tests for the position command."""

    def test_position_startpos_sets_board(self) -> None:
        engine = RandomEngine()
        handler = UCIHandler(engine)
        handler._handle_position(["startpos"])
        import chess

        assert engine.board == chess.Board()

    def test_position_startpos_with_moves(self) -> None:
        engine = RandomEngine()
        handler = UCIHandler(engine)
        handler._handle_position(["startpos", "moves", "e2e4", "e7e5"])
        import chess

        assert engine.board.fullmove_number == 2
        assert engine.board.turn == chess.WHITE

    def test_position_fen(self) -> None:
        # Use a FEN without en-passant square; python-chess normalises that field.
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        engine = RandomEngine()
        handler = UCIHandler(engine)
        handler._handle_position(["fen", *fen.split()])
        assert engine.board.fen() == fen

    def test_position_fen_with_moves(self) -> None:
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        engine = RandomEngine()
        handler = UCIHandler(engine)
        handler._handle_position(["fen", *fen.split(), "moves", "e7e5"])
        import chess

        assert engine.board.turn == chess.WHITE

    def test_position_with_no_args_does_not_raise(self) -> None:
        """Unknown / missing args should be silently ignored."""
        engine = RandomEngine()
        handler = UCIHandler(engine)
        handler._handle_position([])  # should not raise


# ---------------------------------------------------------------------------
# Go command
# ---------------------------------------------------------------------------


class TestGoCommand:
    """Tests for the go command."""

    def test_go_outputs_info_and_bestmove(self) -> None:
        lines = _run_handler(["position startpos", "go", "quit"])
        assert any(line.startswith("info score cp 0 depth 0") for line in lines)
        assert any(line.startswith("bestmove ") for line in lines)

    def test_go_bestmove_is_legal(self) -> None:
        """The bestmove reported must be a legal move in the position."""
        import chess

        engine = RandomEngine()
        handler = UCIHandler(engine)

        output: list[str] = []

        def _capture(msg: str, **_kw: object) -> None:
            output.append(msg)

        with patch("builtins.print", side_effect=_capture):
            handler._handle_go()

        bestmove_lines = [ln for ln in output if ln.startswith("bestmove ")]
        assert len(bestmove_lines) == 1
        move_str = bestmove_lines[0].split()[1]
        move = chess.Move.from_uci(move_str)
        assert move in chess.Board().legal_moves

    def test_go_with_time_control_args_ignored(self) -> None:
        """go with wtime/btime/etc. should still produce a bestmove."""
        lines = _run_handler(
            ["position startpos", "go wtime 60000 btime 60000 winc 0 binc 0", "quit"]
        )
        assert any(line.startswith("bestmove ") for line in lines)

    def test_go_on_checkmate_reports_none(self) -> None:
        """In a terminal position, bestmove (none) should be reported."""
        # Fool's Mate position — checkmate.
        fool_moves = ["f2f3", "e7e5", "g2g4", "d8h4"]
        lines = _run_handler([f"position startpos moves {' '.join(fool_moves)}", "go", "quit"])
        assert any(line == "bestmove (none)" for line in lines)


# ---------------------------------------------------------------------------
# ucinewgame
# ---------------------------------------------------------------------------


class TestUCINewGame:
    """Tests for the ucinewgame command."""

    def test_ucinewgame_resets_board(self) -> None:
        import chess

        engine = RandomEngine()
        handler = UCIHandler(engine)
        # Set up a position first.
        engine.set_position_startpos(moves=["e2e4"])
        handler._handle_ucinewgame()
        assert engine.board == chess.Board()


# ---------------------------------------------------------------------------
# Quit
# ---------------------------------------------------------------------------


class TestQuitCommand:
    """Tests for the quit command."""

    def test_quit_stops_the_loop(self) -> None:
        """After quit, no further commands should be processed."""
        output: list[str] = []

        def _capture(msg: str, **_kw: object) -> None:
            output.append(msg)

        with (
            patch("sys.stdin", io.StringIO("quit\nisready\n")),
            patch("builtins.print", side_effect=_capture),
        ):
            engine = RandomEngine()
            handler = UCIHandler(engine)
            handler.run()

        # readyok must NOT appear because loop exited on quit.
        assert "readyok" not in output

    def test_unknown_commands_are_ignored(self) -> None:
        """Unrecognised tokens must not raise exceptions."""
        lines = _run_handler(["unknowncmd foo bar", "isready", "quit"])
        assert "readyok" in lines


# ---------------------------------------------------------------------------
# Full session smoke test
# ---------------------------------------------------------------------------


class TestFullSession:
    """End-to-end smoke test for a typical UCI session."""

    def test_typical_session(self) -> None:
        session = [
            "uci",
            "isready",
            "ucinewgame",
            "position startpos",
            "go",
            "position startpos moves e2e4",
            "go",
            "quit",
        ]
        lines = _run_handler(session)

        assert "uciok" in lines
        assert "readyok" in lines
        # Two go commands → two bestmove lines
        bestmove_lines = [ln for ln in lines if ln.startswith("bestmove ")]
        assert len(bestmove_lines) == 2
