"""``lab report``: the ledger summed up, in plain text.

It reads only the ledger on lab/main: attempts by verdict and reason, the
acceptance rate, each attempt's SPRTs, the milestones and the Elo that froze
them, the latest anchor run's Elo against each opponent, and machine hours.
Machine hours are wall-clock hours from each command's start to its record,
since the lab runs one command at a time and each keeps the machine busy.

The layout is fixed, so the researcher agent can read it as reliably as a
person.
"""

from __future__ import annotations

from collections import Counter

from lab.elo import describe_elo
from lab.git import Repo
from lab.layout import LAB_MAIN_BRANCH
from lab.ledger import AnchorEntry, AttemptEntry, Ledger, StageRecord
from lab.preflight import read_ledger, require_lab_main

VERDICTS = ("accepted", "rejected", "failed")
SECONDS_PER_HOUR = 3600.0


def _breakdown(attempts: list[AttemptEntry], verdict: str) -> str:
    counts = Counter(reason for a in attempts if a.verdict == verdict for reason in a.reasons)
    if not counts:
        return ""
    return "  (" + ", ".join(f"{reason} {counts[reason]}" for reason in sorted(counts)) + ")"


def _counts(attempts: list[AttemptEntry]) -> list[str]:
    lines = [f"Attempts: {len(attempts)}"]
    for verdict in VERDICTS:
        count = sum(1 for a in attempts if a.verdict == verdict)
        lines.append(f"  {verdict:<8}  {count}{_breakdown(attempts, verdict)}")
    accepted = sum(1 for a in attempts if a.verdict == "accepted")
    if attempts:
        rate = f"{100 * accepted / len(attempts):.0f}% ({accepted} of {len(attempts)})"
    else:
        rate = "n/a"
    lines.append(f"Acceptance rate: {rate}")
    return lines


def _stage(stage: StageRecord) -> str:
    tally = f"W {stage.wins} L {stage.losses} D {stage.draws}"
    if stage.result is None:
        return f"  {stage.name:<7}  no result after {stage.games} games, {tally}"
    elo = describe_elo(
        stage.wins, stage.losses, stage.draws, stage.elo, stage.elo_lower, stage.elo_upper
    )
    return f"  {stage.name:<7}  {stage.result} after {stage.games} games, {tally}, Elo {elo}"


def _attempts(attempts: list[AttemptEntry]) -> list[str]:
    lines: list[str] = []
    for entry in attempts:
        reasons = f" ({', '.join(entry.reasons)})" if entry.reasons else ""
        lines.append(f"Attempt {entry.id}, {entry.verdict}{reasons}: {entry.hypothesis}")
        lines.extend(_stage(stage) for stage in entry.stages)
    return lines


def _milestones(anchors: list[AnchorEntry]) -> list[str]:
    frozen = [a for a in anchors if a.milestone is not None]
    if not frozen:
        return ["Milestones: none yet"]
    lines = ["Milestones:"]
    for entry in frozen:
        previous = entry.matches[-1]
        elo = describe_elo(
            previous.wins,
            previous.losses,
            previous.draws,
            previous.elo,
            previous.elo_lower,
            previous.elo_upper,
        )
        lines.append(
            f"  {entry.milestone}  {entry.version[:12]}  frozen by anchor run {entry.id}, "
            f"against {entry.previous}: {elo}"
        )
    return lines


def _latest_anchor(anchors: list[AnchorEntry]) -> list[str]:
    if not anchors:
        return ["Latest anchor run: none yet"]
    entry = anchors[-1]
    if entry.milestone is not None:
        result = f"milestone {entry.milestone}"
    else:
        result = f"{entry.outcome} ({', '.join(entry.reasons)})"
    lines = [f"Latest anchor run, {entry.id}: {LAB_MAIN_BRANCH} at {entry.version[:12]}, {result}"]
    width = max(len(m.opponent) for m in entry.matches)
    for m in entry.matches:
        tally = f"W {m.wins} L {m.losses} D {m.draws}"
        reasons = f"  ({', '.join(m.reasons)})" if m.reasons else ""
        if m.outcome == "fail":
            lines.append(
                f"  {m.opponent:<{width}}  no result after {m.games} games, {tally}{reasons}"
            )
            continue
        elo = describe_elo(m.wins, m.losses, m.draws, m.elo, m.elo_lower, m.elo_upper)
        lines.append(f"  {m.opponent:<{width}}  {tally}, Elo {elo}{reasons}")
    return lines


def _hours(attempts: list[AttemptEntry], anchors: list[AnchorEntry]) -> str:
    in_attempts = sum(a.duration_s for a in attempts) / SECONDS_PER_HOUR
    in_anchors = sum(a.duration_s for a in anchors) / SECONDS_PER_HOUR
    return (
        f"Machine hours: {in_attempts + in_anchors:.1f} "
        f"(attempts {in_attempts:.1f}, anchor runs {in_anchors:.1f})"
    )


def render_report(ledger: Ledger, tip: str) -> str:
    """Sum up a ledger.

    Args:
        ledger: The ledger.
        tip: The commit it was read from.

    Returns:
        The report, ending with a newline.
    """
    attempts = [e for e in ledger.entries if isinstance(e, AttemptEntry)]
    anchors = [e for e in ledger.entries if isinstance(e, AnchorEntry)]
    count = len(ledger.entries)
    entries = f"{count} ledger entries" if count else "no ledger entries yet"
    sections = [
        [f"{LAB_MAIN_BRANCH} at {tip[:12]}: {entries}"],
        _counts(attempts),
        _attempts(attempts),
        _milestones(anchors),
        _latest_anchor(anchors),
        [_hours(attempts, anchors)],
    ]
    return "\n\n".join("\n".join(lines) for lines in sections if lines) + "\n"


def report_text(git: Repo) -> str:
    """Read the ledger on lab/main and sum it up.

    Raises:
        Refused: If lab/main doesn't exist, or its ledger can't be read.
    """
    tip = require_lab_main(git)
    return render_report(read_ledger(git, tip), tip)
