"""SPRT (Sequential Probability Ratio Test) statistics for chess engine testing.

Implements the log-likelihood ratio calculation and stopping conditions
using the logistic Elo model, following the methodology used in
Fishtest and similar chess engine testing frameworks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


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

    Uses the logistic model to compute the LLR of the observed results
    under H1 (elo=elo1) vs H0 (elo=elo0).

    The trinomial LLR uses win/draw/loss probabilities derived from
    the expected score and an empirical draw ratio.

    Args:
        wins: Number of wins for the tested engine.
        losses: Number of losses for the tested engine.
        draws: Number of drawn games.
        elo0: Null hypothesis Elo difference.
        elo1: Alternative hypothesis Elo difference.

    Returns:
        The log-likelihood ratio value.
    """
    total = wins + losses + draws
    if total == 0:
        return 0.0

    # Observed probabilities
    w = wins / total
    d = draws / total
    lo = losses / total

    # Expected scores under H0 and H1
    s0 = elo_to_score(elo0)
    s1 = elo_to_score(elo1)

    # Derive win/draw/loss probabilities from expected score and draw ratio
    # Using the trinomial model: score = w + d/2
    # With observed draw ratio as the estimate
    draw_ratio = d

    w0 = s0 - draw_ratio / 2.0
    l0 = 1.0 - s0 - draw_ratio / 2.0
    d0 = draw_ratio

    w1 = s1 - draw_ratio / 2.0
    l1 = 1.0 - s1 - draw_ratio / 2.0
    d1 = draw_ratio

    # Clamp probabilities to avoid log(0)
    eps = 1e-12
    w0 = max(eps, w0)
    l0 = max(eps, l0)
    d0 = max(eps, d0)
    w1 = max(eps, w1)
    l1 = max(eps, l1)
    d1 = max(eps, d1)

    # LLR = sum over categories of count * log(p1/p0)
    llr = 0.0
    if w > 0:
        llr += w * math.log(w1 / w0)
    if lo > 0:
        llr += lo * math.log(l1 / l0)
    if d > 0:
        llr += d * math.log(d1 / d0)

    return llr * total


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
