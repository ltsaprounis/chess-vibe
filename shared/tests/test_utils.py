"""Tests for shared utility functions."""

import functools
from pathlib import Path
from unittest.mock import patch

import pytest
from shared.repo_root import RepoRootNotFoundError, get_repo_root


class TestGetRepoRoot:
    """Tests for get_repo_root."""

    def test_returns_path_containing_git(self) -> None:
        get_repo_root.cache_clear()
        root = get_repo_root()
        assert (root / ".git").exists()

    def test_returns_absolute_path(self) -> None:
        get_repo_root.cache_clear()
        root = get_repo_root()
        assert root.is_absolute()

    def test_result_is_cached(self) -> None:
        get_repo_root.cache_clear()
        first = get_repo_root()
        second = get_repo_root()
        assert first is second

    def test_raises_when_no_git_directory(self, tmp_path: Path) -> None:
        """Test that RepoRootNotFoundError is raised when no .git exists."""

        # Build a standalone function that starts from tmp_path (no .git ancestor)
        @functools.cache
        def _get_repo_root_from(start: Path) -> Path:
            current = start
            while True:
                if (current / ".git").exists():
                    return current
                parent = current.parent
                if parent == current:
                    raise RepoRootNotFoundError("no .git found")
                current = parent

        with pytest.raises(RepoRootNotFoundError):
            _get_repo_root_from(tmp_path)

    def test_raises_with_patched_file(self, tmp_path: Path) -> None:
        """Test that the real function raises when __file__ points outside a repo."""
        get_repo_root.cache_clear()
        fake_file = tmp_path / "repo_root.py"
        fake_file.touch()
        with (
            patch("shared.repo_root.__file__", str(fake_file)),
            pytest.raises(RepoRootNotFoundError),
        ):
            get_repo_root()
        get_repo_root.cache_clear()


class TestRepoRootNotFoundError:
    """Tests for RepoRootNotFoundError."""

    def test_error_is_importable(self) -> None:
        assert issubclass(RepoRootNotFoundError, Exception)

    def test_error_importable_from_utils(self) -> None:
        from shared.utils import RepoRootNotFoundError as ErrorFromUtils

        assert ErrorFromUtils is RepoRootNotFoundError

    def test_error_importable_from_package(self) -> None:
        from shared import RepoRootNotFoundError as ErrorFromPackage

        assert ErrorFromPackage is RepoRootNotFoundError
