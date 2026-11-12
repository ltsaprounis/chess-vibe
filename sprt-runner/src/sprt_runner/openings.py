"""Opening book loader and pair generator for SPRT testing.

Loads opening positions from EPD and PGN files, puts them in an order a
seed fixes, and generates opening pairs where each opening is played
twice with colours swapped to eliminate first-move advantage bias.
"""

from __future__ import annotations

import io
import random
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
    game contains a partial game (the opening line). A game with no moves
    and no ``FEN`` header is skipped, since it describes no opening (#12).

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

        # A game with no moves and no FEN describes no opening. It's what
        # python-chess reads from text that isn't PGN, or from a game whose
        # first move is illegal, and kept it would be the initial position.
        moves = list(game.mainline_moves())
        if not moves and "FEN" not in game.headers:
            continue

        # Walk to the end of the mainline
        board = game.board()
        for move in moves:
            board.push(move)

        fens.append(board.fen())

    return fens


def load_openings(path: Path) -> list[str]:
    """Load opening positions from an EPD or PGN file.

    Dispatches to the appropriate loader based on file extension.

    Args:
        path: Path to the opening book file (.epd or .pgn).

    Returns:
        A list of FEN strings, never empty.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file extension is not supported, or the book has
            no positions, as when it's empty or none of its lines parse. A run
            given such a book would otherwise play every game from the initial
            position (#12).
    """
    suffix = path.suffix.lower()
    if suffix == ".epd":
        fens = load_epd_openings(path)
    elif suffix == ".pgn":
        fens = load_pgn_openings(path)
    else:
        raise ValueError(f"Unsupported opening book format: {suffix!r} (expected .epd or .pgn)")
    if not fens:
        # The file's name, not its path: the backend relays this to the browser.
        raise ValueError(f"no positions in {path.name}")
    return fens


def shuffle_openings(fens: list[str], seed: int) -> list[str]:
    """Return the positions in an order fixed by ``seed``.

    In file order, every run would start on the same openings. The same seed
    and positions give the same order, on the same Python version: the order
    comes from :mod:`random`, which doesn't promise its shuffle across
    versions.

    Args:
        fens: The book's positions, which are left as they are.
        seed: The seed for the order.

    Returns:
        A new list with every position in ``fens``, shuffled.
    """
    shuffled = list(fens)
    random.Random(seed).shuffle(shuffled)
    return shuffled


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
