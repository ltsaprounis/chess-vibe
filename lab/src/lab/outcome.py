"""What a lab command did, its exit code, and the JSON line it prints."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from lab.ledger import AnchorEntry, AttemptEntry

OutcomeKind = Literal["accepted", "rejected", "milestone", "flagged", "refused", "failed"]

# Exit codes, the same for every command:
#   0  accepted, or frozen as a milestone; recorded
#   1  the lab itself crashed: a traceback, not an outcome
#   2  bad flags (typer's usage error)
#   3  refused: nothing was played or recorded
#   4  rejected, or flagged; recorded
#   5  failed: the harness failed, or a signal stopped the command; an
#      attempt that had reached the SPRT is recorded, anything else isn't
EXIT_CODES: dict[OutcomeKind, int] = {
    "accepted": 0,
    "milestone": 0,
    "refused": 3,
    "rejected": 4,
    "flagged": 4,
    "failed": 5,
}


@dataclass(frozen=True)
class Outcome:
    """A lab command's result.

    Attributes:
        kind: What happened.
        message: One line for a person.
        check: The check that refused, or ``record`` if the result couldn't be
            recorded; None otherwise.
        entry: The ledger entry recorded, or the one that couldn't be.
    """

    kind: OutcomeKind
    message: str
    check: str | None = None
    entry: AttemptEntry | AnchorEntry | None = None

    @property
    def exit_code(self) -> int:
        """The command's exit code."""
        return EXIT_CODES[self.kind]

    def to_json(self) -> str:
        """The outcome as the one line of JSON a command prints on stdout."""
        return json.dumps(
            {
                "outcome": self.kind,
                "check": self.check,
                "message": self.message,
                "entry": None if self.entry is None else self.entry.model_dump(mode="json"),
            }
        )
