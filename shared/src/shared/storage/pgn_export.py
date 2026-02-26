"""PGN export utility for chess-vibe.

Converts a :class:`~shared.storage.models.Game` into a standard PGN string
with engine evaluation data embedded as move comments.

Typical usage::

    from shared.storage.pgn_export import export_game_to_pgn

    pgn_text = export_game_to_pgn(game)
"""

from __future__ import annotations

import chess
import chess.pgn

from shared.storage.models import Game, Move


def _eval_comment(move: Move) -> str:
    """Build an evaluation comment string for a single move.

    Format examples:
        ``+0.35/20``  (centipawn score / depth)
        ``M3/15``     (mate score / depth)
        ``+0.35/20 25s``  (with time)
    """
    parts: list[str] = []

    # Score
    if move.score_cp is not None:
        score_str = f"{move.score_cp / 100:+.2f}"
        parts.append(score_str)
    elif move.score_mate is not None:
        parts.append(f"M{move.score_mate}")

    # Depth
    if move.depth is not None:
        if parts:
            parts[-1] += f"/{move.depth}"
        else:
            parts.append(f"d={move.depth}")

    # Time
    if move.time_ms is not None:
        seconds = move.time_ms / 1000
        parts.append(f"{seconds:.1f}s")

    # Clocks
    clock_parts: list[str] = []
    if move.clock_white_ms is not None:
        clock_parts.append(f"wclk={move.clock_white_ms / 1000:.1f}s")
    if move.clock_black_ms is not None:
        clock_parts.append(f"bclk={move.clock_black_ms / 1000:.1f}s")
    if clock_parts:
        parts.append(" ".join(clock_parts))

    return " ".join(parts)


def export_game_to_pgn(game: Game) -> str:
    """Export a :class:`Game` to a standard PGN string with eval comments.

    Args:
        game: The game to export.

    Returns:
        A PGN-formatted string including headers and move text.
    """
    pgn_game = chess.pgn.Game()

    # -- Headers ----------------------------------------------------------
    pgn_game.headers["White"] = game.white_engine
    pgn_game.headers["Black"] = game.black_engine
    pgn_game.headers["Result"] = game.result.value

    if game.opening_name:
        pgn_game.headers["Opening"] = game.opening_name

    if game.start_fen:
        pgn_game.headers["FEN"] = game.start_fen
        pgn_game.headers["SetUp"] = "1"

    # -- Moves ------------------------------------------------------------
    node: chess.pgn.GameNode = pgn_game
    for move in game.moves:
        chess_move = chess.Move.from_uci(move.uci)
        node = node.add_variation(chess_move)

        comment = _eval_comment(move)
        if comment:
            node.comment = comment

    pgn_game.headers["Result"] = game.result.value

    # -- Export -----------------------------------------------------------
    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=True)
    pgn_str: str = pgn_game.accept(exporter)

    # Ensure trailing newline for POSIX compliance.
    if not pgn_str.endswith("\n"):
        pgn_str += "\n"

    return pgn_str
