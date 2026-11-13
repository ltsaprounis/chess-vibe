"""Published perft counts for the standard test positions.

From the Chess Programming Wiki's "Perft Results" page
(https://www.chessprogramming.org/Perft_Results), read on 2026-11-12. The
FENs are as published there; Kiwipete's has no move counters.

Perft(n) is the number of leaf positions n plies deep, counting every legal
move sequence. A move generator that disagrees with these counts has a bug.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PerftPosition:
    """A test position and its published node counts.

    Attributes:
        name: A short name for test ids.
        fen: The position, as published.
        counts: ``counts[d - 1]`` is perft(d).
    """

    name: str
    fen: str
    counts: tuple[int, ...]


PERFT_POSITIONS: tuple[PerftPosition, ...] = (
    PerftPosition(
        "initial",
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        (20, 400, 8_902, 197_281, 4_865_609, 119_060_324),
    ),
    PerftPosition(
        "kiwipete",
        "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq -",
        (48, 2_039, 97_862, 4_085_603, 193_690_690),
    ),
    PerftPosition(
        "position3",
        "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
        (14, 191, 2_812, 43_238, 674_624, 11_030_083, 178_633_661),
    ),
    PerftPosition(
        "position4",
        "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1",
        (6, 264, 9_467, 422_333, 15_833_292, 706_045_033),
    ),
    PerftPosition(
        "position4_mirrored",
        "r2q1rk1/pP1p2pp/Q4n2/bbp1p3/Np6/1B3NBn/pPPP1PPP/R3K2R b KQ - 0 1",
        (6, 264, 9_467, 422_333, 15_833_292, 706_045_033),
    ),
    PerftPosition(
        "position5",
        "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8",
        (44, 1_486, 62_379, 2_103_487, 89_941_194),
    ),
    PerftPosition(
        "position6",
        "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10",
        (46, 2_079, 89_890, 3_894_594, 164_075_551, 6_923_051_137),
    ),
)
