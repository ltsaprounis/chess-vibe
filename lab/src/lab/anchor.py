"""``lab anchor``: lab/main's tip against random-engine and every milestone.

Every accepted change passed two SPRTs, but in simulation 2.5 to 5% of
changes worth nothing pass SPRT, so over hundreds of attempts useless ones
get in. Fixed-length matches against frozen older versions catch that drift
(#190).

``lab anchor`` plays lab/main's tip, the version, against random-engine
(built at the harness commit) and then against every milestone in the ledger,
oldest first. Each match plays the configured number of games, with the
version as the runner's test engine, so every result and Elo estimate is the
version's.

The version becomes the next milestone, automatically, if every game was
clean and its 95% Elo interval against the previous milestone (the newest,
or random-engine before there is one) lies above 0. Otherwise it's flagged.

Each engine tree gets one anchor run, as each gets one attempt: once a run
has started its first match, it's recorded on lab/main however it ends (a
failed match, a signal or a harness change included), and the same engine
code can't be anchored again in the hope of a better interval.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from lab.elo import describe_elo
from lab.git import GitError, RefUpdate
from lab.layout import ENGINE_DIR, ENGINE_ID, LAB_MAIN, LAB_MAIN_BRANCH, RANDOM_ENGINE_ID
from lab.ledger import LEDGER_PATH, AnchorEntry, Ledger, MatchRecord
from lab.outcome import Outcome
from lab.preflight import (
    Harness,
    harness_change,
    harness_state,
    read_ledger,
    require_identity,
    require_lab_main,
    require_not_checked_out,
    require_runner,
)
from lab.refusal import Refused
from lab.runs import RunReport, RunRequest
from lab.services import Lab, stop_requested
from lab.verdict import (
    HARNESS_CHANGED,
    INTERRUPTED,
    Judgement,
    clearly_stronger,
    judge_match,
)

# Why a version wasn't frozen, beside the reasons a match wasn't clean.
NOT_CLEARLY_STRONGER = "not_clearly_stronger"

RunOutcome = Literal["milestone", "flagged", "failed"]


@dataclass(frozen=True)
class _Opponent:
    """An opponent: its name in the ledger, and how the runner is told to build it."""

    name: str
    spec: str


@dataclass(frozen=True)
class _Prepared:
    """An anchor run that has passed its checks."""

    id: int
    tip: str
    engine_tree: str
    ledger: Ledger
    harness: Harness
    opponents: list[_Opponent]
    next_milestone: int


@dataclass(frozen=True)
class _Stopped:
    """Why a run stopped before playing every match."""

    reasons: list[str]
    message: str


def _describe_run(entry: AnchorEntry) -> str:
    if entry.milestone is not None:
        return f"frozen as milestone {entry.milestone}"
    return entry.outcome


def _prepare(lab: Lab) -> _Prepared:
    """The checks, which raise Refused, and the opponents."""
    git = lab.git
    require_identity(git)
    tip = require_lab_main(git)
    require_not_checked_out(git)
    ledger = read_ledger(git, tip)
    require_runner(lab.repo)
    tree = git.tree(tip, ENGINE_DIR)
    if tree is None:
        raise Refused("lab-main", f"{LAB_MAIN_BRANCH} has no {ENGINE_DIR}/")
    earlier = ledger.anchor_for_tree(tree)
    if earlier is not None:
        raise Refused(
            "retry",
            f"anchor run {earlier.id} already played this engine (tree {tree[:12]}), and it "
            f"was {_describe_run(earlier)}: each engine tree gets one anchor run",
        )
    harness = harness_state(git, lab.repo, lab.config.book)
    milestones = ledger.milestones()
    opponents = [_Opponent(RANDOM_ENGINE_ID, f"{RANDOM_ENGINE_ID}:{harness.commit}")]
    opponents += [
        _Opponent(f"milestone {m.milestone}", f"{ENGINE_ID}:{m.version}") for m in milestones
    ]
    return _Prepared(
        id=ledger.next_id(),
        tip=tip,
        engine_tree=tree,
        ledger=ledger,
        harness=harness,
        opponents=opponents,
        next_milestone=len(milestones) + 1,
    )


def _match_record(opponent: _Opponent, report: RunReport, judgement: Judgement) -> MatchRecord:
    """Record a match: its tally from ``complete``, or else from the last ``progress``."""
    complete, progress = report.complete, report.progress
    if complete is not None:
        games, wins, losses, draws = (
            complete.total_games,
            complete.wins,
            complete.losses,
            complete.draws,
        )
        errors = complete.errors
    elif progress is not None:
        games, wins, losses, draws = (
            progress.games_total,
            progress.wins,
            progress.losses,
            progress.draws,
        )
        errors = progress.errors
    else:
        games = wins = losses = draws = errors = 0
    # A match that doesn't count records its tally, but not its Elo.
    scored = complete if judgement.outcome != "fail" else None
    return MatchRecord(
        opponent=opponent.name,
        engine=opponent.spec,
        seed=report.seed,
        games=games,
        wins=wins,
        losses=losses,
        draws=draws,
        errors=errors,
        terminations=None if scored is None else dict(scored.terminations),
        elo=None if scored is None else scored.elo,
        elo_lower=None if scored is None else scored.elo_lower,
        elo_upper=None if scored is None else scored.elo_upper,
        duration_s=report.duration_s,
        outcome=judgement.outcome,
        reasons=list(judgement.reasons),
        message=judgement.message,
    )


def _judge(
    prepared: _Prepared, matches: list[MatchRecord], stopped: _Stopped | None
) -> tuple[RunOutcome, int | None, list[str], str]:
    """Decide the run's outcome, milestone, reasons and message."""
    version = f"{LAB_MAIN_BRANCH} at {prepared.tip[:12]}"
    if stopped is not None:
        return "failed", None, stopped.reasons, f"failed: {version}, {stopped.message}"
    previous = matches[-1]
    elo = describe_elo(
        previous.wins,
        previous.losses,
        previous.draws,
        previous.elo,
        previous.elo_lower,
        previous.elo_upper,
    )
    reasons: list[str] = []
    for match in matches:
        reasons += [reason for reason in match.reasons if reason not in reasons]
    clearly = clearly_stronger(previous)
    if not clearly:
        reasons.append(NOT_CLEARLY_STRONGER)
    if reasons:
        details = [] if clearly else [f"isn't clearly stronger than {previous.opponent}: {elo}"]
        details += [f"against {m.opponent}, {m.message}" for m in matches if m.reasons]
        return "flagged", None, reasons, f"flagged: {version} " + "; ".join(details)
    milestone = prepared.next_milestone
    message = (
        f"milestone {milestone}: {version} is clearly stronger than {previous.opponent}: {elo}"
    )
    return "milestone", milestone, [], message


