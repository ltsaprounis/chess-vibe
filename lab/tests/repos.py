"""Throwaway git repositories for the integration tests.

Every repository lives under the test's ``tmp_path``, with its own identity
and no signing, and nothing here ever names the repository these tests live
in.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path


def git(repo: Path, *args: str, stdin: str | None = None) -> str:
    """Run git in a test repository and return its stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def init_repo(root: Path) -> Path:
    """Create an empty repository at ``root`` on branch ``main``."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    for key, value in (
        ("user.name", "Lab Test"),
        ("user.email", "lab-test@invalid"),
        ("commit.gpgsign", "false"),
        ("core.hooksPath", os.devnull),
    ):
        git(root, "config", key, value)
    return root


def commit_files(
    repo: Path,
    files: Mapping[str, str | None],
    message: str = "change",
    *,
    executable: frozenset[str] = frozenset(),
) -> str:
    """Write files in the working tree (None deletes one), commit them, return the commit."""
    for name, content in files.items():
        path = repo / name
        if content is None:
            git(repo, "rm", "-q", "--", name)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if name in executable:
            path.chmod(0o755)
        git(repo, "add", "--", name)
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()
