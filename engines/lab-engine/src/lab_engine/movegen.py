"""Move generation, make and unmake, and perft. All compiled.

Moves are generated pseudo-legally: a move may leave the mover's own king in
check. Callers make each move, skip it if ``mover_in_check`` says the king
was left attacked, and unmake it. Castling is the exception, because its
legality depends on squares the king only passes through: it's generated
only when the king isn't in check and doesn't cross an attacked square.

A move packs four fields into one integer::

    bits 0-6    from square
    bits 7-13   to square
    bits 14-16  promotion piece type (KNIGHT to QUEEN), or 0
    bits 17-18  kind: NORMAL, DOUBLE_PUSH, EN_PASSANT_CAPTURE or CASTLE

A capture is a NORMAL move onto an enemy piece; ``make_move`` records the
captured piece in an undo record, which ``unmake_move`` reads back.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from lab_engine.board import (
    A1,
    A8,
    BISHOP,
    BLACK_KING,
    BLACK_KINGSIDE,
    BLACK_QUEENSIDE,
    BOARD_SIZE,
    CASTLING,
    E1,
    E8,
    EMPTY,
    EN_PASSANT,
    FULLMOVE_NUMBER,
    H1,
    H8,
    HALFMOVE_CLOCK,
    KING,
    KNIGHT,
    NO_SQUARE,
    OFFBOARD,
    PAWN,
    QUEEN,
    ROOK,
    SIDE,
    WHITE,
    WHITE_KING,
    WHITE_KINGSIDE,
    WHITE_QUEENSIDE,
)
from lab_engine.jit import TABLE, VECTOR, compiled

Array = NDArray[np.int64]

MAX_MOVES = 256
"""Room for every move in a position. The most any legal position has is 218."""

# Move kinds.
NORMAL = 0
DOUBLE_PUSH = 1
EN_PASSANT_CAPTURE = 2
CASTLE = 3

NO_MOVE = 0
"""Not a move: no move starts on square 0, which is on the border."""

# Fields of an undo record: what make_move changes that unmake_move can't
# work out from the move itself.
CAPTURED = 0
PREVIOUS_CASTLING = 1
PREVIOUS_EN_PASSANT = 2
PREVIOUS_HALFMOVE_CLOCK = 3
UNDO_SIZE = 4

# How each piece moves on the mailbox.
KNIGHT_STEPS = np.array([-21, -19, -12, -8, 8, 12, 19, 21], dtype=np.int64)
KING_STEPS = np.array([-11, -10, -9, -1, 1, 9, 10, 11], dtype=np.int64)
ROOK_DIRECTIONS = np.array([-10, -1, 1, 10], dtype=np.int64)
BISHOP_DIRECTIONS = np.array([-11, -9, 9, 11], dtype=np.int64)


def _castling_rights_kept() -> Array:
    """For each square, the castling rights that survive a move from or to it.

    A king leaving its square loses both of its side's rights, and a rook
    leaving its corner, or captured there, loses that corner's right.
    """
    every_right = WHITE_KINGSIDE | WHITE_QUEENSIDE | BLACK_KINGSIDE | BLACK_QUEENSIDE
    kept = np.full(BOARD_SIZE, every_right, dtype=np.int64)
    kept[E1] = every_right & ~(WHITE_KINGSIDE | WHITE_QUEENSIDE)
    kept[H1] = every_right & ~WHITE_KINGSIDE
    kept[A1] = every_right & ~WHITE_QUEENSIDE
    kept[E8] = every_right & ~(BLACK_KINGSIDE | BLACK_QUEENSIDE)
    kept[H8] = every_right & ~BLACK_KINGSIDE
    kept[A8] = every_right & ~BLACK_QUEENSIDE
    return kept


CASTLING_RIGHTS_KEPT = _castling_rights_kept()


# ---------------------------------------------------------------------------
# Moves as integers
# ---------------------------------------------------------------------------


@compiled("int64(int64, int64, int64, int64)")
def encode_move(from_square: int, to_square: int, promotion: int, kind: int) -> int:
    """Pack a move's fields into one integer."""
    return from_square | (to_square << 7) | (promotion << 14) | (kind << 17)


@compiled("int64(int64)")
def move_from(move: int) -> int:
    """Return the square a move starts on."""
    return move & 127


@compiled("int64(int64)")
def move_to(move: int) -> int:
    """Return the square a move ends on."""
    return (move >> 7) & 127


@compiled("int64(int64)")
def move_promotion(move: int) -> int:
    """Return the piece type a pawn promotes to, or 0."""
    return (move >> 14) & 7


