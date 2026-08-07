"""Tests for game adjudication logic."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import MagicMock

import chess
import pytest
from sprt_runner.adjudication import (
    AdjudicationConfig,
    AdjudicationType,
    check_adjudication,
    validate_syzygy_path,
)


class TestAdjudicationConfig:
    """Tests for AdjudicationConfig defaults and validation."""

    def test_default_config(self) -> None:
        config = AdjudicationConfig()
        assert config.win_threshold_cp == 1000
        assert config.win_consecutive_moves == 5
        assert config.draw_threshold_cp == 10
        assert config.draw_consecutive_moves == 10
        assert config.draw_min_move == 40

    def test_custom_config(self) -> None:
        config = AdjudicationConfig(
            win_threshold_cp=500,
            win_consecutive_moves=3,
            draw_threshold_cp=5,
            draw_consecutive_moves=8,
            draw_min_move=30,
        )
        assert config.win_threshold_cp == 500
        assert config.win_consecutive_moves == 3


class TestWinAdjudication:
    """Tests for win adjudication logic."""

    def test_no_adjudication_insufficient_moves(self) -> None:
        """Not enough consecutive evaluations above threshold."""
        config = AdjudicationConfig(win_threshold_cp=1000, win_consecutive_moves=3)
        # Only 2 moves above threshold
        white_scores = [1100, 1200]
        black_scores = [-1100, -1200]
        result = check_adjudication(
            white_scores, black_scores, move_number=10, config=config, tablebase=None
        )
        assert result is None

    def test_win_adjudication_white_wins(self) -> None:
        """Both engines agree white is winning for enough moves."""
        config = AdjudicationConfig(win_threshold_cp=1000, win_consecutive_moves=3)
        white_scores = [1100, 1200, 1300]
        black_scores = [-1100, -1200, -1300]
        result = check_adjudication(
            white_scores, black_scores, move_number=10, config=config, tablebase=None
        )
        assert result is not None
        assert result.adjudication_type == AdjudicationType.WIN_WHITE

    def test_win_adjudication_black_wins(self) -> None:
        """Both engines agree black is winning for enough moves."""
        config = AdjudicationConfig(win_threshold_cp=1000, win_consecutive_moves=3)
        white_scores = [-1100, -1200, -1300]
        black_scores = [1100, 1200, 1300]
        result = check_adjudication(
            white_scores, black_scores, move_number=10, config=config, tablebase=None
        )
        assert result is not None
        assert result.adjudication_type == AdjudicationType.WIN_BLACK

    def test_no_win_disagreement(self) -> None:
        """One engine thinks it's winning, other doesn't -> no adjudication."""
        config = AdjudicationConfig(win_threshold_cp=1000, win_consecutive_moves=3)
        white_scores = [1100, 1200, 1300]
        black_scores = [100, 200, 300]  # Black thinks it's winning too
        result = check_adjudication(
            white_scores, black_scores, move_number=10, config=config, tablebase=None
        )
        assert result is None

    def test_win_resets_on_below_threshold(self) -> None:
        """Counter resets if score drops below threshold."""
        config = AdjudicationConfig(win_threshold_cp=1000, win_consecutive_moves=3)
        # 2 above, 1 below, 2 above - not enough consecutive
        white_scores = [1100, 1200, 500, 1100, 1200]
        black_scores = [-1100, -1200, -500, -1100, -1200]
        result = check_adjudication(
            white_scores, black_scores, move_number=10, config=config, tablebase=None
        )
        assert result is None


class TestDrawAdjudication:
    """Tests for draw adjudication logic."""

    def test_draw_adjudication(self) -> None:
        """Both engines agree position is drawn for enough moves."""
        config = AdjudicationConfig(draw_threshold_cp=10, draw_consecutive_moves=3, draw_min_move=5)
        white_scores = [5, -3, 8]
        black_scores = [-2, 7, -5]
        result = check_adjudication(
            white_scores, black_scores, move_number=10, config=config, tablebase=None
        )
        assert result is not None
        assert result.adjudication_type == AdjudicationType.DRAW

    def test_no_draw_too_early(self) -> None:
        """Draw adjudication should not trigger before min_move."""
        config = AdjudicationConfig(
            draw_threshold_cp=10, draw_consecutive_moves=3, draw_min_move=40
        )
        white_scores = [5, -3, 8]
        black_scores = [-2, 7, -5]
        result = check_adjudication(
            white_scores, black_scores, move_number=10, config=config, tablebase=None
        )
        assert result is None

    def test_no_draw_eval_too_high(self) -> None:
        """One eval above threshold -> no draw."""
        config = AdjudicationConfig(draw_threshold_cp=10, draw_consecutive_moves=3, draw_min_move=5)
        white_scores = [5, -3, 50]
        black_scores = [-2, 7, -5]
        result = check_adjudication(
            white_scores, black_scores, move_number=10, config=config, tablebase=None
        )
        assert result is None


class TestAdjudicationDisabled:
    """Tests for disabling adjudication."""

    def test_no_adjudication_with_none_scores(self) -> None:
        """Empty score lists should not trigger adjudication."""
        config = AdjudicationConfig()
        result = check_adjudication([], [], move_number=100, config=config, tablebase=None)
        assert result is None

    def test_disabled_win_adjudication(self) -> None:
        """Setting win_consecutive_moves=0 disables win adjudication."""
        config = AdjudicationConfig(win_consecutive_moves=0)
        white_scores = [1100, 1200, 1300]
        black_scores = [-1100, -1200, -1300]
        result = check_adjudication(
            white_scores, black_scores, move_number=10, config=config, tablebase=None
        )
        assert result is None

    def test_disabled_draw_adjudication(self) -> None:
        """Setting draw_consecutive_moves=0 disables draw adjudication."""
        config = AdjudicationConfig(draw_consecutive_moves=0, draw_min_move=0)
        white_scores = [5, -3, 8]
        black_scores = [-2, 7, -5]
        result = check_adjudication(
            white_scores, black_scores, move_number=10, config=config, tablebase=None
        )
        assert result is None


class TestSyzygyAdjudication:
    """Tests for Syzygy tablebase adjudication logic.

    The tablebase handle is opened once per game by the caller (see
    ``game.py``) and passed in directly — ``check_adjudication`` never
    opens tablebases itself, so these tests pass a mock handle rather
    than patching ``chess.syzygy.open_tablebase``.
    """

    def test_syzygy_white_wins(self, tmp_path: Path) -> None:
        """Tablebase probe shows white wins."""
        config = AdjudicationConfig(
            syzygy_path=tmp_path, win_consecutive_moves=0, draw_consecutive_moves=0
        )
        # KQ vs K — white to move, white wins
        board = chess.Board("8/8/8/8/8/8/1k6/KQ6 w - - 0 1")
        mock_tb = MagicMock()
        mock_tb.probe_wdl.return_value = 2  # Win

        result = check_adjudication(
            [], [], move_number=100, config=config, board=board, tablebase=mock_tb
        )

        assert result is not None
        assert result.adjudication_type == AdjudicationType.WIN_WHITE
        assert "Syzygy" in result.reason

    def test_syzygy_black_wins(self, tmp_path: Path) -> None:
        """Tablebase probe shows black wins (side to move wins, turn is black)."""
        config = AdjudicationConfig(
            syzygy_path=tmp_path, win_consecutive_moves=0, draw_consecutive_moves=0
        )
        board = chess.Board("8/8/8/8/8/8/1K6/kq6 b - - 0 1")
        mock_tb = MagicMock()
        mock_tb.probe_wdl.return_value = 2  # Win for side to move (black)

        result = check_adjudication(
            [], [], move_number=100, config=config, board=board, tablebase=mock_tb
        )

        assert result is not None
        assert result.adjudication_type == AdjudicationType.WIN_BLACK

    def test_syzygy_draw(self, tmp_path: Path) -> None:
        """Tablebase probe shows draw."""
        config = AdjudicationConfig(
            syzygy_path=tmp_path, win_consecutive_moves=0, draw_consecutive_moves=0
        )
        board = chess.Board("8/8/8/8/8/8/1k6/K7 w - - 0 1")
        mock_tb = MagicMock()
        mock_tb.probe_wdl.return_value = 0  # Draw

        result = check_adjudication(
            [], [], move_number=100, config=config, board=board, tablebase=mock_tb
        )

        assert result is not None
        assert result.adjudication_type == AdjudicationType.DRAW
        assert "Syzygy" in result.reason

    def test_syzygy_too_many_pieces(self, tmp_path: Path) -> None:
        """Should not probe tablebase when too many pieces on board."""
        config = AdjudicationConfig(
            syzygy_path=tmp_path, win_consecutive_moves=0, draw_consecutive_moves=0
        )
        board = chess.Board()  # Starting position has 32 pieces
        mock_tb = MagicMock()

        result = check_adjudication(
            [], [], move_number=100, config=config, board=board, tablebase=mock_tb
        )

        assert result is None
        mock_tb.probe_wdl.assert_not_called()

    def test_syzygy_disabled_when_no_path(self) -> None:
        """No tablebase adjudication when syzygy_path is None."""
        config = AdjudicationConfig(
            syzygy_path=None, win_consecutive_moves=0, draw_consecutive_moves=0
        )
        board = chess.Board("8/8/8/8/8/8/1k6/KQ6 w - - 0 1")
        result = check_adjudication(
            [], [], move_number=100, config=config, board=board, tablebase=None
        )
        assert result is None

    def test_syzygy_key_error_returns_none(self, tmp_path: Path) -> None:
        """KeyError from tablebase probe (position not found) returns None."""
        config = AdjudicationConfig(
            syzygy_path=tmp_path, win_consecutive_moves=0, draw_consecutive_moves=0
        )
        board = chess.Board("8/8/8/8/8/8/1k6/KQ6 w - - 0 1")
        mock_tb = MagicMock()
        mock_tb.probe_wdl.side_effect = KeyError("not found")

        result = check_adjudication(
            [], [], move_number=100, config=config, board=board, tablebase=mock_tb
        )

        assert result is None

    def test_syzygy_side_to_move_loses(self, tmp_path: Path) -> None:
        """When WDL < 0, side to move loses — result depends on who moved."""
        config = AdjudicationConfig(
            syzygy_path=tmp_path, win_consecutive_moves=0, draw_consecutive_moves=0
        )
        # White to move but loses
        board = chess.Board("8/8/8/8/8/8/1k6/K7 w - - 0 1")
        mock_tb = MagicMock()
        mock_tb.probe_wdl.return_value = -2  # Loss for side to move

        result = check_adjudication(
            [], [], move_number=100, config=config, board=board, tablebase=mock_tb
        )

        assert result is not None
        assert result.adjudication_type == AdjudicationType.WIN_BLACK

    def test_syzygy_explicit_none_handle_disables_without_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``tablebase=None`` with ``syzygy_path`` set is a silent, deliberate disable.

        ``tablebase`` has no default (see
        ``test_tablebase_parameter_is_required`` below), so the only way
        to reach this branch is to explicitly pass ``tablebase=None`` —
        e.g. because opening the tablebase already failed and was logged
        once, at the point of failure, by ``_open_tablebase`` in
        ``game.py``. Re-warning here on every position would just be
        per-move log spam (previously up to ~1000 duplicate warnings per
        game); this test pins that it no longer happens.
        """
        config = AdjudicationConfig(
            syzygy_path=tmp_path, win_consecutive_moves=0, draw_consecutive_moves=0
        )
        board = chess.Board("8/8/8/8/8/8/1k6/KQ6 w - - 0 1")

        with caplog.at_level(logging.WARNING):
            result = check_adjudication(
                [], [], move_number=100, config=config, board=board, tablebase=None
            )

        assert result is None
        assert caplog.records == []

    def test_tablebase_parameter_is_required(self, tmp_path: Path) -> None:
        """``tablebase`` has no default: omitting it fails immediately.

        This is the static guard against the original "silent no-op"
        hazard: a caller that sets ``config.syzygy_path`` but forgets to
        open and pass the handle now fails at type-check time (pyright
        strict flags a missing required argument) instead of silently
        losing tablebase adjudication at runtime.
        """
        config = AdjudicationConfig(
            syzygy_path=tmp_path, win_consecutive_moves=0, draw_consecutive_moves=0
        )
        board = chess.Board("8/8/8/8/8/8/1k6/KQ6 w - - 0 1")

        with pytest.raises(TypeError, match="tablebase"):
            check_adjudication(  # type: ignore[call-arg]
                [], [], move_number=100, config=config, board=board
            )

    def test_syzygy_cursed_win_is_draw(self, tmp_path: Path) -> None:
        """WDL == 1 (cursed win): mate is forcible but takes more than fifty
        moves to convert. play_game() enforces is_fifty_moves() as an automatic
        draw, so we adjudicate it as a draw rather than a decisive win."""
        config = AdjudicationConfig(
            syzygy_path=tmp_path, win_consecutive_moves=0, draw_consecutive_moves=0
        )
        board = chess.Board("8/8/8/8/8/8/1k6/KQ6 w - - 0 1")
        mock_tb = MagicMock()
        mock_tb.probe_wdl.return_value = 1  # Cursed win

        result = check_adjudication(
            [], [], move_number=100, config=config, board=board, tablebase=mock_tb
        )

        assert result is not None
        assert result.adjudication_type == AdjudicationType.DRAW

    def test_syzygy_blessed_loss_is_draw(self, tmp_path: Path) -> None:
        """WDL == -1 (blessed loss): the mirror of a cursed win — the side to
        move is theoretically lost but the fifty-move rule can save it, so
        this is a draw, not a decisive loss."""
        config = AdjudicationConfig(
            syzygy_path=tmp_path, win_consecutive_moves=0, draw_consecutive_moves=0
        )
        board = chess.Board("8/8/8/8/8/8/1k6/KQ6 w - - 0 1")
        mock_tb = MagicMock()
        mock_tb.probe_wdl.return_value = -1  # Blessed loss

        result = check_adjudication(
            [], [], move_number=100, config=config, board=board, tablebase=mock_tb
        )

        assert result is not None
        assert result.adjudication_type == AdjudicationType.DRAW


