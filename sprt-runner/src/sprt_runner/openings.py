"""Opening book loader and pair generator for SPRT testing.

Loads opening positions from EPD files and generates opening pairs
where each opening is played twice with colours swapped to eliminate
first-move advantage bias.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OpeningPair:
    """An opening position with colour assignment.

    Attributes:
        fen: FEN string of the starting position.
        swap_colors: If True, the test engine plays black (and base plays white).
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
        if not stripped:
            continue

        # EPD format: FEN fields ; operations
        # Split on semicolon and take the FEN part
        fen_part = stripped.split(";")[0].strip()
        if fen_part:
            fens.append(fen_part)

    return fens


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
