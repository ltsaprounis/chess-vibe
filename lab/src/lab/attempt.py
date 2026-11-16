"""``lab attempt``: test one candidate through the SPRT runner, and keep or reject it.

The researcher agent proposes a change as one commit on lab/main's tip
that touches only ``engines/lab-engine/``. This module decides its fate, so
the agent never judges its own results. Each step stops the attempt if it
fails:

1. **Scope:** the candidate changes only ``engines/lab-engine/``.
2. **No retries:** its engine tree isn't in the ledger already.
3. **Harness:** ``sprt-runner/``, ``shared/`` and ``lab/`` are clean; their
   commit and the book's hash are recorded.
4. **Correctness:** the candidate's own checks pass, at the candidate.
5. **Fast SPRT** at the fast time control.
6. **Confirmation SPRT** at the longer one, only after a pass at step 5.

Before step 1 come the preconditions every command shares (lab/main exists
and isn't checked out, the base is its tip, the candidate is one commit on
the base). A failure up to step 4 refuses the attempt: nothing about the
candidate's strength was measured, so nothing is recorded.

Once the candidate reaches the SPRT, the attempt is recorded whatever happens
next, so the same engine code can't be tested again in the hope of a pass.
Accepted, it advances lab/main with the ledger line on top. Rejected, or
failed when the harness fails or a signal stops it, the ledger line goes on
top of the base, and the candidate is kept under ``refs/lab/rejected/<id>``.
The branch and that ref move in one transaction.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from lab.config import StageSettings
from lab.git import GitError, RefUpdate
from lab.layout import ENGINE_DIR, ENGINE_ID, LAB_MAIN, LAB_MAIN_BRANCH, REJECTED_PREFIX
from lab.ledger import LEDGER_PATH, AttemptEntry, Ledger, StageRecord
from lab.outcome import Outcome
from lab.preflight import (
    Harness,
    harness_change,
    harness_state,
    list_paths,
    read_ledger,
    require_identity,
    require_lab_main,
    require_not_checked_out,
    require_runner,
    resolve_commit,
)
from lab.refusal import Refused
from lab.runs import RunReport, RunRequest
from lab.services import Lab, stop_requested
from lab.verdict import HARNESS_CHANGED, INTERRUPTED, Judgement, judge_sprt

Verdict = Literal["accepted", "rejected", "failed"]


@dataclass(frozen=True)
class AttemptRequest:
    """What ``lab attempt`` was asked to test.

    Attributes:
        hypothesis: What the change is meant to do.
        base: The commit it builds on, which must be lab/main's tip.
        candidate: The change, one commit on top of ``base``.
    """

    hypothesis: str
    base: str
    candidate: str


@dataclass(frozen=True)
class _Prepared:
    """An attempt that has passed steps 1 to 3."""

    id: int
    hypothesis: str
    tip: str
    base: str
    candidate: str
    engine_tree: str
    ledger: Ledger
    harness: Harness

    @property
    def rejected_ref(self) -> str:
        return f"{REJECTED_PREFIX}{self.id}"


def _prepare(lab: Lab, request: AttemptRequest) -> _Prepared:
    """The preconditions and steps 1 to 3, which raise Refused."""
    git = lab.git
    require_identity(git)
    tip = require_lab_main(git)
    require_not_checked_out(git)
    base = resolve_commit(git, request.base, "--base")
    candidate = resolve_commit(git, request.candidate, "--candidate")
    if base != tip:
        raise Refused(
            "base",
            f"--base must be {LAB_MAIN_BRANCH}'s tip, {tip[:12]}, but {request.base!r} is "
            f"{base[:12]}: build the candidate on the tip",
        )
    if git.parents(candidate) != [base]:
        raise Refused(
            "one-commit",
            "the candidate must be one commit whose only parent is --base: squash the change "
            "into one commit on the tip",
        )
    ledger = read_ledger(git, tip)
    attempt_id = ledger.next_id()
    rejected_ref = f"{REJECTED_PREFIX}{attempt_id}"
    if git.ref(rejected_ref) is not None:
        raise Refused(
            "rejected-ref",
            f"{rejected_ref} already exists, though the ledger's next attempt is {attempt_id}: "
            "something other than the lab wrote it, so move it aside first",
        )
    require_runner(lab.repo)

    # Step 1: scope.
    changed = git.changed_paths(base, candidate)
    outside = [path for path in changed if not path.startswith(f"{ENGINE_DIR}/")]
    if outside:
        raise Refused(
            "scope",
            f"the candidate changes files outside {ENGINE_DIR}/, which only the maintainer "
            f"changes: {list_paths(outside)}",
        )
    if not changed:
        raise Refused("scope", "the candidate changes nothing")
    tree = git.tree(candidate, ENGINE_DIR)
    if tree is None:
        raise Refused("scope", f"the candidate has no {ENGINE_DIR}/")

    # Step 2: no retries.
    earlier = ledger.attempt_for_tree(tree)
    if earlier is not None:
        raise Refused(
            "retry",
            f"attempt {earlier.id} already tested this engine code (tree {tree[:12]}) and "
            f"was {earlier.verdict}: each engine tree gets one attempt",
        )

    # Step 3: the harness, and the book.
    harness = harness_state(git, lab.repo, lab.config.book)
    return _Prepared(
        id=attempt_id,
        hypothesis=request.hypothesis,
        tip=tip,
        base=base,
        candidate=candidate,
        engine_tree=tree,
        ledger=ledger,
        harness=harness,
    )


def _stage_record(
    name: Literal["fast", "confirm"],
    settings: StageSettings,
    report: RunReport,
    judgement: Judgement,
) -> StageRecord:
    """Record a stage: its settings, its tally, and its judgement."""
    complete, progress = report.complete, report.progress
    if complete is not None:
        games, wins, losses, draws = (
            complete.total_games,
            complete.wins,
            complete.losses,
            complete.draws,
        )
        errors, llr = complete.errors, complete.llr
    elif progress is not None:
        games, wins, losses, draws = (
            progress.games_total,
            progress.wins,
            progress.losses,
            progress.draws,
        )
        errors, llr = progress.errors, progress.llr
    else:
        games = wins = losses = draws = errors = 0
        llr = None
    return StageRecord(
        name=name,
        tc=settings.tc,
        elo0=settings.elo0,
        elo1=settings.elo1,
        alpha=settings.alpha,
        beta=settings.beta,
        max_games=settings.max_games,
        max_time_losses=settings.max_time_losses,
        seed=report.seed,
        result=None if complete is None else complete.result,
        games=games,
        wins=wins,
        losses=losses,
        draws=draws,
        errors=errors,
        llr=llr,
        terminations=None if complete is None else dict(complete.terminations),
        elo=None if complete is None else complete.elo,
        elo_lower=None if complete is None else complete.elo_lower,
        elo_upper=None if complete is None else complete.elo_upper,
        duration_s=report.duration_s,
        outcome=judgement.outcome,
        reasons=list(judgement.reasons),
        message=judgement.message,
    )


@dataclass(frozen=True)
class _Played:
    """The SPRTs an attempt ran, and its verdict, explained in ``message``."""

    stages: list[StageRecord]
    verdict: Verdict
    reasons: list[str]
    message: str


async def _play(lab: Lab, prepared: _Prepared, stop: asyncio.Event) -> _Played | None:
    """Steps 5 and 6. None if nothing was played: the attempt then isn't recorded."""
    stages: list[StageRecord] = []
    to_play: list[tuple[Literal["fast", "confirm"], StageSettings]] = [
        ("fast", lab.config.fast),
        ("confirm", lab.config.confirm),
    ]
    for name, settings in to_play:
        change = harness_change(lab.git, lab.repo, lab.config.book, prepared.harness)
        if change is not None:
            if not stages:
                return None
            return _Played(stages, "failed", [HARNESS_CHANGED], f"before the {name} SPRT, {change}")
        if await stop_requested(stop):
            if not stages:
                return None
            return _Played(
                stages, "failed", [INTERRUPTED], f"stopped by a signal before the {name} SPRT"
            )
        report = await lab.runner.run(_request(lab, prepared, name, settings), stop)
        judgement = judge_sprt(report, settings.max_time_losses)
        stages.append(_stage_record(name, settings, report, judgement))
        if judgement.outcome != "pass":
            verdict: Verdict = "rejected" if judgement.outcome == "reject" else "failed"
            message = f"at the {name} SPRT, {judgement.message}"
            return _Played(stages, verdict, list(judgement.reasons), message)
    fast, confirm = stages
    return _Played(
        stages, "accepted", [], f"fast SPRT {fast.message}; confirmation SPRT {confirm.message}"
    )


