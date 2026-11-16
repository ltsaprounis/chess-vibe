"""Checks the lab's commands make before they play anything.

Each raises :class:`~lab.refusal.Refused` with a message that says what's
wrong and what would fix it, so the researcher agent (#194) can act on a
refusal without guessing.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from lab.git import GitError, Repo
from lab.layout import HARNESS_PATHS, LAB_MAIN, LAB_MAIN_BRANCH
from lab.ledger import LEDGER_PATH, Ledger, LedgerError
from lab.refusal import Refused
from lab.runs import RUNNER_PYTHON

# How many paths a refusal lists before summing up the rest.
SHOWN_PATHS = 5

# The harness's directories, for messages: "sprt-runner/, shared/ and lab/".
HARNESS_NAMES = ", ".join(f"{p}/" for p in HARNESS_PATHS[:-1]) + f" and {HARNESS_PATHS[-1]}/"


def list_paths(paths: list[str]) -> str:
    """List paths for a message, the first few by name."""
    shown = ", ".join(paths[:SHOWN_PATHS])
    rest = len(paths) - SHOWN_PATHS
    return f"{shown} and {rest} more" if rest > 0 else shown


def require_identity(git: Repo) -> None:
    """Git must have an identity, or the ledger's commit would fail after the games."""
    if not git.can_commit():
        raise Refused(
            "identity",
            "git has no identity to commit the ledger with: set user.name and user.email",
        )


def require_lab_main(git: Repo) -> str:
    """Return lab/main's tip. The lab never creates the branch itself."""
    tip = git.ref(LAB_MAIN)
    if tip is None:
        raise Refused(
            "lab-main",
            f"{LAB_MAIN_BRANCH} doesn't exist, and the lab never creates it: start it at "
            f"the baseline with `git branch {LAB_MAIN_BRANCH} <commit>`",
        )
    return tip


def require_not_checked_out(git: Repo) -> None:
    """lab/main mustn't be checked out: the lab moves it without updating any checkout."""
    if LAB_MAIN in git.checked_out_branches():
        raise Refused(
            "checked-out",
            f"{LAB_MAIN_BRANCH} is checked out in a worktree, which the lab would leave out "
            "of step when it moves the branch: switch that worktree to a detached HEAD "
            "(`git checkout --detach`)",
        )


def resolve_commit(git: Repo, rev: str, flag: str) -> str:
    """The full SHA of the commit an option names."""
    commit = git.commit(rev)
    if commit is None:
        raise Refused("unknown-commit", f"{flag} {rev!r} doesn't name a commit in this repository")
    return commit


def require_runner(repo: Path) -> None:
    """The runner's interpreter must be there to start the runner."""
    if not (repo / RUNNER_PYTHON).is_file():
        raise Refused(
            "runner", f"the runner's interpreter, {RUNNER_PYTHON}, isn't there: run `make setup`"
        )


def read_ledger(git: Repo, tip: str) -> Ledger:
    """Read the ledger at lab/main's tip; a branch without one has none yet."""
    try:
        text = git.read_file(tip, LEDGER_PATH)
        return Ledger.parse(text or "")
    except (GitError, LedgerError) as e:
        raise Refused(
            "ledger", f"{LEDGER_PATH} on {LAB_MAIN_BRANCH} ({tip[:12]}) can't be read: {e}"
        ) from e


def book_sha256(repo: Path, book: str) -> str:
    """The SHA-256 of the book the runner will read."""
    try:
        data = (repo / book).read_bytes()
    except OSError as e:
        raise Refused("book", f"the opening book {book} can't be read ({e.strerror or e})") from e
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Harness:
    """What plays, scores and judges the games, as the working tree has it.

    Attributes:
        commit: The commit checked out, recorded with every entry.
        trees: The tree of each harness path at that commit.
        book_sha256: The book's SHA-256.
    """

    commit: str
    trees: tuple[str | None, ...]
    book_sha256: str

    def same_code(self, other: Harness) -> bool:
        """Whether the harness's code and the book are unchanged."""
        return (self.trees, self.book_sha256) == (other.trees, other.book_sha256)


def harness_state(git: Repo, repo: Path, book: str) -> Harness:
    """The harness must be clean, so the recorded commit describes the code that runs."""
    dirty = git.dirty(HARNESS_PATHS)
    if dirty:
        raise Refused(
            "harness",
            f"the harness ({HARNESS_NAMES}) has uncommitted changes, so the commit the lab "
            f"records wouldn't be the code that ran: {list_paths(dirty)}",
        )
    head = git.head()
    if head is None:
        raise Refused("harness", "the repository has no commit checked out")
    trees = tuple(git.tree(head, path) for path in HARNESS_PATHS)
    return Harness(head, trees, book_sha256(repo, book))


def harness_change(git: Repo, repo: Path, book: str, before: Harness) -> str | None:
    """Describe how the harness or the book changed since ``before``, or None if they didn't.

    A command checks before each run, since a run starts the runner afresh
    from the working tree, and the ledger records one harness commit.
    """
    try:
        now = harness_state(git, repo, book)
    except Refused as e:
        return e.message
    if not now.same_code(before):
        return f"the harness ({HARNESS_NAMES}) or the book changed"
    return None
