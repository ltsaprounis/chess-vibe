"""Tests for opening book loading and pair generation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sprt_runner.openings import (
    OpeningPair,
    load_epd_openings,
    load_openings,
    load_pgn_openings,
    make_opening_pairs,
    shuffle_openings,
)


@pytest.fixture
def epd_file(tmp_path: Path) -> Path:
    """Create a sample EPD file with a few openings."""
    content = (
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1 ; e4\n"
        "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1 ; d4\n"
        "rnbqkbnr/pppppppp/8/8/2P5/8/PP1PPPPP/RNBQKBNR b KQkq - 0 1 ; c4\n"
    )
    epd_path = tmp_path / "openings.epd"
    epd_path.write_text(content)
    return epd_path


@pytest.fixture
def empty_epd_file(tmp_path: Path) -> Path:
    """Create an empty EPD file."""
    epd_path = tmp_path / "empty.epd"
    epd_path.write_text("")
    return epd_path


class TestLoadEPDOpenings:
    """Tests for EPD opening book loading."""

    def test_load_epd_returns_fens(self, epd_file: Path) -> None:
        fens = load_epd_openings(epd_file)
        assert len(fens) == 3

    def test_load_epd_valid_fens(self, epd_file: Path) -> None:
        fens = load_epd_openings(epd_file)
        assert "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1" in fens

    def test_load_epd_empty_file(self, empty_epd_file: Path) -> None:
        fens = load_epd_openings(empty_epd_file)
        assert fens == []

    def test_load_epd_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_epd_openings(tmp_path / "nonexistent.epd")

    def test_load_epd_skips_blank_lines(self, tmp_path: Path) -> None:
        content = (
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1 ; e4\n"
            "\n"
            "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1 ; d4\n"
            "\n"
        )
        epd_path = tmp_path / "with_blanks.epd"
        epd_path.write_text(content)
        fens = load_epd_openings(epd_path)
        assert len(fens) == 2

    def test_load_epd_strips_bm_operation(self, tmp_path: Path) -> None:
        """EPD line with bm operation before semicolon should only return FEN."""
        content = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - bm d5; id "ECO:B00";\n'
        epd_path = tmp_path / "bm_ops.epd"
        epd_path.write_text(content)
        fens = load_epd_openings(epd_path)
        assert len(fens) == 1
        assert fens[0] == "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"

    def test_load_epd_strips_multiple_operations(self, tmp_path: Path) -> None:
        """EPD line with multiple operations should only return FEN."""
        content = (
            'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - bm d5; id "test"; ce 30;\n'
        )
        epd_path = tmp_path / "multi_ops.epd"
        epd_path.write_text(content)
        fens = load_epd_openings(epd_path)
        assert len(fens) == 1
        assert fens[0] == "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"

    def test_load_epd_pure_fen_six_fields(self, tmp_path: Path) -> None:
        """Pure 6-field FEN line should be returned unchanged."""
        content = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1\n"
        epd_path = tmp_path / "pure_fen.epd"
        epd_path.write_text(content)
        fens = load_epd_openings(epd_path)
        assert len(fens) == 1
        assert fens[0] == "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"

    def test_load_epd_four_field_epd(self, tmp_path: Path) -> None:
        """4-field EPD line should get default halfmove/fullmove counters."""
        content = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq -\n"
        epd_path = tmp_path / "four_field.epd"
        epd_path.write_text(content)
        fens = load_epd_openings(epd_path)
        assert len(fens) == 1
        assert fens[0] == "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"

    def test_load_epd_skips_comment_lines(self, tmp_path: Path) -> None:
        """Comment lines starting with # or % should be skipped."""
        content = (
            "# This is a comment\n"
            "% Another comment\n"
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1\n"
        )
        epd_path = tmp_path / "comments.epd"
        epd_path.write_text(content)
        fens = load_epd_openings(epd_path)
        assert len(fens) == 1
        assert fens[0] == "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"

    def test_load_epd_semicolon_only_operations(self, tmp_path: Path) -> None:
        """EPD line with operations only after semicolons should still work."""
        content = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1; e4\n"
        epd_path = tmp_path / "semi_ops.epd"
        epd_path.write_text(content)
        fens = load_epd_openings(epd_path)
        assert len(fens) == 1
        assert fens[0] == "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