def _request(lab: Lab, prepared: _Prepared, name: str, settings: StageSettings) -> RunRequest:
    return RunRequest(
        label=f"attempt {prepared.id} {name} SPRT",
        base=f"{ENGINE_ID}:{prepared.base}",
        test=f"{ENGINE_ID}:{prepared.candidate}",
        tc=settings.tc,
        concurrency=lab.config.concurrency,
        book=lab.config.book,
        max_games=settings.max_games,
        timeout_s=settings.timeout_minutes * 60.0,
        elo0=settings.elo0,
        elo1=settings.elo1,
        alpha=settings.alpha,
        beta=settings.beta,
        early_stop=True,
    )


def _entry(
    lab: Lab, prepared: _Prepared, played: _Played, started_at: datetime, correctness_s: float
) -> AttemptEntry:
    return AttemptEntry(
        kind="attempt",
        id=prepared.id,
        started_at=started_at,
        finished_at=lab.now(),
        hypothesis=prepared.hypothesis,
        base=prepared.base,
        candidate=prepared.candidate,
        engine_tree=prepared.engine_tree,
        harness=prepared.harness.commit,
        book=lab.config.book,
        book_sha256=prepared.harness.book_sha256,
        concurrency=lab.config.concurrency,
        config_sha256=lab.config_sha256,
        correctness_commands=[list(command) for command in lab.config.correctness.commands],
        correctness_s=correctness_s,
        stages=played.stages,
        verdict=played.verdict,
        reasons=played.reasons,
        message=f"{played.verdict}: {played.message}",
    )


