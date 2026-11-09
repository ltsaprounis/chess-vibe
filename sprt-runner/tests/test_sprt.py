"""Tests for SPRT statistics calculations."""

from __future__ import annotations

import math

import pytest
from sprt_runner.sprt import (
    SPRTDecision,
    calculate_llr,
    elo_to_score,
    sprt_bounds,
    sprt_test,
)


class TestEloToScore:
    """Tests for logistic Elo to expected score conversion."""

    def test_zero_elo_gives_half(self) -> None:
        assert elo_to_score(0.0) == pytest.approx(0.5)  # type: ignore[reportUnknownMemberType]

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
        assert s_pos + s_neg == pytest.approx(1.0)  # type: ignore[reportUnknownMemberType]

    def test_large_positive_elo(self) -> None:
        score = elo_to_score(800.0)
        assert score == pytest.approx(1.0, abs=0.01)  # type: ignore[reportUnknownMemberType]

    def test_large_negative_elo(self) -> None:
        score = elo_to_score(-800.0)
        assert score == pytest.approx(0.0, abs=0.01)  # type: ignore[reportUnknownMemberType]


class TestSPRTBounds:
    """Tests for SPRT stopping boundaries calculation."""

    def test_default_bounds(self) -> None:
        lower, upper = sprt_bounds(alpha=0.05, beta=0.05)
        # Lower bound should be log(beta / (1 - alpha))
        expected_lower = math.log(0.05 / 0.95)
        expected_upper = math.log(0.95 / 0.05)
        assert lower == pytest.approx(expected_lower)  # type: ignore[reportUnknownMemberType]
        assert upper == pytest.approx(expected_upper)  # type: ignore[reportUnknownMemberType]

    def test_asymmetric_bounds(self) -> None:
        lower, upper = sprt_bounds(alpha=0.05, beta=0.10)
        assert lower < 0
        assert upper > 0
        assert lower != -upper  # Asymmetric

    def test_bounds_ordering(self) -> None:
        lower, upper = sprt_bounds(alpha=0.05, beta=0.05)
        assert lower < upper

    def test_unequal_error_rates_go_to_the_right_bounds(self) -> None:
        """The lower bound is ln(beta/(1-alpha)) and the upper is ln((1-beta)/alpha).

        With alpha == beta the two are mirror images, so only unequal rates
        show whether alpha and beta are used in the right places. Swapped,
        alpha=0.05 and beta=0.10 would move the upper bound from 2.890 to 2.251
        and pass about 10% of worthless patches instead of 5%.
        """
        lower, upper = sprt_bounds(alpha=0.05, beta=0.10)
        assert lower == pytest.approx(math.log(0.10 / 0.95))  # type: ignore[reportUnknownMemberType]
        assert upper == pytest.approx(math.log(0.90 / 0.05))  # type: ignore[reportUnknownMemberType]


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
        result = sprt_test(wins=10, losses=5, draws=10, elo0=0.0, elo1=5.0, alpha=0.01, beta=0.01)
        # Stricter bounds = wider
        default = sprt_test(wins=10, losses=5, draws=10, elo0=0.0, elo1=5.0)
        assert result.upper_bound > default.upper_bound

    def test_unequal_error_rates_reach_the_bounds_in_order(self) -> None:
        """sprt_test hands alpha and beta to sprt_bounds without swapping them."""
        result = sprt_test(wins=10, losses=5, draws=10, elo0=0.0, elo1=5.0, alpha=0.05, beta=0.10)
        assert result.lower_bound == pytest.approx(math.log(0.10 / 0.95))  # type: ignore[reportUnknownMemberType]
        assert result.upper_bound == pytest.approx(math.log(0.90 / 0.05))  # type: ignore[reportUnknownMemberType]


