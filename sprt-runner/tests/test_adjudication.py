"""Tests for game adjudication logic."""

from __future__ import annotations

from sprt_runner.adjudication import (
    AdjudicationConfig,
    AdjudicationType,
    check_adjudication,
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
        result = check_adjudication(white_scores, black_scores, move_number=10, config=config)
        assert result is None

    def test_win_adjudication_white_wins(self) -> None:
        """Both engines agree white is winning for enough moves."""
        config = AdjudicationConfig(win_threshold_cp=1000, win_consecutive_moves=3)
        white_scores = [1100, 1200, 1300]
        black_scores = [-1100, -1200, -1300]
        result = check_adjudication(white_scores, black_scores, move_number=10, config=config)
        assert result is not None
        assert result.adjudication_type == AdjudicationType.WIN_WHITE

    def test_win_adjudication_black_wins(self) -> None:
        """Both engines agree black is winning for enough moves."""
        config = AdjudicationConfig(win_threshold_cp=1000, win_consecutive_moves=3)
        white_scores = [-1100, -1200, -1300]
        black_scores = [1100, 1200, 1300]
        result = check_adjudication(white_scores, black_scores, move_number=10, config=config)
        assert result is not None
        assert result.adjudication_type == AdjudicationType.WIN_BLACK

    def test_no_win_disagreement(self) -> None:
        """One engine thinks it's winning, other doesn't -> no adjudication."""
        config = AdjudicationConfig(win_threshold_cp=1000, win_consecutive_moves=3)
        white_scores = [1100, 1200, 1300]
        black_scores = [100, 200, 300]  # Black thinks it's winning too
        result = check_adjudication(white_scores, black_scores, move_number=10, config=config)
        assert result is None

    def test_win_resets_on_below_threshold(self) -> None:
        """Counter resets if score drops below threshold."""
        config = AdjudicationConfig(win_threshold_cp=1000, win_consecutive_moves=3)
        # 2 above, 1 below, 2 above - not enough consecutive
        white_scores = [1100, 1200, 500, 1100, 1200]
        black_scores = [-1100, -1200, -500, -1100, -1200]
        result = check_adjudication(white_scores, black_scores, move_number=10, config=config)
        assert result is None


class TestDrawAdjudication:
    """Tests for draw adjudication logic."""

    def test_draw_adjudication(self) -> None:
        """Both engines agree position is drawn for enough moves."""
        config = AdjudicationConfig(draw_threshold_cp=10, draw_consecutive_moves=3, draw_min_move=5)
        white_scores = [5, -3, 8]
        black_scores = [-2, 7, -5]
        result = check_adjudication(white_scores, black_scores, move_number=10, config=config)
        assert result is not None
        assert result.adjudication_type == AdjudicationType.DRAW

    def test_no_draw_too_early(self) -> None:
        """Draw adjudication should not trigger before min_move."""
        config = AdjudicationConfig(
            draw_threshold_cp=10, draw_consecutive_moves=3, draw_min_move=40
        )
        white_scores = [5, -3, 8]
        black_scores = [-2, 7, -5]
        result = check_adjudication(white_scores, black_scores, move_number=10, config=config)
        assert result is None

    def test_no_draw_eval_too_high(self) -> None:
        """One eval above threshold -> no draw."""
        config = AdjudicationConfig(draw_threshold_cp=10, draw_consecutive_moves=3, draw_min_move=5)
        white_scores = [5, -3, 50]
        black_scores = [-2, 7, -5]
        result = check_adjudication(white_scores, black_scores, move_number=10, config=config)
        assert result is None


class TestAdjudicationDisabled:
    """Tests for disabling adjudication."""

    def test_no_adjudication_with_none_scores(self) -> None:
        """Empty score lists should not trigger adjudication."""
        config = AdjudicationConfig()
        result = check_adjudication([], [], move_number=100, config=config)
        assert result is None

    def test_disabled_win_adjudication(self) -> None:
        """Setting win_consecutive_moves=0 disables win adjudication."""
        config = AdjudicationConfig(win_consecutive_moves=0)
        white_scores = [1100, 1200, 1300]
        black_scores = [-1100, -1200, -1300]
        result = check_adjudication(white_scores, black_scores, move_number=10, config=config)
        assert result is None

    def test_disabled_draw_adjudication(self) -> None:
        """Setting draw_consecutive_moves=0 disables draw adjudication."""
        config = AdjudicationConfig(draw_consecutive_moves=0, draw_min_move=0)
        white_scores = [5, -3, 8]
        black_scores = [-2, 7, -5]
        result = check_adjudication(white_scores, black_scores, move_number=10, config=config)
        assert result is None
