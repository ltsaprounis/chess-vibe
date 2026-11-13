"""Evaluation: material plus piece-square bonuses. Compiled.

A position's score is in centipawns, from the point of view of the side it's
asked for: each piece's value, plus a bonus for the square it stands on, for
that side's pieces, minus the same for the opponent's.

The piece-square tables are built from simple rules, so their intent is
plain:

- pawns are worth more the further they advance, and on d4, e4, d5 and e5;
- knights are worth more near the centre and much less on the rim;
- bishops and queens get a smaller centre bonus;
- rooks get a bonus on the seventh rank;
- the king is safer on its back rank, towards a corner.

The tables are written from White's side. A black piece reads the square
mirrored top to bottom, so the evaluation is symmetric.

The baseline is plain on purpose. Its king table suits the middlegame only,
and there's no pawn structure, mobility or king safety beyond it.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from lab_engine.board import (
    A1,
    BISHOP,
    BOARD_SIZE,
    EMPTY,
    H8,
    KING,
    KNIGHT,
    OFFBOARD,
    PAWN,
    QUEEN,
    ROOK,
    square,
)
from lab_engine.jit import VECTOR, compiled

Array = NDArray[np.int64]

PIECE_VALUES = np.array([0, 100, 300, 320, 500, 900, 0, 0], dtype=np.int64)
"""Centipawns per piece type: none, pawn, knight, bishop, rook, queen, king.

Each side always has one king, so the king's value is 0.
"""


def _centre_distance(file: int, rank: int) -> int:
    """How many files plus ranks a square is from the four centre squares: 0 to 6."""
    return max(3 - file, file - 4) + max(3 - rank, rank - 4)


def _piece_square_bonus(piece_type: int, file: int, rank: int) -> int:
    """The bonus for a white piece of ``piece_type`` on a file and rank (0 to 7)."""
    distance = _centre_distance(file, rank)
    if piece_type == PAWN:
        advance = (0, 0, 5, 10, 20, 30, 50, 0)[rank]
        centre = 10 if file in (3, 4) and rank in (3, 4) else 0
        return advance + centre
    if piece_type == KNIGHT:
        return 20 - 10 * distance
    if piece_type == BISHOP:
        return 10 - 5 * distance
    if piece_type == ROOK:
        return 20 if rank == 6 else 0
    if piece_type == QUEEN:
        return 5 - 2 * distance
    if piece_type == KING:
        return (20, 20, 10, 0, 0, 10, 20, 20)[file] - 20 * rank
    return 0


def _piece_square_tables() -> Array:
    """Return the bonuses as an array indexed by piece type, then by square.

    Squares are numbered 0 (a1) to 63 (h8), from White's side.
    """
    tables = np.zeros((KING + 1, 64), dtype=np.int64)
    for piece_type in range(PAWN, KING + 1):
        for rank in range(8):
            for file in range(8):
                tables[piece_type, rank * 8 + file] = _piece_square_bonus(piece_type, file, rank)
    return tables


def _table_index(mirrored: bool) -> Array:
    """Map each mailbox square to its piece-square table index.

    White pieces read their square as it is; Black's read it mirrored top to
    bottom, so a black pawn on e5 counts as a white pawn on e4. Border
    squares map to 0 and are never read.
    """
    index = np.zeros(BOARD_SIZE, dtype=np.int64)
    for rank in range(8):
        for file in range(8):
            seen_rank = 7 - rank if mirrored else rank
            index[square(file, rank)] = seen_rank * 8 + file
    return index


PIECE_SQUARE = _piece_square_tables()
WHITE_INDEX = _table_index(mirrored=False)
BLACK_INDEX = _table_index(mirrored=True)


@compiled(f"int64({VECTOR}, int64)")
def evaluate(board: Array, side: int) -> int:
    """Return the position's score, in centipawns, for ``side``.

    Args:
        board: The position's board.
        side: WHITE or BLACK: whose point of view the score is from.

    Returns:
        Positive when ``side`` is ahead.
    """
    score = 0
    for sq in range(A1, H8 + 1):
        piece: int = board[sq]
        if piece in (EMPTY, OFFBOARD):
            continue
        if piece > 0:
            score += PIECE_VALUES[piece] + PIECE_SQUARE[piece, WHITE_INDEX[sq]]
        else:
            score -= PIECE_VALUES[-piece] + PIECE_SQUARE[-piece, BLACK_INDEX[sq]]
    return score * side