class TestDrawRateFittedPerHypothesis:
    """The draw rate is fitted under each hypothesis, not fixed at the observed ratio (#196).

    Fixing it at the observed ratio drove a hypothesis's win or loss
    probability towards zero at high draw rates, so one decisive game could
    decide a test.

    The single-game tests below use #196's [0, elo1] bounds. At [elo0, 0]
    they hold with wins and losses swapped. No case was found with both
    bounds on the same side of 0 either. When the bounds straddle 0, one
    decisive game can still decide at near-total draw rates (#203): W1 L0 D3
    accepts H1 at [-200,200].
    """

    @pytest.mark.parametrize(
        ("wins", "losses", "draws", "elo0", "elo1", "expected"),
        [
            (0, 1, 17, 0.0, 20.0, -1.0659700124829994),
            (0, 1, 70, 0.0, 5.0, -1.0291241227690273),
            (0, 1, 7, 0.0, 50.0, -1.2338503228993878),
            (1, 0, 70, -5.0, 0.0, 1.0291241227690273),
            (1, 0, 2, -200.0, 200.0, 2.6807046433152903),
        ],
    )
    def test_one_decisive_game_does_not_decide(
        self,
        wins: int,
        losses: int,
        draws: int,
        elo0: float,
        elo1: float,
        expected: float,
    ) -> None:
        """The direct cases from #196, which used to end in H0 or H1.

        The LLR is pinned as well as the decision, because the [-200,200]
        case is one draw short of H1 (see the class docstring).
        """
        result = sprt_test(wins=wins, losses=losses, draws=draws, elo0=elo0, elo1=elo1)
        assert result.decision == SPRTDecision.CONTINUE
        assert result.llr == pytest.approx(expected, rel=1e-9)  # type: ignore[reportUnknownMemberType]

    def test_positive_score_at_high_draw_rate_is_not_rejected(self) -> None:
        """W40 L10 D950 at [0,20] scores 0.515, about +10.4 Elo.

        It used to give H0 with an LLR of -208.8. With 950 draws this LLR
        changes sign between W38 L12 (-0.87) and W39 L11 (+1.25), so a score of
        0.515 favours H1.
        """
        result = sprt_test(wins=40, losses=10, draws=950, elo0=0.0, elo1=20.0)
        assert result.decision == SPRTDecision.H1
        assert result.llr == pytest.approx(3.434454321339144, rel=1e-9)  # type: ignore[reportUnknownMemberType]

    @pytest.mark.parametrize(
        ("wins", "losses", "draws", "elo0", "elo1", "expected"),
        [
            (130, 100, 120, 0.0, 10.0, 1.0943221589785606),
            (95, 60, 845, 0.0, 20.0, 2.369589120317823),
            (25, 10, 1965, 0.0, 5.0, 0.9543443558415603),
            (40, 55, 300, -10.0, 0.0, -1.1191981325052809),
            (30, 20, 1950, 0.5, 2.5, 0.32152514685431244),
        ],
    )
    def test_llr_matches_reference_values(
        self,
        wins: int,
        losses: int,
        draws: int,
        elo0: float,
        elo1: float,
        expected: float,
    ) -> None:
        """Generalised LLR with the win/draw/loss probabilities fitted by maximum likelihood.

        Reference values for this class come from a separate 80-digit solver
        that maximises the log-likelihood by bisection on its derivative. They
        were not produced by this implementation's closed form.
        """
        llr = calculate_llr(wins=wins, losses=losses, draws=draws, elo0=elo0, elo1=elo1)
        assert llr == pytest.approx(expected, rel=1e-9)  # type: ignore[reportUnknownMemberType]

    def test_decisive_only_results_use_the_hypothesis_scores(self) -> None:
        """With no draws, each fit is the hypothesis's score as the win rate.

        So the LLR is W*ln(s1/s0) + L*ln((1 - s1)/(1 - s0)) exactly.
        """
        s1 = elo_to_score(5.0)
        expected = 10 * math.log(s1 / 0.5) + 5 * math.log((1.0 - s1) / 0.5)
        llr = calculate_llr(wins=10, losses=5, draws=0, elo0=0.0, elo1=5.0)
        assert llr == pytest.approx(expected, rel=1e-12)  # type: ignore[reportUnknownMemberType]

    @pytest.mark.parametrize(("elo1", "streak"), [(5.0, 204), (10.0, 101), (20.0, 50)])
    def test_draw_streak_accepts_h0(self, elo1: float, streak: int) -> None:
        """A patch at elo1 can draw at most 2*(1 - s1) of its games.

        So a run of nothing but draws is evidence against H1, and a long
        enough run accepts H0.
        """
        max_draw_rate = 2.0 * (1.0 - elo_to_score(elo1))
        llr = calculate_llr(wins=0, losses=0, draws=streak, elo0=0.0, elo1=elo1)
        assert llr == pytest.approx(streak * math.log(max_draw_rate), rel=1e-9)  # type: ignore[reportUnknownMemberType]
        before = sprt_test(wins=0, losses=0, draws=streak - 1, elo0=0.0, elo1=elo1)
        assert before.decision == SPRTDecision.CONTINUE
        at = sprt_test(wins=0, losses=0, draws=streak, elo0=0.0, elo1=elo1)
        assert at.decision == SPRTDecision.H0

    @pytest.mark.parametrize(
        ("wins", "losses", "draws", "elo0", "elo1", "expected"),
        [
            (0, 0, 7, -3e-6, 0.0, 6.044285953415483e-08),
            (1, 0, 100_000_000, 0.0, 1e-6, 0.28782314556096417),
        ],
    )
    def test_llr_stays_accurate_near_zero_elo(
        self,
        wins: int,
        losses: int,
        draws: int,
        elo0: float,
        elo1: float,
        expected: float,
    ) -> None:
        """Bounds within rounding of 0 Elo must not cost the fit its precision.

        Here the textbook discriminant, B**2 - 4*n*draws*a*b, cancels
        catastrophically. It gives 0.0 for the first case and 0.455 for the
        second, and with other counts it rounds below zero or gives a fitted
        probability of zero to an outcome that happened.
        """
        llr = calculate_llr(wins=wins, losses=losses, draws=draws, elo0=elo0, elo1=elo1)
        assert llr == pytest.approx(expected, rel=1e-6)  # type: ignore[reportUnknownMemberType]

    def test_double_root_survives_rounding(self) -> None:
        """With no losses the fit has a double root at a score of (W + D)/(W + 2*D).

        At the bound giving that score for W1 D3, the discriminant rounds to
        -1.1e-16. Without the clamp at zero its square root would raise.
        """
        llr = calculate_llr(wins=1, losses=0, draws=3, elo0=0.0, elo1=49.97549464331808)
        assert llr == pytest.approx(0.5341255704980716, rel=1e-9)  # type: ignore[reportUnknownMemberType]

    @pytest.mark.parametrize(
        ("wins", "losses", "draws", "elo0", "elo1"),
        [
            (1, 0, 0, 0.0, 7000.0),
            (0, 1, 0, 0.0, 7000.0),
            (5, 3, 10, 7000.0, 20.0),
            (1, 0, 0, 0.0, 6390.0),
            (0, 0, 0, 0.0, 7000.0),
            (1, 0, 0, -math.inf, 5.0),
            (1, 0, 0, 0.0, math.nan),
        ],
    )
    def test_rejects_bounds_without_a_score_strictly_between_0_and_1(
        self, wins: int, losses: int, draws: int, elo0: float, elo1: float
    ) -> None:
        """The fit needs both expected scores strictly between 0 and 1.

        From about 6,382 Elo the score is exactly 1.0 in floating point, and at
        -inf it is 0.0. Such a hypothesis allows only wins (or only losses),
        and the closed form then divides by zero or takes the log of zero. A
        NaN bound would make every LLR NaN, so the test could never stop. All
        of these are rejected with a message, even before any game is played.
        """
        with pytest.raises(ValueError, match="expected score"):
            calculate_llr(wins=wins, losses=losses, draws=draws, elo0=elo0, elo1=elo1)

    @pytest.mark.parametrize("elo1", [5.0, 10.0, 20.0])
    def test_lone_loss_decides_like_a_draw(self, elo1: float) -> None:
        """In a test whose only decisive game is a loss, the loss never decides it."""
        for draws in range(5001):
            one_loss = sprt_test(wins=0, losses=1, draws=draws, elo0=0.0, elo1=elo1)
            all_draws = sprt_test(wins=0, losses=0, draws=draws + 1, elo0=0.0, elo1=elo1)
            assert one_loss.decision == all_draws.decision, draws

    @pytest.mark.parametrize("elo1", [5.0, 10.0, 20.0])
    def test_lone_win_never_accepts_h1_or_hastens_h0(self, elo1: float) -> None:
        """In a test whose only decisive game is a win, the win never decides it."""
        for draws in range(5001):
            one_win = sprt_test(wins=1, losses=0, draws=draws, elo0=0.0, elo1=elo1)
            all_draws = sprt_test(wins=0, losses=0, draws=draws + 1, elo0=0.0, elo1=elo1)
            assert one_win.decision != SPRTDecision.H1, draws
            if one_win.decision == SPRTDecision.H0:
                assert all_draws.decision == SPRTDecision.H0, draws
