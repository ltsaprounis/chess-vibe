"""Fixtures shared by the lab's tests.

Guards that apply to every test:

- Each test runs from an empty working directory under ``tmp_path``, outside
  any git repository, without the environment variables that point git at a
  repository (a git hook sets ``GIT_DIR``, for one). The lab always takes the
  repository's path explicitly, so code that falls back to the working
  directory meets no repository, and neither the lab nor a test helper can
  write refs or commits into the one these tests live in. Temporary files go
  under ``tmp_path`` too, so no cleanup can delete outside it.
- In unit tests, no process can start, and ``os.kill`` and ``os.killpg``
  refuse any process but pytest's own. Unit tests drive fake processes, so a
  real one there could only come from a bug or a mutant, and a real signal
  would land on whatever process held that pid. Integration tests start real
  processes, signal only their own children, and keep the real calls.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from lab.git import REPOSITORY_VARIABLES


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Run the test from an empty directory that isn't inside a git repository.

    Temporary files go under ``tmp_path`` too, in this process and in every
    process it starts, so code that cleans up a temporary directory can only
    ever delete inside the test's own.
    """
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    # Git would otherwise look for a repository in every parent directory, or
    # use the one these variables name.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    for name in REPOSITORY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("TMPDIR", str(temp))
    yield cwd


def _guard(real: Callable[[int, int], None], name: str) -> Callable[[int, int], None]:
    """Wrap a signalling call so it can only reach pytest's own process.

    ``os.kill(0, ...)`` would reach pytest's whole process group, and a process
    group is never pytest alone, so ``killpg`` is refused outright.
    """

    def guarded(pid: int, sig: int) -> None:
        if name != "kill" or pid != os.getpid():
            raise AssertionError(f"a unit test called os.{name}({pid}, {sig})")
        real(pid, sig)

    return guarded


def _no_process(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("a unit test started a process: only integration tests may")


@pytest.fixture(autouse=True)
def no_real_processes(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail a unit test that starts a process, or signals any but pytest itself."""
    if "integration" in request.keywords:
        return
    monkeypatch.setattr(subprocess.Popen, "__init__", _no_process)
    monkeypatch.setattr(os, "kill", _guard(os.kill, "kill"))
    monkeypatch.setattr(os, "killpg", _guard(os.killpg, "killpg"))