@compiled("int64(int64)")
def move_kind(move: int) -> int:
    """Return NORMAL, DOUBLE_PUSH, EN_PASSANT_CAPTURE or CASTLE."""
    return (move >> 17) & 3


@compiled("int64(int64)")
def _rank(square: int) -> int:
    """Return a square's rank, 0 (first) to 7 (eighth)."""
    return square // 10 - 2


# ---------------------------------------------------------------------------
# Attacks
# ---------------------------------------------------------------------------


@compiled(f"boolean({VECTOR}, int64, int64)")
def is_attacked(board: Array, square: int, by_side: int) -> bool:
    """Return whether any piece of ``by_side`` attacks ``square``."""
    # A pawn attacks diagonally forward, so a pawn attacking this square
    # stands diagonally behind it, as the attacker sees the board.
    behind = square - 10 * by_side
    if board[behind - 1] == PAWN * by_side or board[behind + 1] == PAWN * by_side:
        return True
    for step in KNIGHT_STEPS:
        if board[square + step] == KNIGHT * by_side:
            return True
    for step in KING_STEPS:
        if board[square + step] == KING * by_side:
            return True
    for direction in ROOK_DIRECTIONS:
        target = square + direction
        while board[target] == EMPTY:
            target += direction
        if board[target] == ROOK * by_side or board[target] == QUEEN * by_side:
            return True
    for direction in BISHOP_DIRECTIONS:
        target = square + direction
        while board[target] == EMPTY:
            target += direction
        if board[target] == BISHOP * by_side or board[target] == QUEEN * by_side:
            return True
    return False


@compiled(f"int64({VECTOR}, int64)")
def king_square(state: Array, side: int) -> int:
    """Return the square of ``side``'s king."""
    return state[WHITE_KING] if side == WHITE else state[BLACK_KING]


@compiled(f"boolean({VECTOR}, {VECTOR})")
def in_check(board: Array, state: Array) -> bool:
    """Return whether the side to move is in check."""
    side = state[SIDE]
    return is_attacked(board, king_square(state, side), -side)


@compiled(f"boolean({VECTOR}, {VECTOR})")
def mover_in_check(board: Array, state: Array) -> bool:
    """After ``make_move``: whether the side that moved left its king attacked.

    Such a move is illegal, and the caller must unmake it.
    """
    side = state[SIDE]
    return is_attacked(board, king_square(state, -side), side)


# ---------------------------------------------------------------------------
# Move generation
# ---------------------------------------------------------------------------


@compiled(f"int64({VECTOR}, int64, int64, int64, {VECTOR}, int64)")
def _add_step(
    board: Array, side: int, from_square: int, to_square: int, moves: Array, count: int
) -> int:
    """Add a knight's or king's step, unless it lands on its own piece or the border."""
    target = board[to_square]
    if target == EMPTY or (target != OFFBOARD and target * side < 0):
        moves[count] = encode_move(from_square, to_square, 0, NORMAL)
        count += 1
    return count


@compiled(f"int64({VECTOR}, int64, int64, int64, {VECTOR}, int64)")
def _add_slides(
    board: Array, side: int, from_square: int, direction: int, moves: Array, count: int
) -> int:
    """Add a slider's moves in one direction: every empty square, then a capture."""
    to_square = from_square + direction
    while board[to_square] == EMPTY:
        moves[count] = encode_move(from_square, to_square, 0, NORMAL)
        count += 1
        to_square += direction
    target = board[to_square]
    if target != OFFBOARD and target * side < 0:
        moves[count] = encode_move(from_square, to_square, 0, NORMAL)
        count += 1
    return count


@compiled(f"int64(int64, int64, int64, {VECTOR}, int64)")
def _add_pawn_move(from_square: int, to_square: int, side: int, moves: Array, count: int) -> int:
    """Add a pawn's move, or its four promotions if it reaches the last rank."""
    last_rank = 7 if side == WHITE else 0
    if _rank(to_square) == last_rank:
        for piece in (QUEEN, ROOK, BISHOP, KNIGHT):
            moves[count] = encode_move(from_square, to_square, piece, NORMAL)
            count += 1
    else:
        moves[count] = encode_move(from_square, to_square, 0, NORMAL)
        count += 1
    return count


