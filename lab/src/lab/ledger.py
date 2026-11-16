"""The ledger: one line of JSON per attempt and per anchor run, only ever appended to.

The ledger is the lab's own file, not suite data, so it doesn't go through
``data/`` or the repository ABC. It lives at ``lab/ledger.jsonl`` in the tree
of ``lab/main``, the branch of accepted engine changes, so it stays with the
history it describes. The lab adds each line in a commit of its own on that
branch, built without touching any checkout (see :mod:`lab.git`).

Entry ids count up from 1 with no gaps, as do milestone numbers, and every
read checks both, so a ledger that lost or reordered a line is refused rather
than trusted.

An attempt is recorded once its candidate has reached the SPRT, and an
anchor run once its first match has started, whatever happens next. So the
same engine code can't be tested again, or anchored again, in the hope of a
better result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from lab.validation import describe

# Where the ledger lives in lab/main's tree.
LEDGER_PATH = "lab/ledger.jsonl"

Outcome = Literal["pass", "reject", "fail"]


class LedgerError(Exception):
    """The ledger can't be read, or an entry wouldn't keep it consistent."""


class _Record(BaseModel):
    """Strict, closed and immutable: no coercion, and no unknown fields."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class StageRecord(_Record):
    """One SPRT of an attempt: its settings, what the runner reported, and its judgement.

    Attributes:
        name: ``fast`` (step 5) or ``confirm`` (step 6).
        tc: Time control.
        elo0: Elo bound of the null hypothesis.
        elo1: Elo bound of the alternative hypothesis.
        alpha: Type-I error rate.
        beta: Type-II error rate.
        max_games: Game limit.
        max_time_losses: Most games either side could lose on time.
        seed: The seed of the book's order, which the runner's ``--seed``
            replays, or None if the run failed before choosing one.
        result: ``H0``, ``H1`` or ``inconclusive``, or None without a
            ``complete`` line.
        games: Games scored: the total from ``complete``, or else from the
            last ``progress`` line.
        wins: Wins for the candidate.
        losses: Losses for the candidate.
        draws: Drawn games.
        errors: Games that ended without a result.
        llr: Final log-likelihood ratio, if the run reported one.
        terminations: Scored games per way of ending, from ``complete``.
        elo: Elo estimate for the candidate, None where infinite or unknown.
        elo_lower: Lower end of its 95% interval.
        elo_upper: Upper end of its 95% interval.
        duration_s: Wall-clock time of the run.
        outcome: ``pass``, ``reject`` or ``fail``.
        reasons: Why it didn't pass.
        message: The judgement, for a person.
    """

    name: Literal["fast", "confirm"]
    tc: str
    elo0: float
    elo1: float
    alpha: float
    beta: float
    max_games: int
    max_time_losses: int
    seed: int | None
    result: str | None
    games: int
    wins: int
    losses: int
    draws: int
    errors: int
    llr: float | None
    terminations: dict[str, int] | None
    elo: float | None
    elo_lower: float | None
    elo_upper: float | None
    duration_s: float
    outcome: Outcome
    reasons: list[str]
    message: str


class AttemptEntry(_Record):
    """One candidate that reached the SPRT.

    Attributes:
        kind: Always ``attempt``.
        id: Its line number in the ledger, from 1.
        started_at: When the attempt started, in UTC.
        finished_at: When it was recorded, in UTC.
        hypothesis: What the change was meant to do.
        base: The commit tested against: lab/main's tip when it started.
        candidate: The commit tested, one commit on top of ``base``.
        engine_tree: The tree of ``engines/lab-engine/`` at ``candidate``.
            The same engine code gives the same tree, so it can't be tested
            twice.
        harness: The commit the working tree's runner, shared library and
            lab were at, clean.
        book: The opening book's path.
        book_sha256: The book's SHA-256.
        concurrency: Games played at once.
        config_sha256: The config file's SHA-256.
        correctness_commands: The commands step 4 ran.
        correctness_s: How long step 4 took.
        stages: The SPRTs run, in order: fast, then confirm if fast passed.
        verdict: ``accepted``, which advanced lab/main; ``rejected``; or
            ``failed``, when the harness failed or a signal stopped the
            attempt. Neither of the last two advanced lab/main, and both kept
            the candidate under ``refs/lab/rejected/<id>``.
        reasons: Why it wasn't accepted.
        message: The outcome, for a person.
    """

    kind: Literal["attempt"]
    id: int
    started_at: datetime
    finished_at: datetime
    hypothesis: str
    base: str
    candidate: str
    engine_tree: str
    harness: str
    book: str
    book_sha256: str
    concurrency: int
    config_sha256: str
    correctness_commands: list[list[str]]
    correctness_s: float
    stages: list[StageRecord]
    verdict: Literal["accepted", "rejected", "failed"]
    reasons: list[str]
    message: str

    @property
    def duration_s(self) -> float:
        """Wall-clock time from start to record."""
        return (self.finished_at - self.started_at).total_seconds()


