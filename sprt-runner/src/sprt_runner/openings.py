"""Opening book loader and pair generator for SPRT testing.

Loads opening positions from EPD and PGN files and generates opening
pairs where each opening is played twice with colours swapped to
eliminate first-move advantage bias.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import chess.pgn


@dataclass(frozen=True)
class OpeningPair:
    """An opening position with colour assignment.

    Attributes:
        fen: FEN string of the starting position.
        swap_colors: If True, the test engine plays white (and base plays black).
            Default is False, where the base engine plays white.
    """

    fen: str
    swap_colors: bool


def load_epd_openings(path: Path) -> list[str]:
    """Load opening positions from an EPD file.

    Each line in an EPD file contains a FEN position followed by optional
    operations separated by semicolons. Only the FEN portion is extracted.

    Args:
        path: Path to the EPD file.

    Returns:
        A list of FEN strings.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    if not path.is_file():
        raise FileNotFoundError(f"EPD file not found: {path}")

    fens: list[str] = []
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("%"):
            continue

        # EPD format: <4 FEN fields> [halfmove fullmove] [operations] [; more operations]
        # Split on semicolon first to remove trailing operations
        before_semi = stripped.split(";")[0].strip()
        if not before_semi:
            continue

        fields = before_semi.split()
        if len(fields) < 4:
            continue

        # Always take the first 4 FEN fields
        fen_fields = fields[:4]

        # Check if fields 5-6 are halfmove/fullmove counters (both numeric)
        if len(fields) >= 6 and fields[4].isdigit() and fields[5].isdigit():
            fen_fields = fields[:6]
        else:
            # 4-field EPD: append default halfmove clock and fullmove counter
            fen_fields.extend(["0", "1"])

        fens.append(" ".join(fen_fields))

    return fens


def load_pgn_openings(path: Path) -> list[str]:
    """Load opening positions from a PGN file.

    Each game in the PGN file is replayed and the final position's FEN
    is used as an opening. This allows PGN opening books where each
    game contains a partial game (the opening line).

    Args:
        path: Path to the PGN file.

    Returns:
        A list of FEN strings.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    if not path.is_file():
        raise FileNotFoundError(f"PGN file not found: {path}")

    fens: list[str] = []
    text = path.read_text(encoding="utf-8")
    pgn_io = io.StringIO(text)

    while True:
        game = chess.pgn.read_game(pgn_io)
        if game is None:
            break

        # Walk to the end of the mainline
        board = game.board()
        for move in game.mainline_moves():
            board.push(move)

        fens.append(board.fen())

    return fens


def load_openings(path: Path) -> list[str]:
    """Load opening positions from an EPD or PGN file.

    Dispatches to the appropriate loader based on file extension.

    Args:
        path: Path to the opening book file (.epd or .pgn).

    Returns:
        A list of FEN strings.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file extension is not supported.
    """
    suffix = path.suffix.lower()
    if suffix == ".epd":
        return load_epd_openings(path)
    if suffix == ".pgn":
        return load_pgn_openings(path)
    raise ValueError(f"Unsupported opening book format: {suffix!r} (expected .epd or .pgn)")


def make_opening_pairs(fens: list[str]) -> list[OpeningPair]:
    """Generate opening pairs from a list of FEN positions.

    Each FEN is used twice: once with normal colour assignment and once
    with swapped colours. This ensures fair testing by eliminating
    first-move advantage.

    Args:
        fens: List of FEN strings.

    Returns:
        A list of OpeningPair objects, two per input FEN.
    """
    pairs: list[OpeningPair] = []
    for fen in fens:
        pairs.append(OpeningPair(fen=fen, swap_colors=False))
        pairs.append(OpeningPair(fen=fen, swap_colors=True))
    return pairs
