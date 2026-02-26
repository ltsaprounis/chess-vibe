"""Tests for SPRT statistics calculations."""

from __future__ import annotations

import math

import pytest
from sprt_runner.sprt import (
    SPRTDecision,
    SPRTResult,
    calculate_llr,
    elo_to_score,
    sprt_bounds,
    sprt_test,
)


class TestEloToScore:
    """Tests for logistic Elo to expected score conversion."""

    def test_zero_elo_gives_half(self) -> None:
        assert elo_to_score(0.0) == pytest.approx(0.5)

    def test_positive_elo_above_half(self) -> None:
        score = elo_to_score(100.0)
        assert score > 0.5
        assert score < 1.0

    def test_negative_elo_below_half(self) -> None:
        score = elo_to_score(-100.0)
        assert score < 0.5
        assert score > 0.0

    def test_symmetry(self) -> None:
        """Score for +N and -N should sum to 1.0."""
        s_pos = elo_to_score(50.0)
        s_neg = elo_to_score(-50.0)
        assert s_pos + s_neg == pytest.approx(1.0)

    def test_large_positive_elo(self) -> None:
        score = elo_to_score(800.0)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_large_negative_elo(self) -> None:
        score = elo_to_score(-800.0)
        assert score == pytest.approx(0.0, abs=0.01)


class TestSPRTBounds:
    """Tests for SPRT stopping boundaries calculation."""

    def test_default_bounds(self) -> None:
        lower, upper = sprt_bounds(alpha=0.05, beta=0.05)
        # Lower bound should be log(beta / (1 - alpha))
        expected_lower = math.log(0.05 / 0.95)
        expected_upper = math.log(0.95 / 0.05)
        assert lower == pytest.approx(expected_lower)
        assert upper == pytest.approx(expected_upper)

    def test_asymmetric_bounds(self) -> None:
        lower, upper = sprt_bounds(alpha=0.05, beta=0.10)
        assert lower < 0
        assert upper > 0
        assert lower != -upper  # Asymmetric

    def test_bounds_ordering(self) -> None:
        lower, upper = sprt_bounds(alpha=0.05, beta=0.05)
        assert lower < upper


class TestCalculateLLR:
    """Tests for log-likelihood ratio calculation."""

    def test_no_games_zero_llr(self) -> None:
        llr = calculate_llr(wins=0, losses=0, draws=0, elo0=0.0, elo1=5.0)
        assert llr == 0.0

    def test_positive_llr_for_wins(self) -> None:
        """Many wins against H0=0 Elo should give positive LLR."""
        llr = calculate_llr(wins=100, losses=50, draws=50, elo0=0.0, elo1=5.0)
        assert llr > 0

    def test_negative_llr_for_losses(self) -> None:
        """Many losses against H0=0 should give negative LLR."""
        llr = calculate_llr(wins=50, losses=100, draws=50, elo0=0.0, elo1=5.0)
        assert llr < 0

    def test_all_draws_near_zero(self) -> None:
        """Equal results should give LLR close to zero."""
        llr = calculate_llr(wins=50, losses=50, draws=100, elo0=0.0, elo1=5.0)
        assert abs(llr) < 5.0

    def test_known_values(self) -> None:
        """Verify LLR for a known result: 55 wins, 45 losses, 100 draws, elo0=0, elo1=5."""
        llr = calculate_llr(wins=55, losses=45, draws=100, elo0=0.0, elo1=5.0)
        # LLR should be positive (evidence for H1)
        assert llr > 0


class TestSPRTTest:
    """Tests for the full SPRT test function."""

    def test_continue_with_few_games(self) -> None:
        result = sprt_test(wins=5, losses=3, draws=2, elo0=0.0, elo1=5.0)
        assert result.decision == SPRTDecision.CONTINUE

    def test_accept_h1_with_many_wins(self) -> None:
        """Enough wins should trigger H1 acceptance."""
        result = sprt_test(wins=600, losses=400, draws=100, elo0=0.0, elo1=10.0)
        assert result.decision == SPRTDecision.H1

    def test_accept_h0_with_many_losses(self) -> None:
        """Enough losses should trigger H0 acceptance."""
        result = sprt_test(wins=400, losses=500, draws=100, elo0=0.0, elo1=10.0)
        assert result.decision == SPRTDecision.H0

    def test_result_contains_llr(self) -> None:
        result = sprt_test(wins=10, losses=5, draws=10, elo0=0.0, elo1=5.0)
        assert isinstance(result.llr, float)

    def test_result_contains_bounds(self) -> None:
        result = sprt_test(wins=10, losses=5, draws=10, elo0=0.0, elo1=5.0)
        assert result.lower_bound < result.upper_bound

    def test_custom_alpha_beta(self) -> None:
        result = sprt_test(
            wins=10, losses=5, draws=10, elo0=0.0, elo1=5.0, alpha=0.01, beta=0.01
        )
        # Stricter bounds = wider
        default = sprt_test(wins=10, losses=5, draws=10, elo0=0.0, elo1=5.0)
        assert result.upper_bound > default.upper_bound
