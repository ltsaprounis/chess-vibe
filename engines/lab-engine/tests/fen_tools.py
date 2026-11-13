"""FEN helpers for the tests."""

from __future__ import annotations


def mirror_fen(fen: str) -> str:
    """Return the colour-flipped position: ranks reversed, colours swapped.

    The mirrored position is the same game with White and Black exchanged,
    so the side to move should have the same evaluation and the same best
    move, mirrored.
    """
    placement, side, castling, en_passant, *counters = fen.split()
    rows = placement.split("/")[::-1]
    flipped = "/".join(row.swapcase() for row in rows)
    other = "b" if side == "w" else "w"
    rights = "-" if castling == "-" else "".join(sorted(castling.swapcase(), key="KQkq".index))
    target = "-" if en_passant == "-" else en_passant[0] + str(9 - int(en_passant[1]))
    return " ".join([flipped, other, rights, target, *counters])
