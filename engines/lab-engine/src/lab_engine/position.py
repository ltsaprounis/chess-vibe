"""A position as Python code sees it: FEN in and out, legal moves, UCI moves.

``Position`` holds the two arrays the compiled code works on, ``board`` and
``state`` (``board.py`` describes both), and wraps the compiled move
generator for the UCI layer and the tests.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from lab_engine.board import (
    BLACK,
    BLACK_KING,
    BLACK_KINGSIDE,
    BLACK_QUEENSIDE,
    BOARD_SIZE,
    CASTLING,
    EMPTY,
    EN_PASSANT,
    FULLMOVE_NUMBER,
    HALFMOVE_CLOCK,
    KING,
    NO_SQUARE,
    OFFBOARD,
    SIDE,
    STATE_SIZE,
    WHITE,
    WHITE_KING,
    WHITE_KINGSIDE,
    WHITE_QUEENSIDE,
    parse_square,
    rank_of,
    square,
    square_name,
)
from lab_engine.movegen import (
    MAX_MOVES,
    UNDO_SIZE,
    generate_legal_moves,
    in_check,
    make_move,
    move_from,
    move_promotion,
    move_to,
    perft,
)

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

PIECE_LETTERS = "PNBRQK"
"""White's piece letters, in piece-type order: a type is its index plus one."""

CASTLING_LETTERS = {
    "K": WHITE_KINGSIDE,
    "Q": WHITE_QUEENSIDE,
    "k": BLACK_KINGSIDE,
    "q": BLACK_QUEENSIDE,
}


class Position:
    """A board and its state, as the compiled code reads and changes them.

    Attributes:
        board: The 120-square mailbox, ``BOARD_SIZE`` int64 values.
        state: Side to move, castling rights, en passant square, clocks and
            king squares, ``STATE_SIZE`` int64 values.
    """

    def __init__(self, board: NDArray[np.int64], state: NDArray[np.int64]) -> None:
        """Wrap a board and a state array; ``from_fen`` builds them from FEN."""
        self.board = board
        self.state = state

    @classmethod
    def start(cls) -> Position:
        """Return the initial position."""
        return cls.from_fen(START_FEN)

    @classmethod
    def from_fen(cls, fen: str) -> Position:
        """Read a position from FEN.

        The move counters may be left off, as in some published test
        positions; they then start at 0 and 1.

        Raises:
            ValueError: If ``fen`` isn't a valid FEN with one king a side.
        """
        fields = fen.split()
        if len(fields) == 4:
            fields += ["0", "1"]
        if len(fields) != 6:
            raise ValueError(f"invalid FEN, expected 4 or 6 fields: {fen!r}")
        placement, side, castling, en_passant, halfmove, fullmove = fields

        board = _parse_placement(placement, fen)
        state = np.zeros(STATE_SIZE, dtype=np.int64)
        if side not in ("w", "b"):
            raise ValueError(f"invalid FEN, side to move must be w or b: {fen!r}")
        state[SIDE] = WHITE if side == "w" else BLACK
        state[CASTLING] = _parse_castling(castling, fen)
        state[EN_PASSANT] = _parse_en_passant(en_passant, int(state[SIDE]), fen)
        state[HALFMOVE_CLOCK] = _parse_counter(halfmove, fen)
        state[FULLMOVE_NUMBER] = _parse_counter(fullmove, fen)
        state[WHITE_KING] = _find_king(board, WHITE, fen)
        state[BLACK_KING] = _find_king(board, BLACK, fen)
        return cls(board, state)

    def fen(self) -> str:
        """Return the position as FEN."""
        rows: list[str] = []
        for rank in range(7, -1, -1):
            row = ""
            empty = 0
            for file in range(8):
                piece = int(self.board[square(file, rank)])
                if piece == EMPTY:
                    empty += 1
                    continue
                if empty:
                    row += str(empty)
                    empty = 0
                letter = PIECE_LETTERS[abs(piece) - 1]
                row += letter if piece > 0 else letter.lower()
            if empty:
                row += str(empty)
            rows.append(row)
        side = "w" if self.state[SIDE] == WHITE else "b"
        rights = int(self.state[CASTLING])
        castling = "".join(letter for letter, bit in CASTLING_LETTERS.items() if rights & bit)
        en_passant = int(self.state[EN_PASSANT])
        target = "-" if en_passant == NO_SQUARE else square_name(en_passant)
        return (
            f"{'/'.join(rows)} {side} {castling or '-'} {target} "
            f"{self.state[HALFMOVE_CLOCK]} {self.state[FULLMOVE_NUMBER]}"
        )

    def copy(self) -> Position:
        """Return an independent copy."""
        return Position(self.board.copy(), self.state.copy())

    def legal_moves(self) -> list[int]:
        """Return every legal move of the side to move."""
        moves = np.zeros(MAX_MOVES, dtype=np.int64)
        undo = np.zeros(UNDO_SIZE, dtype=np.int64)
        count = generate_legal_moves(self.board, self.state, moves, undo)
        return [int(move) for move in moves[:count]]

    def in_check(self) -> bool:
        """Return whether the side to move is in check."""
        return in_check(self.board, self.state)

    def push(self, move: int) -> None:
        """Play a legal move."""
        make_move(self.board, self.state, move, np.zeros(UNDO_SIZE, dtype=np.int64))

    def push_uci(self, text: str) -> None:
        """Play a legal move given in UCI notation, such as ``e2e4`` or ``e7e8q``.

        Raises:
            ValueError: If ``text`` isn't a legal move here; the position is
                then unchanged.
        """
        for move in self.legal_moves():
            if move_to_uci(move) == text:
                self.push(move)
                return
        raise ValueError(f"not a legal move here: {text!r} in {self.fen()}")

    def perft(self, depth: int) -> int:
        """Count the leaf positions ``depth`` plies below this one."""
        moves = np.zeros((depth + 1, MAX_MOVES), dtype=np.int64)
        undo = np.zeros((depth + 1, UNDO_SIZE), dtype=np.int64)
        return perft(self.board, self.state, depth, 0, moves, undo)