def _record(lab: Lab, prepared: _Prepared, entry: AttemptEntry) -> Outcome:
    """Commit the ledger line, and move lab/main (and keep a rejected candidate) in one go."""
    accepted = entry.verdict == "accepted"
    text = prepared.ledger.append(entry)
    parent = prepared.candidate if accepted else prepared.base
    summary = f"lab: record attempt {entry.id}, {entry.verdict}"
    try:
        commit = lab.git.commit_file(
            parent, LEDGER_PATH, text, f"{summary}\n\nHypothesis: {entry.hypothesis}\n"
        )
        updates = [RefUpdate(LAB_MAIN, commit, prepared.tip)]
        if not accepted:
            updates.append(RefUpdate(prepared.rejected_ref, prepared.candidate, None))
        lab.git.update_refs(updates, summary)
    except GitError as e:
        return Outcome(
            "failed",
            f"attempt {entry.id} was {entry.verdict}, but the lab couldn't record it: {e}",
            "record",
            entry,
        )
    if accepted:
        message = (
            f"attempt {entry.id} accepted: {LAB_MAIN_BRANCH} is now {commit[:12]}; {entry.message}"
        )
    else:
        message = (
            f"attempt {entry.id} {entry.message}; the candidate is kept as {prepared.rejected_ref}"
        )
    return Outcome(entry.verdict, message, None, entry)


async def run_attempt(lab: Lab, request: AttemptRequest, stop: asyncio.Event) -> Outcome:
    """Test a candidate, and keep or reject it.

    Args:
        lab: What the command works with.
        request: The candidate.
        stop: Set to stop the attempt early, as a signal handler does.

    Returns:
        ``accepted``, ``rejected`` or ``failed`` with the entry recorded;
        ``refused`` with nothing recorded; or ``failed`` with nothing recorded,
        when a signal stopped the attempt before any game, or the harness
        changed before the first SPRT.
    """
    started_at = lab.now()
    try:
        prepared = _prepare(lab, request)
    except Refused as e:
        return Outcome("refused", e.message, e.check)

    # Step 4: the candidate's own checks.
    if await stop_requested(stop):
        return Outcome("failed", "stopped by a signal before the checks; nothing was recorded")
    correctness = await lab.check(prepared.candidate, lab.config.correctness, stop)
    if correctness.stopped:
        return Outcome(
            "failed", "stopped by a signal during the correctness checks; nothing was recorded"
        )
    if not correctness.passed:
        return Outcome(
            "refused", f"the candidate's checks failed: {correctness.message}", "correctness"
        )

    played = await _play(lab, prepared, stop)
    if played is None:
        return Outcome(
            "failed",
            "stopped before the first SPRT, by a signal or a change to the harness; "
            "nothing was recorded",
        )
    entry = _entry(lab, prepared, played, started_at, correctness.duration_s)
    return _record(lab, prepared, entry)
