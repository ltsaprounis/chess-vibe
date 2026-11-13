"""The board: squares, pieces, and the state array's layout.

The board is a 10x12 mailbox: an array of 120 squares, of which the 64 in
the middle are the chessboard and the rest are a border marked ``OFFBOARD``.
Moving up a rank adds 10 and moving right a file adds 1, so ``a1`` is 21 and
``h8`` is 98. A piece that walks off the chessboard lands on the border, and
the border is two squares deep above and below, so even a knight's jump
lands inside the array. Move generation needs no bounds arithmetic::

     0   1   2   3   4   5   6   7   8   9
    10  11  12  13  14  15  16  17  18  19
    20  a1  b1  c1  d1  e1  f1  g1  h1  29
    30  a2  ...                     h2  39
    ...
    90  a8  ...                     h8  99
   100 101 102 103 104 105 106 107 108 109
   110 111 112 113 114 115 116 117 118 119

Pieces are signed integers: positive for White and negative for Black. The
side to move is ``WHITE`` (1) or ``BLACK`` (-1), so ``piece * side > 0``
means the piece belongs to the side to move, and ``piece * side`` is then
its type.

The rest of a position lives in a small ``state`` array, indexed by the
constants below. Every array holds int64, which keeps numba's typing simple.

Everything here is plain Python. Compiled code reads these constants as
compile-time values.
"""

from __future__ import annotations

# Pieces. White's are positive, Black's negative; OFFBOARD marks the border.
EMPTY = 0
PAWN = 1
KNIGHT = 2
BISHOP = 3
ROOK = 4
QUEEN = 5
KING = 6
OFFBOARD = 7

# Sides, as signs.
WHITE = 1
BLACK = -1

BOARD_SIZE = 120

NO_SQUARE = 0
"""The en passant square when there is none. Square 0 is on the border."""

# Indices into a position's state array.
SIDE = 0  # WHITE or BLACK: the side to move
CASTLING = 1  # castling rights, as bits below
EN_PASSANT = 2  # the square a pawn can capture onto en passant, or NO_SQUARE
HALFMOVE_CLOCK = 3  # plies since the last capture or pawn move
FULLMOVE_NUMBER = 4  # starts at 1, and goes up after each Black move
WHITE_KING = 5  # the white king's square
BLACK_KING = 6  # the black king's square
STATE_SIZE = 7

# Castling rights.
WHITE_KINGSIDE = 1
WHITE_QUEENSIDE = 2
BLACK_KINGSIDE = 4
BLACK_QUEENSIDE = 8

FILES = "abcdefgh"
RANKS = "12345678"


def square(file: int, rank: int) -> int:
    """Return the mailbox square of a file and a rank, each 0 to 7.

    Args:
        file: 0 for the a-file up to 7 for the h-file.
        rank: 0 for the first rank up to 7 for the eighth.

    Returns:
        The square's index in the 120-square board.
    """
    return 21 + file + 10 * rank


def file_of(sq: int) -> int:
    """Return a square's file, 0 (a) to 7 (h)."""
    return sq % 10 - 1


def rank_of(sq: int) -> int:
    """Return a square's rank, 0 (first) to 7 (eighth)."""
    return sq // 10 - 2


def square_name(sq: int) -> str:
    """Return a square's algebraic name, such as ``"e4"``."""
    return FILES[file_of(sq)] + RANKS[rank_of(sq)]


def parse_square(name: str) -> int:
    """Return the square an algebraic name such as ``"e4"`` names.

    Raises:
        ValueError: If ``name`` isn't a file letter and a rank digit.
    """
    if len(name) != 2 or name[0] not in FILES or name[1] not in RANKS:
        raise ValueError(f"not a square: {name!r}")
    return square(FILES.index(name[0]), RANKS.index(name[1]))


A1, B1, C1, D1, E1, F1, G1, H1 = (square(file, 0) for file in range(8))
A8, B8, C8, D8, E8, F8, G8, H8 = (square(file, 7) for file in range(8))