@compiled(f"int64({VECTOR}, {VECTOR}, int64, {VECTOR}, int64)")
def _add_pawn_moves(board: Array, state: Array, from_square: int, moves: Array, count: int) -> int:
    """Add a pawn's pushes, captures and en passant captures."""
    side = state[SIDE]
    forward = 10 * side
    one_step = from_square + forward
    if board[one_step] == EMPTY:
        count = _add_pawn_move(from_square, one_step, side, moves, count)
        start_rank = 1 if side == WHITE else 6
        two_steps = one_step + forward
        if _rank(from_square) == start_rank and board[two_steps] == EMPTY:
            moves[count] = encode_move(from_square, two_steps, 0, DOUBLE_PUSH)
            count += 1
    for to_square in (one_step - 1, one_step + 1):
        target = board[to_square]
        if target != OFFBOARD and target * side < 0:
            count = _add_pawn_move(from_square, to_square, side, moves, count)
        elif to_square == state[EN_PASSANT] and board[to_square - forward] == -PAWN * side:
            moves[count] = encode_move(from_square, to_square, 0, EN_PASSANT_CAPTURE)
            count += 1
    return count


@compiled(f"int64({VECTOR}, {VECTOR}, int64, {VECTOR}, int64)")
def _add_castling(board: Array, state: Array, king_from: int, moves: Array, count: int) -> int:
    """Add the castling moves the side to move has the right and the room for."""
    side = state[SIDE]
    if side == WHITE:
        home = E1
        kingside = WHITE_KINGSIDE
        queenside = WHITE_QUEENSIDE
    else:
        home = E8
        kingside = BLACK_KINGSIDE
        queenside = BLACK_QUEENSIDE
    rights = state[CASTLING]
    if king_from != home or (rights & (kingside | queenside)) == 0:
        return count
    if is_attacked(board, home, -side):
        return count  # no castling out of check
    rook = ROOK * side
    if (
        (rights & kingside) != 0
        and board[home + 3] == rook
        and board[home + 1] == EMPTY
        and board[home + 2] == EMPTY
        and not is_attacked(board, home + 1, -side)
    ):
        moves[count] = encode_move(home, home + 2, 0, CASTLE)
        count += 1
    if (
        (rights & queenside) != 0
        and board[home - 4] == rook
        and board[home - 1] == EMPTY
        and board[home - 2] == EMPTY
        and board[home - 3] == EMPTY
        and not is_attacked(board, home - 1, -side)
    ):
        moves[count] = encode_move(home, home - 2, 0, CASTLE)
        count += 1
    return count


@compiled(f"int64({VECTOR}, {VECTOR}, {VECTOR})")
def generate_moves(board: Array, state: Array, moves: Array) -> int:
    """Write every pseudo-legal move of the side to move into ``moves``.

    Args:
        board: The position's board.
        state: The position's state.
        moves: At least ``MAX_MOVES`` long.

    Returns:
        How many moves were written, from the start of ``moves``.
    """
    side = state[SIDE]
    count = 0
    for from_square in range(A1, H8 + 1):
        piece = board[from_square]
        if piece == OFFBOARD or piece * side <= 0:
            continue  # the border, an empty square, or the opponent's piece
        piece_type = piece * side
        if piece_type == PAWN:
            count = _add_pawn_moves(board, state, from_square, moves, count)
        elif piece_type == KNIGHT:
            for step in KNIGHT_STEPS:
                count = _add_step(board, side, from_square, from_square + step, moves, count)
        elif piece_type == KING:
            for step in KING_STEPS:
                count = _add_step(board, side, from_square, from_square + step, moves, count)
            count = _add_castling(board, state, from_square, moves, count)
        else:
            if piece_type != ROOK:  # bishops and queens
                for direction in BISHOP_DIRECTIONS:
                    count = _add_slides(board, side, from_square, direction, moves, count)
            if piece_type != BISHOP:  # rooks and queens
                for direction in ROOK_DIRECTIONS:
                    count = _add_slides(board, side, from_square, direction, moves, count)
    return count


# ---------------------------------------------------------------------------
# Making and unmaking moves
# ---------------------------------------------------------------------------


