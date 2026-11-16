"""The lab's verdicts: whether a run's result keeps or rejects a candidate.

These are the rules #192 sets, as plain functions of the runner's report, so
the agent proposing changes never judges its own results.

An SPRT stage (step 5 or 6 of ``lab attempt``) passes only on H1 with clean
games. Any game ending in ``engine_crash`` (which includes illegal moves), any
game ending without a result (``errors``), or more games lost on time than the
config allows rejects the candidate, even after H1. So do H0 and an
inconclusive run. A run that doesn't count, as :meth:`RunReport.failure`
describes, fails the stage instead: that's the harness's fault, not the
candidate's.

A fixed-length match of ``lab anchor`` is held to the same hygiene, and
:func:`clearly_stronger` decides whether its Elo interval lies above 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from lab.runs import ENGINE_CRASH, TIMEOUT, CompleteMessage, RunReport

# Why a stage rejects a candidate, in the order they're reported.
CRASHES = "engine_crash"
ERRORS = "errors"
TIME_LOSSES = "time_losses"
H0 = "h0"
INCONCLUSIVE = "inconclusive"

# Why a stage fails rather than judging the candidate.
RUNNER = "runner"
TIME_LIMIT = "time_limit"
INTERRUPTED = "interrupted"
HARNESS_CHANGED = "harness_changed"
UNEXPECTED_RESULT = "unexpected_result"

SPRT_RESULTS = ("H0", "H1", "inconclusive")
FIXED_RESULT = "fixed"

Outcome = Literal["pass", "reject", "fail"]


@dataclass(frozen=True)
class Judgement:
    """A run judged.

    Attributes:
        outcome: ``pass``; ``reject``, when the result counts against the
            candidate; or ``fail``, when the run doesn't count.
        reasons: Why it was rejected or failed, empty on a pass.
        message: One line for a person: the result, then each reason.
    """

    outcome: Outcome
    reasons: tuple[str, ...]
    message: str


def _games(count: int) -> str:
    return f"{count} game" if count == 1 else f"{count} games"


def _hygiene(complete: CompleteMessage, max_time_losses: int) -> list[tuple[str, str]]:
    """Reasons a run's games don't count as clean, each with its explanation."""
    found: list[tuple[str, str]] = []
    crashes = complete.terminations[ENGINE_CRASH]
    if crashes > 0:
        found.append((CRASHES, f"{_games(crashes)} ended in engine_crash, a crash or illegal move"))
    if complete.errors > 0:
        found.append((ERRORS, f"{_games(complete.errors)} ended without a result"))
    time_losses = complete.terminations[TIMEOUT]
    if time_losses > max_time_losses:
        found.append(
            (
                TIME_LOSSES,
                f"{_games(time_losses)} lost on time, more than the {max_time_losses} allowed",
            )
        )
    return found


def _summary(complete: CompleteMessage) -> str:
    return (
        f"{complete.result} after {_games(complete.total_games)}: "
        f"W {complete.wins} L {complete.losses} D {complete.draws}, LLR {complete.llr:.2f}"
    )


def _failed(report: RunReport) -> Judgement | None:
    """The failure, if the run doesn't count."""
    failure = report.failure()
    if failure is None:
        return None
    reason = {"timeout": TIME_LIMIT, "stopped": INTERRUPTED}.get(report.ending, RUNNER)
    return Judgement("fail", (reason,), failure)


def _judged(complete: CompleteMessage, found: list[tuple[str, str]]) -> Judgement:
    reasons = tuple(reason for reason, _ in found)
    message = "; ".join([_summary(complete), *(text for _, text in found)])
    return Judgement("reject" if reasons else "pass", reasons, message)


def judge_sprt(report: RunReport, max_time_losses: int) -> Judgement:
    """Judge one SPRT stage of an attempt.

    Args:
        report: The run.
        max_time_losses: Most games either side may lose on time.

    Returns:
        A pass on clean H1; a rejection for crashes, errors, too many time
        losses, H0 or no decision (every reason that applies, in that order);
        or a failure for a run that doesn't count or isn't an SPRT.
    """
    failed = _failed(report)
    if failed is not None:
        return failed
    complete = report.complete
    assert complete is not None  # a run that counts has one
    if complete.result not in SPRT_RESULTS:
        return Judgement(
            "fail",
            (UNEXPECTED_RESULT,),
            f"the runner's result was {complete.result!r}, not an SPRT decision",
        )
    found = _hygiene(complete, max_time_losses)
    if complete.result == "H0":
        found.append((H0, "SPRT accepted H0"))
    elif complete.result == "inconclusive":
        found.append((INCONCLUSIVE, "no SPRT decision by the game limit"))
    return _judged(complete, found)


def judge_match(report: RunReport, max_time_losses: int) -> Judgement:
    """Judge whether a fixed-length match was played cleanly.

    Args:
        report: The run.
        max_time_losses: Most games either side may lose on time.

    Returns:
        A pass if every game was clean; a rejection for crashes, errors or
        too many time losses; or a failure for a run that doesn't count or
        isn't a fixed-length match.
    """
    failed = _failed(report)
    if failed is not None:
        return failed
    complete = report.complete
    assert complete is not None  # a run that counts has one
    if complete.result != FIXED_RESULT:
        return Judgement(
            "fail",
            (UNEXPECTED_RESULT,),
            f"the runner's result was {complete.result!r}, not a fixed-length match",
        )
    return _judged(complete, _hygiene(complete, max_time_losses))


class Tally(Protocol):
    """A match's result: a ``complete`` line, or a recorded match."""

    @property
    def wins(self) -> int:
        """Wins for the engine under test."""
        ...

    @property
    def losses(self) -> int:
        """Its losses."""
        ...

    @property
    def draws(self) -> int:
        """Drawn games."""
        ...

    @property
    def elo_lower(self) -> float | None:
        """The lower end of its 95% Elo interval, None where infinite."""
        ...


def clearly_stronger(result: Tally) -> bool:
    """Whether the engine under test is clearly stronger: its 95% Elo interval lies above 0.

    The runner gives an infinite end of the interval as None. The lower end is
    +infinity only when every game was won; otherwise a None lower end is
    -infinity.

    Args:
        result: The match's result, from the engine under test's side.

    Returns:
        True if the interval's lower end is above 0.
    """
    if result.elo_lower is not None:
        return result.elo_lower > 0.0
    return result.wins > 0 and result.losses == 0 and result.draws == 0