class TestMakeOpeningPairs:
    """Tests for opening pair generation."""

    def test_basic_pairs(self) -> None:
        fens = ["fen1", "fen2"]
        pairs = make_opening_pairs(fens)
        assert len(pairs) == 4  # 2 fens x 2 colour assignments
        # First opening pair
        assert pairs[0].fen == "fen1"
        assert pairs[0].swap_colors is False
        assert pairs[1].fen == "fen1"
        assert pairs[1].swap_colors is True

    def test_all_openings_used_twice(self) -> None:
        fens = ["fen1", "fen2", "fen3"]
        pairs = make_opening_pairs(fens)
        assert len(pairs) == 6
        # Each opening should appear exactly twice (once per color)
        for fen in fens:
            matching = [p for p in pairs if p.fen == fen]
            assert len(matching) == 2
            assert any(not p.swap_colors for p in matching)
            assert any(p.swap_colors for p in matching)

    def test_empty_openings(self) -> None:
        pairs = make_opening_pairs([])
        assert pairs == []

    def test_single_opening(self) -> None:
        pairs = make_opening_pairs(["only_fen"])
        assert len(pairs) == 2
        assert pairs[0].fen == "only_fen"
        assert pairs[0].swap_colors is False
        assert pairs[1].fen == "only_fen"
        assert pairs[1].swap_colors is True


class TestShuffleOpenings:
    """A run's openings come in an order its seed fixes (#12).

    In file order, every run would start on the same openings, so hundreds of
    attempts would all be tested on the same first positions.
    """

    _FENS: tuple[str, ...] = tuple(f"position {i}" for i in range(20))

    def test_the_order_is_not_the_files(self) -> None:
        assert shuffle_openings(list(self._FENS), seed=1) != list(self._FENS)

    def test_each_seed_gives_its_own_order(self) -> None:
        orders = {tuple(shuffle_openings(list(self._FENS), seed=seed)) for seed in range(5)}
        assert len(orders) == 5

    def test_the_same_seed_gives_the_same_order(self) -> None:
        first = shuffle_openings(list(self._FENS), seed=7)
        assert shuffle_openings(list(self._FENS), seed=7) == first

    def test_a_seed_still_gives_the_order_it_gave_when_recorded(self) -> None:
        """A recorded seed replays its run's order, in a new process too.

        This pins the order :mod:`random` gives for seed 12 on Python 3.13.
        :mod:`random` doesn't promise its shuffle across Python versions, so if
        this fails after an upgrade, seeds recorded before it no longer
        reproduce their runs' order. The input isn't sorted, so an order that
        sorted the positions first would fail too.
        """
        assert shuffle_openings(["e", "c", "a", "f", "b", "d"], seed=12) == [
            "d",
            "c",
            "e",
            "b",
            "a",
            "f",
        ]

    def test_every_position_appears_once(self) -> None:
        shuffled = shuffle_openings(list(self._FENS), seed=3)
        assert sorted(shuffled) == sorted(self._FENS)

    def test_the_callers_list_is_left_alone(self) -> None:
        fens = list(self._FENS)
        shuffle_openings(fens, seed=3)
        assert fens == list(self._FENS)


class TestOpeningPair:
    """Tests for OpeningPair dataclass."""

    def test_frozen(self) -> None:
        pair = OpeningPair(fen="some fen", swap_colors=False)
        with pytest.raises(AttributeError):
            pair.fen = "other"  # type: ignore[misc]

    def test_equality(self) -> None:
        p1 = OpeningPair(fen="fen1", swap_colors=True)
        p2 = OpeningPair(fen="fen1", swap_colors=True)
        assert p1 == p2


