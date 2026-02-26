"""Tests for the PGN export utility."""

from __future__ import annotations

import io
from datetime import datetime

import chess
import chess.pgn
from shared.storage.models import Game, GameResult, Move
from shared.storage.pgn_export import export_game_to_pgn

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_game_with_moves() -> Game:
    """Create a short game (Scholar's Mate) for export tests."""
    fen1 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    fen2 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
    fen3 = "rnbqkbnr/pppp1ppp/8/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR b KQkq - 1 2"
    fen4 = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 2 3"
    fen5 = "r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 3 3"
    fen6 = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
    fen7 = "r1bqkb1r/pppp1Qpp/2n2n2/4p3/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 0 4"
    moves = [
        Move(uci="e2e4", san="e4", fen_after=fen1, score_cp=35, depth=20, time_ms=100),
        Move(uci="e7e5", san="e5", fen_after=fen2, score_cp=-30, depth=18, time_ms=200),
        Move(uci="f1c4", san="Bc4", fen_after=fen3, score_cp=40, depth=22, time_ms=150),
        Move(uci="b8c6", san="Nc6", fen_after=fen4),
        Move(
            uci="d1h5",
            san="Qh5",
            fen_after=fen5,
            score_cp=150,
            depth=25,
            time_ms=300,
            clock_white_ms=59000,
            clock_black_ms=58000,
        ),
        Move(uci="g8f6", san="Nf6", fen_after=fen6, score_cp=-200, depth=20),
        Move(uci="h5f7", san="Qxf7#", fen_after=fen7, score_mate=1, depth=30),
    ]
    return Game(
        id="pgn-test-game",
        white_engine="engine-white",
        black_engine="engine-black",
        result=GameResult.WHITE_WIN,
        moves=moves,
        created_at=datetime(2025, 1, 15, 12, 0, 0),
        opening_name="Italian Game",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExportGameToPgn:
    """Tests for the PGN export function."""

    def test_produces_valid_pgn(self) -> None:
        game = _make_game_with_moves()
        pgn_text = export_game_to_pgn(game)

        # python-chess should be able to parse it back.
        pgn_game = chess.pgn.read_game(io.StringIO(pgn_text))
        assert pgn_game is not None

    def test_headers_correct(self) -> None:
        game = _make_game_with_moves()
        pgn_text = export_game_to_pgn(game)
        pgn_game = chess.pgn.read_game(io.StringIO(pgn_text))
        assert pgn_game is not None

        assert pgn_game.headers["White"] == "engine-white"
        assert pgn_game.headers["Black"] == "engine-black"
        assert pgn_game.headers["Result"] == "1-0"
        assert pgn_game.headers["Opening"] == "Italian Game"

    def test_move_count(self) -> None:
        game = _make_game_with_moves()
        pgn_text = export_game_to_pgn(game)
        pgn_game = chess.pgn.read_game(io.StringIO(pgn_text))
        assert pgn_game is not None

        moves = list(pgn_game.mainline_moves())
        assert len(moves) == 7

    def test_eval_comments_present(self) -> None:
        game = _make_game_with_moves()
        pgn_text = export_game_to_pgn(game)

        # Centipawn score should appear as a signed float.
        assert "+0.35" in pgn_text
        # Mate score should appear.
        assert "M1" in pgn_text

    def test_result_in_pgn(self) -> None:
        game = _make_game_with_moves()
        pgn_text = export_game_to_pgn(game)
        assert "1-0" in pgn_text

    def test_empty_game(self) -> None:
        game = Game(
            id="empty",
            white_engine="w",
            black_engine="b",
            result=GameResult.DRAW,
            moves=[],
            created_at=datetime(2025, 1, 1),
        )
        pgn_text = export_game_to_pgn(game)
        pgn_game = chess.pgn.read_game(io.StringIO(pgn_text))
        assert pgn_game is not None
        assert pgn_game.headers["Result"] == "1/2-1/2"
        assert list(pgn_game.mainline_moves()) == []

    def test_custom_start_fen(self) -> None:
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        game = Game(
            id="custom-fen",
            white_engine="w",
            black_engine="b",
            result=GameResult.UNFINISHED,
            moves=[
                Move(
                    uci="e7e5",
                    san="e5",
                    fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                )
            ],
            created_at=datetime(2025, 1, 1),
            start_fen=fen,
        )
        pgn_text = export_game_to_pgn(game)
        assert '[FEN "' in pgn_text
        assert '[SetUp "1"]' in pgn_text

    def test_move_without_eval_has_no_comment(self) -> None:
        game = Game(
            id="no-eval",
            white_engine="w",
            black_engine="b",
            result=GameResult.WHITE_WIN,
            moves=[
                Move(
                    uci="e2e4",
                    san="e4",
                    fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                )
            ],
            created_at=datetime(2025, 1, 1),
        )
        pgn_text = export_game_to_pgn(game)
        pgn_game = chess.pgn.read_game(io.StringIO(pgn_text))
        assert pgn_game is not None
        node = pgn_game.next()
        assert node is not None
        assert node.comment == ""

    def test_clock_annotations(self) -> None:
        game = _make_game_with_moves()
        pgn_text = export_game_to_pgn(game)
        # The 5th move has clock annotations.
        assert "wclk=" in pgn_text
        assert "bclk=" in pgn_text

    def test_trailing_newline(self) -> None:
        game = _make_game_with_moves()
        pgn_text = export_game_to_pgn(game)
        assert pgn_text.endswith("\n")