def move_to_uci(move: int) -> str:
    """Return a move in UCI notation, such as ``e2e4`` or ``e7e8q``."""
    text = square_name(move_from(move)) + square_name(move_to(move))
    promotion = move_promotion(move)
    if promotion:
        text += PIECE_LETTERS[promotion - 1].lower()
    return text


def _parse_placement(placement: str, fen: str) -> NDArray[np.int64]:
    """Read FEN's first field into a mailbox board."""
    rows = placement.split("/")
    if len(rows) != 8:
        raise ValueError(f"invalid FEN, expected 8 ranks: {fen!r}")
    board = np.full(BOARD_SIZE, OFFBOARD, dtype=np.int64)
    for index, row in enumerate(rows):
        rank = 7 - index
        file = 0
        for char in row:
            if char in "12345678":
                squares = int(char)
                if file + squares > 8:
                    file = -1
                    break
                for _ in range(squares):
                    board[square(file, rank)] = EMPTY
                    file += 1
            elif char.upper() in PIECE_LETTERS and file < 8:
                piece = PIECE_LETTERS.index(char.upper()) + 1
                board[square(file, rank)] = piece if char.isupper() else -piece
                file += 1
            else:
                file = -1
                break
        if file != 8:
            raise ValueError(f"invalid FEN, rank {rank + 1} isn't 8 squares: {fen!r}")
    return board


def _parse_castling(text: str, fen: str) -> int:
    """Read FEN's castling field into rights bits."""
    if text == "-":
        return 0
    rights = 0
    for char in text:
        bit = CASTLING_LETTERS.get(char)
        if bit is None or rights & bit:
            raise ValueError(f"invalid FEN, bad castling rights {text!r}: {fen!r}")
        rights |= bit
    return rights


def _parse_en_passant(text: str, side: int, fen: str) -> int:
    """Read FEN's en passant field: the square a pawn just passed, or NO_SQUARE."""
    if text == "-":
        return NO_SQUARE
    try:
        target = parse_square(text)
    except ValueError:
        raise ValueError(f"invalid FEN, bad en passant square {text!r}: {fen!r}") from None
    # The square behind a pawn that just moved two: on the sixth rank if
    # Black moved it and White is to move, on the third if the reverse.
    if rank_of(target) != (5 if side == WHITE else 2):
        raise ValueError(f"invalid FEN, impossible en passant square {text!r}: {fen!r}")
    return target


def _parse_counter(text: str, fen: str) -> int:
    """Read a FEN move counter, which can't be negative."""
    if not text.isdigit():
        raise ValueError(f"invalid FEN, bad move counter {text!r}: {fen!r}")
    return int(text)


def _find_king(board: NDArray[np.int64], side: int, fen: str) -> int:
    """Return the square of ``side``'s only king."""
    squares = np.flatnonzero(board == KING * side)
    if len(squares) != 1:
        colour = "white" if side == WHITE else "black"
        raise ValueError(f"invalid FEN, expected one {colour} king: {fen!r}")
    return int(squares[0])
