"""Unit tests for reading git's machine output."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import lab.git
import pytest
from lab.git import Git, GitError, parse_ls_tree, parse_status, parse_worktree_branches


class TestFailuresToRun:
    """A git that hangs or can't start is a GitError, which callers handle, not a crash."""

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(subprocess.TimeoutExpired(["git"], 120), id="timeout"),
            pytest.param(FileNotFoundError(2, "No such file or directory", "git"), id="no-git"),
        ],
    )
    def test_becomes_a_git_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
    ) -> None:
        def fail(*args: Any, **kwargs: Any) -> Any:
            raise failure

        monkeypatch.setattr(lab.git.subprocess, "run", fail)

        with pytest.raises(GitError, match="git commit-tree"):
            Git(tmp_path).run("commit-tree", "abc")


class TestParseStatus:
    def test_every_kind_of_change(self) -> None:
        output = " M shared/lib.py\0?? shared/new.py\0D  shared/old.py\0A  shared/added.py\0"

        assert parse_status(output) == [
            "shared/lib.py",
            "shared/new.py",
            "shared/old.py",
            "shared/added.py",
        ]

    def test_a_rename_lists_both_paths(self) -> None:
        output = "R  shared/renamed.py\0shared/lib.py\0 M shared/x.py\0"

        assert parse_status(output) == ["shared/renamed.py", "shared/lib.py", "shared/x.py"]

    def test_a_copy_lists_both_paths(self) -> None:
        assert parse_status("C  shared/copy.py\0shared/lib.py\0") == [
            "shared/copy.py",
            "shared/lib.py",
        ]

    def test_a_path_with_spaces(self) -> None:
        assert parse_status("?? shared/a b.py\0") == ["shared/a b.py"]

    def test_nothing(self) -> None:
        assert parse_status("") == []


class TestParseWorktreeBranches:
    def test_branches_only(self) -> None:
        output = (
            "worktree /repo\nHEAD 1111\nbranch refs/heads/main\n\n"
            "worktree /wt\nHEAD 2222\ndetached\n\n"
            "worktree /lab\nHEAD 3333\nbranch refs/heads/lab/main\nlocked\n\n"
        )

        assert parse_worktree_branches(output) == {"refs/heads/main", "refs/heads/lab/main"}

    def test_a_bare_repository(self) -> None:
        assert parse_worktree_branches("worktree /repo.git\nbare\n\n") == set()


class TestParseLsTree:
    def test_a_tree(self) -> None:
        assert parse_ls_tree("040000 tree abcd1234\tengines/lab-engine\0") == ("tree", "abcd1234")

    def test_a_blob(self) -> None:
        assert parse_ls_tree("100644 blob ef567890\tlab/ledger.jsonl\0") == ("blob", "ef567890")

    def test_nothing(self) -> None:
        assert parse_ls_tree("") is None