class MatchRecord(_Record):
    """One fixed-length match of an anchor run, from the version's side.

    A match that failed has no ``complete`` line: its tally is the last
    ``progress`` line's, and ``terminations`` and the Elo are None.

    Attributes:
        opponent: ``random-engine``, or ``milestone <n>``.
        engine: The opponent as the runner was given it, ``ENGINE:COMMIT``.
        seed: The seed of the book's order.
        games: Games scored.
        wins: Wins for the version.
        losses: Losses for the version.
        draws: Drawn games.
        errors: Games that ended without a result.
        terminations: Scored games per way of ending.
        elo: Elo estimate for the version, None where infinite.
        elo_lower: Lower end of its 95% interval.
        elo_upper: Upper end of its 95% interval.
        duration_s: Wall-clock time of the match.
        outcome: ``pass`` if every game was clean, ``reject`` if not, and
            ``fail`` if the match didn't count.
        reasons: What wasn't clean, or why it failed.
        message: The result, for a person.
    """

    opponent: str
    engine: str
    seed: int | None
    games: int
    wins: int
    losses: int
    draws: int
    errors: int
    terminations: dict[str, int] | None
    elo: float | None
    elo_lower: float | None
    elo_upper: float | None
    duration_s: float
    outcome: Outcome
    reasons: list[str]
    message: str


class AnchorEntry(_Record):
    """One anchor run: lab/main's tip against random-engine and every milestone.

    Attributes:
        kind: Always ``anchor``.
        id: Its line number in the ledger, from 1.
        started_at: When the run started, in UTC.
        finished_at: When it was recorded, in UTC.
        version: The commit played: lab/main's tip.
        engine_tree: The tree of ``engines/lab-engine/`` at ``version``.
        harness: The commit the working tree's runner, shared library and
            lab were at, clean.
        book: The opening book's path.
        book_sha256: The book's SHA-256.
        concurrency: Games played at once.
        tc: Time control of every match.
        games: Games in each match.
        max_time_losses: Most games either side could lose on time in a match.
        config_sha256: The config file's SHA-256.
        matches: The matches played, random-engine first, then the
            milestones, oldest first. A failed run stops at its failed match.
        previous: The opponent the version had to be clearly stronger than:
            the newest milestone, or random-engine before there is one.
        outcome: ``milestone``, ``flagged``, or ``failed`` when a match failed
            or a signal or a harness change stopped the run.
        milestone: The number the version was frozen as; None unless
            ``outcome`` is ``milestone``.
        reasons: Why it wasn't frozen.
        message: The outcome, for a person.
    """

    kind: Literal["anchor"]
    id: int
    started_at: datetime
    finished_at: datetime
    version: str
    engine_tree: str
    harness: str
    book: str
    book_sha256: str
    concurrency: int
    tc: str
    games: int
    max_time_losses: int
    config_sha256: str
    matches: list[MatchRecord]
    previous: str
    outcome: Literal["milestone", "flagged", "failed"]
    milestone: int | None
    reasons: list[str]
    message: str

    @model_validator(mode="after")
    def _milestone_only_when_frozen(self) -> Self:
        if (self.outcome == "milestone") != (self.milestone is not None):
            raise ValueError(f"a {self.outcome} run with milestone {self.milestone}")
        return self

    @property
    def duration_s(self) -> float:
        """Wall-clock time from start to record."""
        return (self.finished_at - self.started_at).total_seconds()