def _entry(
    lab: Lab,
    prepared: _Prepared,
    matches: list[MatchRecord],
    stopped: _Stopped | None,
    started_at: datetime,
) -> AnchorEntry:
    outcome, milestone, reasons, message = _judge(prepared, matches, stopped)
    settings = lab.config.anchor
    return AnchorEntry(
        kind="anchor",
        id=prepared.id,
        started_at=started_at,
        finished_at=lab.now(),
        version=prepared.tip,
        engine_tree=prepared.engine_tree,
        harness=prepared.harness.commit,
        book=lab.config.book,
        book_sha256=prepared.harness.book_sha256,
        concurrency=lab.config.concurrency,
        tc=settings.tc,
        games=settings.games,
        max_time_losses=settings.max_time_losses,
        config_sha256=lab.config_sha256,
        matches=matches,
        previous=prepared.opponents[-1].name,
        outcome=outcome,
        milestone=milestone,
        reasons=reasons,
        message=message,
    )


def _record(lab: Lab, prepared: _Prepared, entry: AnchorEntry) -> Outcome:
    """Commit the ledger line on lab/main's tip."""
    text = prepared.ledger.append(entry)
    result = "failed" if entry.outcome == "failed" else _describe_run(entry)
    summary = f"lab: record anchor run {entry.id}, {result}"
    try:
        commit = lab.git.commit_file(prepared.tip, LEDGER_PATH, text, f"{summary}\n")
        lab.git.update_refs([RefUpdate(LAB_MAIN, commit, prepared.tip)], summary)
    except GitError as e:
        return Outcome(
            "failed",
            f"anchor run {entry.id} was {entry.outcome}, but the lab couldn't record it: {e}",
            "record",
            entry,
        )
    return Outcome(entry.outcome, entry.message, None, entry)


async def run_anchor(lab: Lab, stop: asyncio.Event) -> Outcome:
    """Play lab/main's tip against random-engine and every milestone.

    Args:
        lab: What the command works with.
        stop: Set to stop the run early, as a signal handler does.

    Returns:
        ``milestone``, ``flagged`` or ``failed`` with the entry recorded;
        ``refused``; or ``failed`` with nothing recorded, when a signal or a
        harness change stopped the run before its first match.
    """
    started_at = lab.now()
    try:
        prepared = _prepare(lab)
    except Refused as e:
        return Outcome("refused", e.message, e.check)
    settings = lab.config.anchor
    matches: list[MatchRecord] = []
    stopped: _Stopped | None = None
    for opponent in prepared.opponents:
        change = harness_change(lab.git, lab.repo, lab.config.book, prepared.harness)
        if change is not None:
            stopped = _Stopped(
                [HARNESS_CHANGED], f"before the match against {opponent.name}, {change}"
            )
            break
        if await stop_requested(stop):
            stopped = _Stopped(
                [INTERRUPTED], f"stopped by a signal before the match against {opponent.name}"
            )
            break
        request = RunRequest(
            label=f"anchor {prepared.id}: {LAB_MAIN_BRANCH} vs {opponent.name}",
            base=opponent.spec,
            test=f"{ENGINE_ID}:{prepared.tip}",
            tc=settings.tc,
            concurrency=lab.config.concurrency,
            book=lab.config.book,
            max_games=settings.games,
            timeout_s=settings.timeout_minutes * 60.0,
            early_stop=False,
        )
        report = await lab.runner.run(request, stop)
        judgement = judge_match(report, settings.max_time_losses)
        matches.append(_match_record(opponent, report, judgement))
        if judgement.outcome == "fail":
            stopped = _Stopped(
                list(judgement.reasons),
                f"the match against {opponent.name} failed: {judgement.message}",
            )
            break
    if stopped is not None and not matches:
        return Outcome("failed", f"{stopped.message}; nothing was recorded")
    return _record(lab, prepared, _entry(lab, prepared, matches, stopped, started_at))
