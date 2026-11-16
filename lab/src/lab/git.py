"""The lab's git layer: every git command the lab runs, against one explicit repository.

Every command runs as ``git -C <repo>``, from that directory, with the
environment variables that would point git at another repository removed.
So the lab only ever reads or writes the repository it was given, never the
one it happens to be run from.

Writes never touch a working tree or the real index. A ledger commit is built
with plumbing, through an index file of its own, and isn't signed, and refs
move together in one ``update-ref`` transaction that applies every update or
none.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Variables that make git use a repository, index or object store other than
# the one ``-C`` names. A git hook, for one, runs with GIT_DIR set.
REPOSITORY_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
)

# How long one git command may take. They all take milliseconds, apart from
# adding a worktree, which checks out every file.
DEFAULT_TIMEOUT_S = 120.0


class GitError(Exception):
    """A git command failed, or its output wasn't what the lab expects."""


def clean_environment(drop: Iterable[str] = ()) -> dict[str, str]:
    """This process's environment, without the variables that point git elsewhere.

    The lab's git commands run with it, and so do the runner, whose worktree
    commands run from the repository root without ``-C``, and step 4's checks.

    Args:
        drop: More variables to leave out.
    """
    dropped = {*REPOSITORY_VARIABLES, *drop}
    return {k: v for k, v in os.environ.items() if k not in dropped}


@dataclass(frozen=True)
class RefUpdate:
    """One ref to write in a transaction.

    Attributes:
        ref: The full ref name, such as ``refs/heads/lab/main``.
        new: The commit it should point at.
        old: The commit it must point at now, or None if it must not exist.
    """

    ref: str
    new: str
    old: str | None


class Repo(Protocol):
    """The git operations the lab's commands use: :class:`Git`, or a fake in unit tests."""

    def commit(self, rev: str) -> str | None:
        """The full SHA of the commit ``rev`` names, or None."""
        ...

    def ref(self, name: str) -> str | None:
        """What a ref points at, or None."""
        ...

    def parents(self, commit: str) -> list[str]:
        """A commit's parents."""
        ...

    def head(self) -> str | None:
        """The commit checked out."""
        ...

    def can_commit(self) -> bool:
        """Whether git has an identity to commit with."""
        ...

    def changed_paths(self, old: str, new: str) -> list[str]:
        """Every path that differs between two commits."""
        ...

    def tree(self, commit: str, path: str) -> str | None:
        """A directory's tree object at a commit, or None."""
        ...

    def read_file(self, commit: str, path: str) -> str | None:
        """A file's text at a commit, or None."""
        ...

    def dirty(self, paths: Sequence[str]) -> list[str]:
        """Changed or untracked files under the given paths."""
        ...

    def checked_out_branches(self) -> set[str]:
        """Every branch checked out in a worktree."""
        ...

    def commit_file(self, parent: str, path: str, content: str, message: str) -> str:
        """A new commit changing one file."""
        ...

    def update_refs(self, updates: Sequence[RefUpdate], message: str) -> None:
        """Write refs in one transaction."""
        ...


def parse_status(output: str) -> list[str]:
    """Read the paths from ``git status --porcelain=v1 -z``.

    A rename or a copy names two paths, and both are returned.
    """
    entries = output.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        paths.append(path)
        if status[0] in "RC" and index < len(entries):
            paths.append(entries[index])
            index += 1
    return paths


def parse_worktree_branches(output: str) -> set[str]:
    """Read the branches checked out in any worktree from ``git worktree list --porcelain``."""
    prefix = "branch "
    return {line[len(prefix) :] for line in output.splitlines() if line.startswith(prefix)}


def parse_ls_tree(output: str) -> tuple[str, str] | None:
    """Read the type and object of the one entry ``git ls-tree -z <commit> -- <path>`` gives."""
    entry = output.split("\0", 1)[0]
    if not entry:
        return None
    info = entry.split("\t", 1)[0]
    _, kind, obj = info.split(" ")
    return kind, obj