Entry = AttemptEntry | AnchorEntry

_KINDS: dict[str, type[AttemptEntry] | type[AnchorEntry]] = {
    "attempt": AttemptEntry,
    "anchor": AnchorEntry,
}


def _parse_entry(line: str) -> Entry:
    """Read one line as the kind of entry it names.

    Raises:
        ValueError: If it isn't a JSON object naming a known kind.
        ValidationError: If it doesn't have that kind's fields.
    """
    try:
        data: object = json.loads(line)
    except json.JSONDecodeError as e:
        raise ValueError(f"isn't JSON ({e})") from e
    kind = cast("dict[str, object]", data).get("kind") if isinstance(data, dict) else None
    model = _KINDS.get(kind) if isinstance(kind, str) else None
    if model is None:
        raise ValueError(f"names no kind of entry the lab knows ({kind!r})")
    return model.model_validate_json(line, strict=True)


@dataclass(frozen=True)
class Ledger:
    """The ledger's entries, and its exact text to append to.

    Attributes:
        entries: Every entry, in order.
        text: The file's text as read.
    """

    entries: tuple[Entry, ...]
    text: str

    @classmethod
    def parse(cls, text: str) -> Ledger:
        """Read a ledger's text.

        Args:
            text: The file's contents; empty for a ledger with no entries.

        Returns:
            The ledger.

        Raises:
            LedgerError: If a line isn't an entry, or the ids or milestone
                numbers don't count up from 1. The message names the line.
        """
        lines = text.split("\n")
        if lines[-1] == "":
            lines.pop()
        entries: list[Entry] = []
        milestones = 0
        for number, line in enumerate(lines, start=1):
            try:
                entry = _parse_entry(line)
            except ValidationError as e:
                raise LedgerError(f"line {number} isn't a ledger entry: {describe(e)}") from e
            except ValueError as e:
                raise LedgerError(f"line {number} isn't a ledger entry: it {e}") from e
            if entry.id != number:
                raise LedgerError(f"line {number} has id {entry.id}, expected {number}")
            if isinstance(entry, AnchorEntry) and entry.milestone is not None:
                milestones += 1
                if entry.milestone != milestones:
                    raise LedgerError(
                        f"line {number} freezes milestone {entry.milestone}, expected {milestones}"
                    )
            entries.append(entry)
        return cls(tuple(entries), text)

    def next_id(self) -> int:
        """The id the next entry takes."""
        return len(self.entries) + 1

    def attempt_for_tree(self, tree: str) -> AttemptEntry | None:
        """The first attempt that tested this engine tree, whatever its verdict."""
        for entry in self.entries:
            if isinstance(entry, AttemptEntry) and entry.engine_tree == tree:
                return entry
        return None

    def anchor_for_tree(self, tree: str) -> AnchorEntry | None:
        """The first anchor run that played this engine tree, whatever its outcome."""
        for entry in self.entries:
            if isinstance(entry, AnchorEntry) and entry.engine_tree == tree:
                return entry
        return None

    def milestones(self) -> list[AnchorEntry]:
        """Every anchor run that froze its version as a milestone, oldest first."""
        return [
            entry
            for entry in self.entries
            if isinstance(entry, AnchorEntry) and entry.milestone is not None
        ]

    def append(self, entry: Entry) -> str:
        """The ledger's text with an entry added at the end.

        Args:
            entry: The next entry.

        Returns:
            The new text: the old text unchanged, then the entry's line.

        Raises:
            LedgerError: If the entry's id, or the milestone it freezes,
                isn't the next one.
        """
        if entry.id != self.next_id():
            raise LedgerError(f"entry {entry.id} isn't the next one, {self.next_id()}")
        if isinstance(entry, AnchorEntry) and entry.milestone is not None:
            expected = len(self.milestones()) + 1
            if entry.milestone != expected:
                raise LedgerError(f"milestone {entry.milestone} isn't the next one, {expected}")
        prefix = self.text if not self.text or self.text.endswith("\n") else f"{self.text}\n"
        return f"{prefix}{entry.model_dump_json()}\n"