class TestLoadPGNOpenings:
    """Tests for PGN opening book loading."""

    @pytest.fixture
    def pgn_file(self, tmp_path: Path) -> Path:
        """Create a sample PGN file with two games."""
        content = (
            '[Event "Test"]\n'
            '[Result "*"]\n'
            "\n"
            "1. e4 e5 *\n"
            "\n"
            '[Event "Test2"]\n'
            '[Result "*"]\n'
            "\n"
            "1. d4 d5 2. c4 *\n"
        )
        pgn_path = tmp_path / "openings.pgn"
        pgn_path.write_text(content)
        return pgn_path

    def test_load_pgn_returns_fens(self, pgn_file: Path) -> None:
        fens = load_pgn_openings(pgn_file)
        assert len(fens) == 2

    def test_load_pgn_replays_moves(self, pgn_file: Path) -> None:
        """FEN should reflect position after all moves played."""
        fens = load_pgn_openings(pgn_file)
        # After 1.e4 e5
        assert "pppp1ppp" in fens[0]  # Black pawn moved from e7
        # After 1.d4 d5 2.c4 - both c and d pawns advanced
        assert "2PP4" in fens[1]  # White pawns on c4 and d4

    def test_load_pgn_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_pgn_openings(tmp_path / "nonexistent.pgn")

    def test_load_pgn_empty_file(self, tmp_path: Path) -> None:
        pgn_path = tmp_path / "empty.pgn"
        pgn_path.write_text("")
        fens = load_pgn_openings(pgn_path)
        assert fens == []

    @pytest.mark.parametrize("setup", ['[SetUp "1"]\n', ""], ids=["with_setup", "without_setup"])
    def test_a_game_with_a_fen_and_no_moves_is_its_fen(self, tmp_path: Path, setup: str) -> None:
        """A game that sets up a position, with no moves, is still an opening (#12).

        python-chess reads the FEN with or without a ``SetUp`` tag.
        """
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        pgn_path = tmp_path / "setup.pgn"
        pgn_path.write_text(f'[Event "T"]\n{setup}[FEN "{fen}"]\n\n*\n')
        assert load_pgn_openings(pgn_path) == [fen]

    def test_the_games_after_one_that_describes_no_opening_still_load(self, tmp_path: Path) -> None:
        """Text that isn't PGN is skipped, and the game after it is still an opening (#12)."""
        pgn_path = tmp_path / "mixed.pgn"
        pgn_path.write_text('not a position\n\n[Event "T"]\n\n1. e4 *\n')
        assert load_pgn_openings(pgn_path) == [
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        ]


class TestLoadOpenings:
    """Tests for the unified load_openings dispatcher."""

    def test_dispatches_to_epd(self, tmp_path: Path) -> None:
        content = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1 ; e4\n"
        epd_path = tmp_path / "book.epd"
        epd_path.write_text(content)
        fens = load_openings(epd_path)
        assert len(fens) == 1

    def test_dispatches_to_pgn(self, tmp_path: Path) -> None:
        content = '[Event "T"]\n[Result "*"]\n\n1. e4 *\n'
        pgn_path = tmp_path / "book.pgn"
        pgn_path.write_text(content)
        fens = load_openings(pgn_path)
        assert len(fens) == 1

    def test_unsupported_extension(self, tmp_path: Path) -> None:
        txt_path = tmp_path / "book.txt"
        txt_path.write_text("some content")
        with pytest.raises(ValueError, match="Unsupported opening book format"):
            load_openings(txt_path)

    @pytest.mark.parametrize(
        ("name", "content"),
        [
            ("empty.epd", ""),
            ("unparseable.epd", "not a position\n# a comment\n\n"),
            ("empty.pgn", ""),
            ("not_pgn.pgn", "not a position\n"),
            ("epd_as_pgn.pgn", "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1\n"),
            ("illegal_first_move.pgn", '[Event "T"]\n\n1. e5 *\n'),
        ],
        ids=[
            "empty_epd",
            "no_line_parses",
            "empty_pgn",
            "not_pgn",
            "epd_as_pgn",
            "illegal_first_move",
        ],
    )
    def test_a_book_with_no_positions_is_an_error(
        self, tmp_path: Path, name: str, content: str
    ) -> None:
        """A book with nothing to play is refused, not read as an empty list (#12).

        The runner used to fall back to the initial position for one. python-chess
        reads text that isn't PGN, and a game whose first move is illegal, as a
        game with no moves, which would be the initial position. The error names
        the file, not its path, since the backend relays it to the browser.
        """
        path = tmp_path / name
        path.write_text(content)
        with pytest.raises(ValueError, match=re.escape(f"no positions in {name}")) as exc_info:
            load_openings(path)
        assert str(tmp_path) not in str(exc_info.value)
