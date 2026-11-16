"""Integration tests for the lab's git layer, against throwaway repositories."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from lab.git import Git, GitError, RefUpdate

from tests.repos import commit_files, git, init_repo

pytestmark = pytest.mark.integration


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = init_repo(tmp_path / "repo")
    commit_files(root, {"engines/lab-engine/engine.py": "v1\n", "shared/lib.py": "x\n"}, "base")
    return root


class TestLookups:
    def test_commit_resolves_names_to_full_shas(self, repo: Path) -> None:
        head = git(repo, "rev-parse", "HEAD").strip()

        lab = Git(repo)

        assert lab.commit("HEAD") == head
        assert lab.commit("main") == head
        assert lab.commit(head[:10]) == head

    def test_commit_is_none_for_anything_else(self, repo: Path) -> None:
        tree = git(repo, "rev-parse", "HEAD^{tree}").strip()

        lab = Git(repo)

        assert lab.commit("no-such-branch") is None
        assert lab.commit(tree) is None
        assert lab.commit("--all") is None

    def test_ref(self, repo: Path) -> None:
        lab = Git(repo)

        assert lab.ref("refs/heads/lab/main") is None
        git(repo, "branch", "lab/main")
        assert lab.ref("refs/heads/lab/main") == git(repo, "rev-parse", "HEAD").strip()

    def test_parents(self, repo: Path) -> None:
        root = git(repo, "rev-parse", "HEAD").strip()
        child = commit_files(repo, {"a.txt": "a\n"})
        git(repo, "checkout", "-q", "-b", "side", root)
        side = commit_files(repo, {"b.txt": "b\n"})
        git(repo, "merge", "-q", "--no-edit", "main")
        merge = git(repo, "rev-parse", "HEAD").strip()

        lab = Git(repo)

        assert lab.parents(root) == []
        assert lab.parents(child) == [root]
        assert lab.parents(merge) == [side, child]

    def test_head_and_toplevel(self, repo: Path) -> None:
        lab = Git(repo)

        assert lab.head() == git(repo, "rev-parse", "HEAD").strip()
        assert lab.toplevel() == repo.resolve()

    def test_common_dir_is_shared_by_worktrees(self, repo: Path, tmp_path: Path) -> None:
        linked = tmp_path / "linked"
        git(repo, "worktree", "add", "-q", "--detach", str(linked), "HEAD")

        assert Git(repo).common_dir() == (repo / ".git").resolve()
        assert Git(linked).common_dir() == (repo / ".git").resolve()

    def test_can_commit_with_an_identity(self, repo: Path) -> None:
        assert Git(repo).can_commit()

    def test_cannot_commit_without_an_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        anonymous = tmp_path / "anonymous"
        anonymous.mkdir()
        git(anonymous, "init", "-q")
        git(anonymous, "config", "user.useConfigOnly", "true")
        home = tmp_path / "home"
        home.mkdir()
        # Hide every config file but the repository's own, and any identity
        # in the environment.
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for name in (
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
            "EMAIL",
        ):
            monkeypatch.delenv(name, raising=False)

        assert not Git(anonymous).can_commit()


class TestTreesAndPaths:
    def test_changed_paths_lists_both_sides_of_a_rename(self, repo: Path) -> None:
        base = git(repo, "rev-parse", "HEAD").strip()
        git(repo, "mv", "shared/lib.py", "engines/lab-engine/lib.py")
        candidate = commit_files(repo, {"engines/lab-engine/engine.py": "v2\n"})

        paths = Git(repo).changed_paths(base, candidate)

        assert sorted(paths) == [
            "engines/lab-engine/engine.py",
            "engines/lab-engine/lib.py",
            "shared/lib.py",
        ]

    def test_changed_paths_lists_a_mode_change_and_a_deletion(self, repo: Path) -> None:
        base = git(repo, "rev-parse", "HEAD").strip()
        (repo / "shared/lib.py").chmod(0o755)
        git(repo, "add", "shared/lib.py")
        candidate = commit_files(repo, {"engines/lab-engine/engine.py": None})

        assert sorted(Git(repo).changed_paths(base, candidate)) == [
            "engines/lab-engine/engine.py",
            "shared/lib.py",
        ]

    def test_changed_paths_of_an_empty_commit(self, repo: Path) -> None:
        base = git(repo, "rev-parse", "HEAD").strip()
        candidate = commit_files(repo, {})

        assert Git(repo).changed_paths(base, candidate) == []

    def test_tree_is_content_addressed(self, repo: Path) -> None:
        first = git(repo, "rev-parse", "HEAD").strip()
        second = commit_files(repo, {"shared/lib.py": "y\n"})
        third = commit_files(repo, {"engines/lab-engine/engine.py": "v2\n"})

        lab = Git(repo)

        assert lab.tree(first, "engines/lab-engine") == lab.tree(second, "engines/lab-engine")
        assert lab.tree(third, "engines/lab-engine") != lab.tree(first, "engines/lab-engine")
        assert (
            lab.tree(first, "engines/lab-engine")
            == git(repo, "rev-parse", f"{first}:engines/lab-engine").strip()
        )

    def test_tree_is_none_for_a_file_or_a_missing_path(self, repo: Path) -> None:
        lab = Git(repo)

        assert lab.tree("HEAD", "shared/lib.py") is None
        assert lab.tree("HEAD", "engines/nothing") is None

    def test_read_file(self, repo: Path) -> None:
        lab = Git(repo)

        assert lab.read_file("HEAD", "shared/lib.py") == "x\n"
        assert lab.read_file("HEAD", "lab/ledger.jsonl") is None

    def test_read_file_refuses_a_directory(self, repo: Path) -> None:
        with pytest.raises(GitError, match="isn't a file"):
            Git(repo).read_file("HEAD", "shared")


class TestDirty:
    def test_a_clean_tree(self, repo: Path) -> None:
        assert Git(repo).dirty(["shared", "sprt-runner"]) == []

    def test_lists_changes_under_the_paths_only(self, repo: Path) -> None:
        (repo / "shared/lib.py").write_text("edited\n")
        (repo / "shared/new.py").write_text("new\n")
        (repo / "engines/lab-engine/engine.py").write_text("edited\n")
        (repo / ".gitignore").write_text("*.log\n")
        (repo / "shared/run.log").write_text("ignored\n")

        assert sorted(Git(repo).dirty(["shared", "sprt-runner"])) == [
            "shared/lib.py",
            "shared/new.py",
        ]

    def test_a_staged_rename_counts(self, repo: Path) -> None:
        git(repo, "mv", "shared/lib.py", "shared/renamed.py")

        assert sorted(Git(repo).dirty(["shared"])) == ["shared/lib.py", "shared/renamed.py"]


class TestLeavesTheIndexAlone:
    def test_status_does_not_rewrite_the_index(self, repo: Path) -> None:
        index = repo / ".git" / "index"
        before = index.read_bytes()
        # A file touched but not changed: git status would refresh its stat
        # data in the index, and write the index, if it took the lock.
        lib = repo / "shared" / "lib.py"
        stat = lib.stat()
        os.utime(lib, (stat.st_atime + 100, stat.st_mtime + 100))

        assert Git(repo).dirty(["shared"]) == []

        assert index.read_bytes() == before


class TestWorktrees:
    def test_checked_out_branches(self, repo: Path, tmp_path: Path) -> None:
        git(repo, "branch", "lab/main")
        git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), "lab/main")
        git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "detached"), "HEAD")

        assert Git(repo).checked_out_branches() == {"refs/heads/main", "refs/heads/lab/main"}

    def test_add_and_remove_a_detached_worktree(self, repo: Path, tmp_path: Path) -> None:
        base = git(repo, "rev-parse", "HEAD").strip()
        commit_files(repo, {"engines/lab-engine/engine.py": "v2\n"})
        path = tmp_path / "check" / "tree"
        lab = Git(repo)

        lab.add_worktree(path, base)

        assert (path / "engines/lab-engine/engine.py").read_text() == "v1\n"
        assert git(path, "rev-parse", "HEAD").strip() == base
        assert git(path, "rev-parse", "--abbrev-ref", "HEAD").strip() == "HEAD"  # detached

        lab.remove_worktree(path)

        assert not path.exists()
        assert str(path) not in git(repo, "worktree", "list", "--porcelain")


class TestWriting:
    def test_commit_file_adds_a_commit_without_touching_the_checkout(self, repo: Path) -> None:
        base = git(repo, "rev-parse", "HEAD").strip()
        (repo / "staged.txt").write_text("staged\n")
        git(repo, "add", "staged.txt")
        status_before = git(repo, "status", "--porcelain")

        commit = Git(repo).commit_file(base, "lab/ledger.jsonl", '{"id": 1}\n', "lab: record 1")

        assert git(repo, "rev-parse", f"{commit}^").strip() == base
        assert git(repo, "show", f"{commit}:lab/ledger.jsonl") == '{"id": 1}\n'
        assert git(repo, "show", f"{commit}:shared/lib.py") == "x\n"
        assert git(repo, "log", "-1", "--format=%B", commit).strip() == "lab: record 1"
        assert git(repo, "diff", "--name-only", base, commit).split() == ["lab/ledger.jsonl"]
        assert git(repo, "rev-parse", "HEAD").strip() == base
        assert git(repo, "status", "--porcelain") == status_before
        assert not (repo / "lab").exists()

    def test_commit_file_never_signs(self, repo: Path) -> None:
        # Signing could wait on a passphrase prompt after hours of games.
        git(repo, "config", "commit.gpgsign", "true")
        git(repo, "config", "gpg.program", "false")

        commit = Git(repo).commit_file("HEAD", "lab/ledger.jsonl", "x\n", "record")

        assert git(repo, "cat-file", "-p", commit).count("gpgsig") == 0

    def test_commit_file_replaces_an_existing_file(self, repo: Path) -> None:
        lab = Git(repo)
        first = lab.commit_file("HEAD", "lab/ledger.jsonl", "a\n", "one")
        second = lab.commit_file(first, "lab/ledger.jsonl", "a\nb\n", "two")

        assert git(repo, "show", f"{second}:lab/ledger.jsonl") == "a\nb\n"
        assert git(repo, "rev-parse", f"{second}^").strip() == first

    def test_update_refs_creates_and_moves_refs_together(self, repo: Path) -> None:
        base = git(repo, "rev-parse", "HEAD").strip()
        git(repo, "branch", "lab/main")
        newer = Git(repo).commit_file(base, "lab/ledger.jsonl", "x\n", "record")

        Git(repo).update_refs(
            [
                RefUpdate("refs/heads/lab/main", newer, base),
                RefUpdate("refs/lab/rejected/1", base, None),
            ],
            "lab: attempt 1",
        )

        assert git(repo, "rev-parse", "refs/heads/lab/main").strip() == newer
        assert git(repo, "rev-parse", "refs/lab/rejected/1").strip() == base

    def test_update_refs_changes_nothing_if_one_update_fails(self, repo: Path) -> None:
        base = git(repo, "rev-parse", "HEAD").strip()
        git(repo, "branch", "lab/main")
        newer = Git(repo).commit_file(base, "lab/ledger.jsonl", "x\n", "record")
        git(repo, "update-ref", "refs/lab/rejected/1", base)

        with pytest.raises(GitError):
            Git(repo).update_refs(
                [
                    RefUpdate("refs/heads/lab/main", newer, base),
                    RefUpdate("refs/lab/rejected/1", newer, None),
                ],
                "lab: attempt 1",
            )

        assert git(repo, "rev-parse", "refs/heads/lab/main").strip() == base
        assert git(repo, "rev-parse", "refs/lab/rejected/1").strip() == base

    def test_update_refs_refuses_a_stale_old_value(self, repo: Path) -> None:
        base = git(repo, "rev-parse", "HEAD").strip()
        moved = commit_files(repo, {"a.txt": "a\n"})
        git(repo, "branch", "lab/main", moved)

        with pytest.raises(GitError):
            Git(repo).update_refs([RefUpdate("refs/heads/lab/main", base, base)], "lab")

        assert git(repo, "rev-parse", "refs/heads/lab/main").strip() == moved


class TestEnvironment:
    def test_ignores_a_repository_named_by_the_environment(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        other = init_repo(tmp_path / "other")
        commit_files(other, {"other.txt": "o\n"})
        monkeypatch.setenv("GIT_DIR", str(other / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(other))
        monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))

        lab = Git(repo)

        assert lab.read_file("HEAD", "shared/lib.py") == "x\n"
        assert lab.toplevel() == repo.resolve()

    def test_a_failure_names_the_command(self, repo: Path) -> None:
        with pytest.raises(GitError, match="git cat-file"):
            Git(repo).run("cat-file", "-p", "no-such-object")
