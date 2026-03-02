"""Utility for locating the repository root directory."""

import functools
from pathlib import Path


class RepoRootNotFoundError(Exception):
    """Raised when the repository root cannot be found."""


@functools.cache
def get_repo_root() -> Path:
    """Locate the repository root by walking up from this module's directory.

    Walks up the directory tree from the module's own location until a `.git`
    directory is found. The result is cached so the filesystem traversal only
    happens once per process.

    Returns:
        The absolute path to the repository root.

    Raises:
        RepoRootNotFoundError: If no `.git` directory is found in any ancestor.
    """
    current = Path(__file__).resolve().parent
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            raise RepoRootNotFoundError(
                "Could not find repository root (.git directory) in any ancestor of "
                f"{Path(__file__).resolve().parent}"
            )
        current = parent
