"""Tests for opening book loading and pair generation."""

from __future__ import annotations

from pathlib import Path

import pytest
from sprt_runner.openings import OpeningPair, load_epd_openings, make_opening_pairs


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
