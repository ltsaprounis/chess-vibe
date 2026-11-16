"""Refusals: a lab command stopping before it plays or records anything."""

from __future__ import annotations

from typing import Literal

# What refused. Each has its own message; these names are stable, for the
# researcher agent to act on.
Check = Literal[
    "repo",
    "config",
    "busy",
    "identity",
    "lab-main",
    "checked-out",
    "unknown-commit",
    "base",
    "one-commit",
    "ledger",
    "rejected-ref",
    "runner",
    "scope",
    "retry",
    "harness",
    "book",
    "correctness",
]


class Refused(Exception):
    """A check failed before anything was played or recorded.

    Attributes:
        check: Which check refused.
        message: Why, and what would fix it.
    """

    def __init__(self, check: Check, message: str) -> None:
        super().__init__(message)
        self.check: Check = check
        self.message = message