class Git:
    """Runs git in one repository."""

    def __init__(self, repo: Path, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        """Set up git for a repository.

        Args:
            repo: The repository's top-level directory.
            timeout_s: Time allowed for each command.
        """
        self.repo = repo
        self._timeout_s = timeout_s

    def _result(
        self, args: Sequence[str], stdin: str | None, env: Mapping[str, str] | None
    ) -> subprocess.CompletedProcess[str]:
        environment = clean_environment()
        if env is not None:
            environment.update(env)
        try:
            return subprocess.run(
                ["git", "-C", str(self.repo), *args],
                cwd=self.repo,
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self._timeout_s,
                env=environment,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            # A hung or missing git is a failure the lab reports, like any
            # other git error, rather than a crash after hours of games.
            raise GitError(f"git {' '.join(args)} failed: {e}") from e

    def run(
        self, *args: str, stdin: str | None = None, env: Mapping[str, str] | None = None
    ) -> str:
        """Run a git command and return its stdout.

        Args:
            *args: The command and its arguments, after ``git -C <repo>``.
            stdin: Text to write to its stdin.
            env: Environment variables to add.

        Returns:
            Its stdout.

        Raises:
            GitError: If it exits other than 0. The message names the command
                and quotes its stderr.
        """
        result = self._result(args, stdin, env)
        if result.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
            )
        return result.stdout

    def _lookup(self, *args: str) -> str | None:
        """Run a ``--verify --quiet`` lookup: its output, or None if there's no such object.

        Such a lookup exits 1 when the name resolves to nothing of the kind
        asked for, sometimes still explaining on stderr (a tree asked for as
        a commit). A real error, such as a missing repository, exits 128.
        """
        result = self._result(args, None, None)
        if result.returncode == 1:
            return None
        if result.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def commit(self, rev: str) -> str | None:
        """The full SHA of the commit ``rev`` names, or None if it names no commit."""
        return self._lookup(
            "rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}"
        )

    def ref(self, name: str) -> str | None:
        """What a ref points at, or None if it doesn't exist."""
        return self._lookup("rev-parse", "--verify", "--quiet", "--end-of-options", name)

    def parents(self, commit: str) -> list[str]:
        """A commit's parents, first parent first."""
        return self.run("rev-list", "--parents", "-n", "1", commit, "--").split()[1:]

    def head(self) -> str | None:
        """The commit checked out, or None on an unborn branch."""
        return self._lookup("rev-parse", "--verify", "--quiet", "HEAD")

    def toplevel(self) -> Path:
        """The top-level directory of the repository or worktree."""
        return Path(self.run("rev-parse", "--show-toplevel").strip()).resolve()

    def common_dir(self) -> Path:
        """The git directory every worktree of the repository shares."""
        output = self.run("rev-parse", "--path-format=absolute", "--git-common-dir")
        return Path(output.strip()).resolve()

    def can_commit(self) -> bool:
        """Whether git has an author and committer identity to commit with."""
        return all(
            self._result(["var", name], None, None).returncode == 0
            for name in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT")
        )

    def changed_paths(self, old: str, new: str) -> list[str]:
        """Every path that differs between two commits.

        Renames aren't detected, so a file moved between directories names
        both its old and its new path.
        """
        output = self.run("diff", "--name-only", "--no-renames", "-z", old, new, "--")
        return [path for path in output.split("\0") if path]

    def _entry(self, commit: str, path: str) -> tuple[str, str] | None:
        return parse_ls_tree(self.run("ls-tree", "-z", commit, "--", path))

    def tree(self, commit: str, path: str) -> str | None:
        """The tree object of a directory at a commit, or None if it isn't a directory there.

        The same contents give the same tree, whichever commit they're in.
        """
        entry = self._entry(commit, path)
        if entry is None or entry[0] != "tree":
            return None
        return entry[1]

    def read_file(self, commit: str, path: str) -> str | None:
        """A file's text at a commit, or None if there's nothing at that path.

        Raises:
            GitError: If the path is something other than a file.
        """
        entry = self._entry(commit, path)
        if entry is None:
            return None
        kind, obj = entry
        if kind != "blob":
            raise GitError(f"{path} at {commit} isn't a file, it's a {kind}")
        return self.run("cat-file", "blob", obj)

    def dirty(self, paths: Sequence[str]) -> list[str]:
        """Changed or untracked files under the given paths of the working tree.

        Ignored files don't count.
        """
        # Without optional locks, status doesn't refresh and rewrite the
        # index, which a concurrent git command of the user's might hold.
        output = self.run(
            "--no-optional-locks",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--",
            *paths,
        )
        return parse_status(output)

    def checked_out_branches(self) -> set[str]:
        """Every branch checked out in any of the repository's worktrees, as full ref names."""
        return parse_worktree_branches(self.run("worktree", "list", "--porcelain"))

    def add_worktree(self, path: Path, commit: str) -> None:
        """Check out a commit, detached, in a new worktree at ``path``."""
        self.run("worktree", "add", "--quiet", "--detach", str(path), commit)

    def remove_worktree(self, path: Path) -> None:
        """Remove a worktree the lab added, with whatever is in it."""
        self.run("worktree", "remove", "--force", str(path))

    def commit_file(self, parent: str, path: str, content: str, message: str) -> str:
        """Make a commit that changes one file, without touching any checkout.

        Args:
            parent: The commit to build on. The new commit has its tree, with
                ``path`` added or replaced.
            path: The file's path from the repository root.
            content: The file's new text.
            message: The commit message.

        Returns:
            The new commit. No ref points at it yet.
        """
        with tempfile.TemporaryDirectory(prefix="lab-index-") as scratch:
            env = {"GIT_INDEX_FILE": str(Path(scratch) / "index")}
            self.run("read-tree", parent, env=env)
            blob = self.run("hash-object", "-w", "--stdin", stdin=content).strip()
            self.run("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
            tree = self.run("write-tree", env=env).strip()
        # Never signed: a signing prompt would hang an unattended run. git 2.54's
        # commit-tree doesn't sign by config anyway, but says so explicitly.
        return self.run("commit-tree", "--no-gpg-sign", tree, "-p", parent, stdin=message).strip()

    def update_refs(self, updates: Sequence[RefUpdate], message: str) -> None:
        """Write refs in one transaction: every update happens, or none does.

        Raises:
            GitError: If any ref doesn't hold its expected old value, which
                includes a ref to be created that already exists.
        """
        lines = [
            f"create {u.ref} {u.new}" if u.old is None else f"update {u.ref} {u.new} {u.old}"
            for u in updates
        ]
        self.run(
            "update-ref", "-m", message, "--stdin", stdin="".join(f"{line}\n" for line in lines)
        )