@compiled(f"void({VECTOR}, {VECTOR}, int64, {VECTOR})")
def make_move(board: Array, state: Array, move: int, undo: Array) -> None:
    """Play ``move``, recording in ``undo`` what ``unmake_move`` needs.

    The move must be pseudo-legal in the position.
    """
    side = state[SIDE]
    from_square = move_from(move)
    to_square = move_to(move)
    kind = move_kind(move)
    promotion = move_promotion(move)
    piece = board[from_square]

    undo[CAPTURED] = board[to_square]
    undo[PREVIOUS_CASTLING] = state[CASTLING]
    undo[PREVIOUS_EN_PASSANT] = state[EN_PASSANT]
    undo[PREVIOUS_HALFMOVE_CLOCK] = state[HALFMOVE_CLOCK]

    board[to_square] = piece if promotion == 0 else promotion * side
    board[from_square] = EMPTY
    if kind == EN_PASSANT_CAPTURE:
        board[to_square - 10 * side] = EMPTY  # the captured pawn, behind the target
    elif kind == CASTLE:
        if to_square > from_square:  # kingside: the rook goes from h to f
            board[to_square - 1] = board[to_square + 1]
            board[to_square + 1] = EMPTY
        else:  # queenside: the rook goes from a to d
            board[to_square + 1] = board[to_square - 2]
            board[to_square - 2] = EMPTY

    if piece * side == PAWN or undo[CAPTURED] != EMPTY or kind == EN_PASSANT_CAPTURE:
        state[HALFMOVE_CLOCK] = 0
    else:
        state[HALFMOVE_CLOCK] += 1
    state[EN_PASSANT] = from_square + 10 * side if kind == DOUBLE_PUSH else NO_SQUARE
    state[CASTLING] &= CASTLING_RIGHTS_KEPT[from_square] & CASTLING_RIGHTS_KEPT[to_square]
    if piece * side == KING:
        state[WHITE_KING if side == WHITE else BLACK_KING] = to_square
    if side != WHITE:
        state[FULLMOVE_NUMBER] += 1
    state[SIDE] = -side


@compiled(f"void({VECTOR}, {VECTOR}, int64, {VECTOR})")
def unmake_move(board: Array, state: Array, move: int, undo: Array) -> None:
    """Take back ``move``, the last move made, using its undo record."""
    side = -state[SIDE]  # the side that made the move
    from_square = move_from(move)
    to_square = move_to(move)
    kind = move_kind(move)

    piece = PAWN * side if move_promotion(move) != 0 else board[to_square]
    board[from_square] = piece
    board[to_square] = undo[CAPTURED]
    if kind == EN_PASSANT_CAPTURE:
        board[to_square - 10 * side] = -PAWN * side
    elif kind == CASTLE:
        if to_square > from_square:
            board[to_square + 1] = board[to_square - 1]
            board[to_square - 1] = EMPTY
        else:
            board[to_square - 2] = board[to_square + 1]
            board[to_square + 1] = EMPTY

    if piece * side == KING:
        state[WHITE_KING if side == WHITE else BLACK_KING] = from_square
    if side != WHITE:
        state[FULLMOVE_NUMBER] -= 1
    state[CASTLING] = undo[PREVIOUS_CASTLING]
    state[EN_PASSANT] = undo[PREVIOUS_EN_PASSANT]
    state[HALFMOVE_CLOCK] = undo[PREVIOUS_HALFMOVE_CLOCK]
    state[SIDE] = side


@compiled(f"int64({VECTOR}, {VECTOR}, {VECTOR}, {VECTOR})")
def generate_legal_moves(board: Array, state: Array, moves: Array, undo: Array) -> int:
    """Write every legal move of the side to move into ``moves``.

    Args:
        board: The position's board; left as it was.
        state: The position's state; left as it was.
        moves: At least ``MAX_MOVES`` long.
        undo: A scratch undo record, ``UNDO_SIZE`` long.

    Returns:
        How many moves were written, from the start of ``moves``.
    """
    count = generate_moves(board, state, moves)
    legal = 0
    for i in range(count):
        move = moves[i]
        make_move(board, state, move, undo)
        if not mover_in_check(board, state):
            moves[legal] = move
            legal += 1
        unmake_move(board, state, move, undo)
    return legal


@compiled(f"int64({VECTOR}, {VECTOR}, int64, int64, {TABLE}, {TABLE})")
def perft(board: Array, state: Array, depth: int, ply: int, moves: Array, undo: Array) -> int:
    """Count the leaf positions ``depth`` plies below this one.

    Every legal sequence of moves counts once. The position is left as it was.

    Args:
        board: The position's board.
        state: The position's state.
        depth: Plies to go.
        ply: Plies from the root, which selects the rows of ``moves`` and
            ``undo`` this call uses.
        moves: One row of ``MAX_MOVES`` per ply.
        undo: One undo record per ply.

    Returns:
        The number of leaves.
    """
    if depth == 0:
        return 1
    count = generate_moves(board, state, moves[ply])
    leaves = 0
    for i in range(count):
        move = moves[ply, i]
        make_move(board, state, move, undo[ply])
        if not mover_in_check(board, state):
            leaves += perft(board, state, depth - 1, ply + 1, moves, undo)
        unmake_move(board, state, move, undo[ply])
    return leaves
