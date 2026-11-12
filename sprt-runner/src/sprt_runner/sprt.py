"""SPRT (Sequential Probability Ratio Test) statistics for chess engine testing.

Implements the log-likelihood ratio calculation and stopping conditions
using the logistic Elo model. The LLR is a generalised one: under each
hypothesis, the win/draw/loss probabilities are fitted by maximum
likelihood subject to that hypothesis's expected score. It matches
Fishtest's ``LLR_logistic`` when all three outcomes have occurred. Fishtest
replaces zero counts with 1e-3 first, whereas this computes the exact fit.

It also estimates the Elo difference with a 95% confidence interval, as
cutechess-cli does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from statistics import NormalDist


class SPRTDecision(Enum):
    """Outcome of an SPRT stopping check."""

    H0 = "H0"
    H1 = "H1"
    CONTINUE = "continue"


@dataclass(frozen=True)
class SPRTResult:
    """Result of an SPRT test evaluation.

    Attributes:
        llr: Current log-likelihood ratio.
        lower_bound: Lower stopping boundary (reject H1 / accept H0).
        upper_bound: Upper stopping boundary (accept H1).
        decision: Whether to continue, accept H0, or accept H1.
    """

    llr: float
    lower_bound: float
    upper_bound: float
    decision: SPRTDecision


@dataclass(frozen=True)
class EloEstimate:
    """An Elo difference estimated from game results, with its 95% confidence interval.

    Each value is None where it would be infinite, at a score of 0% or 100%,
    since JSON has no infinity. With no games, all three are None.

    Attributes:
        elo: Estimated Elo difference, positive when the tested engine scored
            more than half the points.
        lower: Lower end of the 95% interval.
        upper: Upper end of the 95% interval.
    """

    elo: float | None
    lower: float | None
    upper: float | None


# The normal quantile for a two-sided 95% interval, 1.959964. cutechess-cli
# approximates it as 1.957162, so its intervals are about 0.14% narrower.
_Z_95 = NormalDist().inv_cdf(0.975)


def estimate_elo(wins: int, losses: int, draws: int) -> EloEstimate:
    """Estimate the Elo difference from game results, with a 95% confidence interval.

    This is cutechess-cli's estimate: the mean score, with a normal
    approximation to its spread from the variance of the win, draw and loss
    results, assuming independent games. Each end of the score's interval is
    converted to Elo on its own, so the interval in Elo is asymmetric. When
    every game has the same result there's no variance, so the interval has
    no width.

    Args:
        wins: Number of wins for the tested engine.
        losses: Number of losses for the tested engine.
        draws: Number of drawn games.

    Returns:
        The estimate. With no games, every value is None.
    """
    games = wins + losses + draws
    if games == 0:
        return EloEstimate(elo=None, lower=None, upper=None)
    score = (wins + draws / 2.0) / games
    variance = (wins * (1.0 - score) ** 2 + losses * score**2 + draws * (0.5 - score) ** 2) / games
    margin = _Z_95 * math.sqrt(variance / games)
    return EloEstimate(
        elo=_score_to_elo(score),
        lower=_score_to_elo(score - margin),
        upper=_score_to_elo(score + margin),
    )


def _score_to_elo(score: float) -> float | None:
    """Convert an expected score to an Elo difference, the inverse of ``elo_to_score``.

    Written as the log of ``score / (1 - score)``, an even score gives 0.0
    rather than -0.0.

    Args:
        score: Expected score of the tested engine.

    Returns:
        The Elo difference, or None for a score outside (0, 1), where it
        would be infinite.
    """
    if not 0.0 < score < 1.0:
        return None
    return 400.0 * math.log10(score / (1.0 - score))


def elo_to_score(elo: float) -> float:
    """Convert an Elo difference to an expected score using the logistic model.

    Args:
        elo: Elo rating difference (positive = stronger).

    Returns:
        Expected score in [0, 1].
    """
    return 1.0 / (1.0 + math.pow(10.0, -elo / 400.0))


def sprt_bounds(alpha: float = 0.05, beta: float = 0.05) -> tuple[float, float]:
    """Calculate SPRT stopping boundaries from Type-I/II error rates.

    Args:
        alpha: Type-I error rate (false positive probability).
        beta: Type-II error rate (false negative probability).

    Returns:
        Tuple of (lower_bound, upper_bound) for the LLR.
    """
    lower = math.log(beta / (1.0 - alpha))
    upper = math.log((1.0 - beta) / alpha)
    return lower, upper


def calculate_llr(
    wins: int,
    losses: int,
    draws: int,
    elo0: float,
    elo1: float,
) -> float:
    """Calculate the log-likelihood ratio for SPRT.

    Compares the best fit to the results under H1 (expected score of elo1)
    with the best fit under H0 (expected score of elo0), using the logistic
    Elo model. The draw rate is fitted separately under each hypothesis.

    Reusing the observed draw ratio under both hypotheses would be wrong at
    high draw rates. A hypothesis's win or loss probability would then
    approach or reach zero, and a single decisive game could decide the test
    (#196).

    Args:
        wins: Number of wins for the tested engine.
        losses: Number of losses for the tested engine.
        draws: Number of drawn games.
        elo0: Null hypothesis Elo difference.
        elo1: Alternative hypothesis Elo difference.

    Returns:
        The log-likelihood ratio value.

    Raises:
        ValueError: If either bound's expected score is not strictly between
            0 and 1. That covers bounds from about 6,382 Elo up, ``-inf``
            and NaN.
        OverflowError: From ``elo_to_score``, for bounds below about
            -123,000 Elo.
    """
    s0 = elo_to_score(elo0)
    s1 = elo_to_score(elo1)
    if not (0.0 < s0 < 1.0 and 0.0 < s1 < 1.0):
        raise ValueError(
            f"Elo bounds {elo0} and {elo1} must give an expected score strictly "
            f"between 0 and 1; got {s0} and {s1}"
        )

    total = wins + losses + draws
    if total == 0:
        return 0.0

    w0, d0, l0 = _fit_outcome_probabilities(wins, losses, draws, s0)
    w1, d1, l1 = _fit_outcome_probabilities(wins, losses, draws, s1)

    # An outcome that never happened adds nothing, even where a fit gives
    # it probability zero. One that did happen always has a positive fit.
    llr = 0.0
    for count, p1, p0 in ((wins, w1, w0), (losses, l1, l0), (draws, d1, d0)):
        if count > 0:
            llr += count * math.log(p1 / p0)
    return llr


def _fit_outcome_probabilities(
    wins: int, losses: int, draws: int, score: float
) -> tuple[float, float, float]:
    """Fit win, draw and loss probabilities by maximum likelihood at a fixed score.

    With the expected score fixed at ``w + d/2 = score``, the draw
    probability ``d`` is the only free parameter: ``w = score - d/2`` and
    ``l = 1 - score - d/2``. Setting the derivative of the log-likelihood to
    zero gives, with ``a = 2*score`` and ``b = 2*(1 - score)``::

        n*d**2 - (2*draws + wins*b + losses*a)*d + draws*a*b = 0

    In exact arithmetic the smaller root is the maximum and lies in
    ``[0, min(a, b)]``. With ``B = 2*draws + wins*b + losses*a``, it is
    computed as ``2*draws*a*b / (B + sqrt(disc))``. That is exactly 0 when
    ``draws`` is 0, and its denominator is positive whenever ``n`` is.

    The discriminant ``B**2 - 4*n*draws*a*b`` cancels catastrophically when
    ``score`` is close to 0.5 and draws dominate. With ``t = 1 - 2*score``,
    so that ``a = 1 - t`` and ``b = 1 + t``, it expands to
    ``(wins + losses)**2 + 2*t*(wins - losses)*(n + draws)
    + t**2*((wins - losses)**2 + 4*n*draws)``, which avoids that
    cancellation. The middle term can still cancel near a double root, such
    as the one at ``score = (wins + draws)/(wins + 2*draws)`` when there are
    no losses, so the result is clamped at zero.

    Args:
        wins: Number of wins for the tested engine.
        losses: Number of losses for the tested engine.
        draws: Number of drawn games. ``wins + losses + draws`` must be positive.
        score: Expected score of the tested engine, in (0, 1).

    Returns:
        Tuple of (win, draw, loss) probabilities.
    """
    a = 2.0 * score
    b = 2.0 * (1.0 - score)
    t = 1.0 - 2.0 * score
    n = wins + losses + draws
    decisive = wins + losses
    margin = wins - losses
    linear = 2.0 * draws + wins * b + losses * a
    discriminant = max(
        decisive * decisive
        + 2.0 * t * margin * (n + draws)
        + t * t * (margin * margin + 4.0 * n * draws),
        0.0,
    )
    draw_p = 2.0 * draws * a * b / (linear + math.sqrt(discriminant))
    return score - draw_p / 2.0, draw_p, 1.0 - score - draw_p / 2.0


def sprt_test(
    wins: int,
    losses: int,
    draws: int,
    elo0: float,
    elo1: float,
    *,
    alpha: float = 0.05,
    beta: float = 0.05,
) -> SPRTResult:
    """Run a complete SPRT evaluation on the given results.

    Args:
        wins: Number of wins for the tested engine.
        losses: Number of losses for the tested engine.
        draws: Number of drawn games.
        elo0: Null hypothesis Elo difference.
        elo1: Alternative hypothesis Elo difference.
        alpha: Type-I error rate.
        beta: Type-II error rate.

    Returns:
        SPRTResult with the LLR, bounds, and decision.
    """
    llr = calculate_llr(wins, losses, draws, elo0, elo1)
    lower, upper = sprt_bounds(alpha, beta)

    if llr >= upper:
        decision = SPRTDecision.H1
    elif llr <= lower:
        decision = SPRTDecision.H0
    else:
        decision = SPRTDecision.CONTINUE

    return SPRTResult(llr=llr, lower_bound=lower, upper_bound=upper, decision=decision)