class TestValidateSyzygyPath:
    """Tests for validate_syzygy_path — fail-fast startup validation (issue #173).

    An explicitly-supplied ``--syzygy-path`` that is unusable must fail at
    startup rather than silently disabling tablebase adjudication for an
    entire multi-hour SPRT run.
    """

    def test_nonexistent_path_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        with pytest.raises(ValueError, match="not a directory"):
            validate_syzygy_path(str(missing))

    def test_file_not_directory_raises(self, tmp_path: Path) -> None:
        file_path = tmp_path / "not-a-directory.txt"
        file_path.write_text("hello")
        with pytest.raises(ValueError, match="not a directory"):
            validate_syzygy_path(str(file_path))

    def test_empty_directory_raises(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty-tables"
        empty_dir.mkdir()
        with pytest.raises(ValueError, match="no WDL tablebase files"):
            validate_syzygy_path(str(empty_dir))

    def test_dtz_only_directory_raises(self, tmp_path: Path) -> None:
        """A DTZ-only directory has no WDL tables, so every probe would no-op.

        The standard Syzygy distribution splits tables into sibling
        ``3-4-5-wdl/`` and ``3-4-5-dtz/`` directories, so pointing at the
        DTZ half is a realistic mistake. ``_check_syzygy`` only ever calls
        ``probe_wdl``, so such a directory must be rejected even though it
        is non-empty.
        """
        dtz_dir = tmp_path / "3-4-5-dtz"
        dtz_dir.mkdir()
        (dtz_dir / "KQvK.rtbz").touch()
        (dtz_dir / "KRvK.rtbz").touch()

        with pytest.raises(ValueError, match="no WDL tablebase files"):
            validate_syzygy_path(str(dtz_dir))

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root bypasses directory permissions",
    )
    def test_unreadable_directory_raises_value_error(self, tmp_path: Path) -> None:
        """An unreadable directory must surface as ValueError, not OSError.

        ``Path.is_dir()`` swallows the permission error and returns True, so
        the failure only appears inside ``add_directory``. main() catches
        ValueError alone, so an escaping PermissionError would exit with a
        traceback instead of the contracted JSON error line.
        """
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o000)
        try:
            with pytest.raises(ValueError, match="Cannot read Syzygy path"):
                validate_syzygy_path(str(locked))
        finally:
            locked.chmod(0o755)

    def test_empty_value_raises(self) -> None:
        """``--syzygy-path ""`` must fail rather than silently disable.

        An unset shell variable in a wrapper script (``--syzygy-path
        "$SYZYGY_DIR"``) is exactly the silent-no-op this flag's validation
        exists to prevent. Note ``Path("")`` is ``Path(".")``, so this has
        to be caught before the string becomes a Path.
        """
        with pytest.raises(ValueError, match="empty"):
            validate_syzygy_path("")

    def test_directory_with_wdl_table_returns_path(self, tmp_path: Path) -> None:
        tb_dir = tmp_path / "tables"
        tb_dir.mkdir()
        (tb_dir / "KQvK.rtbw").touch()

        assert validate_syzygy_path(str(tb_dir)) == tb_dir
