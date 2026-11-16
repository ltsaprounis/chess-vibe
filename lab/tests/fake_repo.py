"""An in-memory git repository for the unit tests of the lab's commands.

It does what the lab asks of :class:`lab.git.Git`, with commits as
dictionaries of file contents, and refs updated all together or not at all,
as ``update-ref --stdin`` does. Trees are content-addressed: the same files
give the same tree, whichever commit they're in.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from lab.git import GitError, RefUpdate


@dataclass(frozen=True)
class FakeCommit:
    parents: tuple[str, ...]
    files: Mapping[str, str]
    message: str


@dataclass
class FakeRepo:
    """Commits, refs and a working tree's state, all in memory."""

    commits: dict[str, FakeCommit] = field(default_factory=lambda: dict[str, FakeCommit]())
    refs: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    head_commit: str | None = None
    dirty_paths: list[str] = field(default_factory=lambda: list[str]())
    checked_out: set[str] = field(default_factory=lambda: {"refs/heads/main"})
    identity: bool = True
    ref_messages: list[str] = field(default_factory=lambda: list[str]())

    def add_commit(
        self, files: Mapping[str, str | None], parents: Sequence[str] = (), message: str = "c"
    ) -> str:
        """Make a commit: the first parent's files, changed by ``files`` (None deletes)."""
        merged = dict(self.commits[parents[0]].files) if parents else {}
        for path, content in files.items():
            if content is None:
                merged.pop(path, None)
            else:
                merged[path] = content
        digest = hashlib.sha1(
            repr((tuple(parents), sorted(merged.items()), message, len(self.commits))).encode()
        ).hexdigest()
        self.commits[digest] = FakeCommit(tuple(parents), merged, message)
        return digest

    def commit(self, rev: str) -> str | None:
        if rev in self.commits:
            return rev
        for name in (rev, f"refs/heads/{rev}"):
            if name in self.refs:
                return self.refs[name]
        if rev == "HEAD":
            return self.head_commit
        return None

    def ref(self, name: str) -> str | None:
        return self.refs.get(name)

    def parents(self, commit: str) -> list[str]:
        return list(self.commits[commit].parents)

    def head(self) -> str | None:
        return self.head_commit

    def can_commit(self) -> bool:
        return self.identity

    def changed_paths(self, old: str, new: str) -> list[str]:
        before, after = self.commits[old].files, self.commits[new].files
        return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))

    def tree(self, commit: str, path: str) -> str | None:
        prefix = f"{path}/"
        inside = sorted(
            (p, c) for p, c in self.commits[commit].files.items() if p.startswith(prefix)
        )
        if not inside:
            return None
        return hashlib.sha1(repr(inside).encode()).hexdigest()

    def read_file(self, commit: str, path: str) -> str | None:
        return self.commits[commit].files.get(path)

    def dirty(self, paths: Sequence[str]) -> list[str]:
        return [p for p in self.dirty_paths if any(p == x or p.startswith(f"{x}/") for x in paths)]

    def checked_out_branches(self) -> set[str]:
        return set(self.checked_out)

    def commit_file(self, parent: str, path: str, content: str, message: str) -> str:
        return self.add_commit({path: content}, [parent], message)

    def update_refs(self, updates: Sequence[RefUpdate], message: str) -> None:
        for update in updates:
            current = self.refs.get(update.ref)
            if current != update.old:
                raise GitError(f"{update.ref} is {current}, expected {update.old}")
        for update in updates:
            self.refs[update.ref] = update.new
        self.ref_messages.append(message)
