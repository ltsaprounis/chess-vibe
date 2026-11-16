"""Integration tests for step 4: the candidate's own checks, in a worktree at the candidate."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest
from lab.config import CorrectnessSettings
from lab.correctness import CorrectnessResult, check_correctness
from lab.git import Git

from tests.repos import commit_files, git, init_repo

pytestmark = pytest.mark.integration

PYTHON = sys.executable


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    root = init_repo(tmp_path / "repo")
    commit_files(root, {"engines/lab-engine/engine.py": "v1\n", "shared/lib.py": "x\n"}, "base")
    candidate = commit_files(root, {"engines/lab-engine/engine.py": "v2\n"}, "candidate")
    # The base is checked out, so a check that read the working tree, not
    # the candidate, would see v1.
    git(root, "checkout", "-q", "HEAD~1")
    return root, candidate


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """Where the checks make their worktree, beside a file that must survive them."""
    (tmp_path / "keep.txt").write_text("keep\n")
    path = tmp_path / "scratch"
    path.mkdir()
    return path


def _check(
    repo: tuple[Path, str],
    scratch: Path,
    commands: list[list[str]],
    *,
    minutes: float = 1.0,
    stop: asyncio.Event | None = None,
    stop_after_s: float | None = None,
    candidate: str | None = None,
) -> CorrectnessResult:
    root, default_candidate = repo
    settings = CorrectnessSettings(commands=commands, timeout_minutes=minutes)

    async def go() -> CorrectnessResult:
        event = stop or asyncio.Event()
        if stop_after_s is not None:
            asyncio.get_running_loop().call_later(stop_after_s, event.set)
        return await check_correctness(
            Git(root), candidate or default_candidate, settings, event, scratch=scratch, grace_s=2.0
        )

    return asyncio.run(go())


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_until_gone(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_runs_in_the_engine_directory_at_the_candidate(
    repo: tuple[Path, str], scratch: Path
) -> None:
    probe = "import pathlib, sys; sys.exit(pathlib.Path('engine.py').read_text() != 'v2\\n')"

    result = _check(repo, scratch, [[PYTHON, "-c", probe], [PYTHON, "-c", "pass"]])

    assert result.passed
    assert not result.stopped
    assert result.message.startswith("2 commands passed in ")
    assert result.duration_s > 0


def test_a_failing_command_fails_the_check(repo: tuple[Path, str], scratch: Path) -> None:
    # The output is computed, so the command's own text, which the message
    # quotes, can't contain it.
    command = [
        PYTHON,
        "-c",
        "print('first line'); print('boom:', 1 + 2, 'tests failed'); raise SystemExit(3)",
    ]

    result = _check(repo, scratch, [command])

    assert not result.passed
    assert not result.stopped
    assert result.message.endswith("` exited 3: boom: 3 tests failed")


def test_stops_at_the_first_failure(repo: tuple[Path, str], scratch: Path, tmp_path: Path) -> None:
    marker = tmp_path / "second-ran"

    result = _check(
        repo,
        scratch,
        [[PYTHON, "-c", "raise SystemExit(1)"], [PYTHON, "-c", f"open({str(marker)!r}, 'w')"]],
    )

    assert not result.passed
    assert not marker.exists()


def test_a_command_that_cannot_start(repo: tuple[Path, str], scratch: Path) -> None:
    result = _check(repo, scratch, [["no-such-command-for-the-lab"]])

    assert not result.passed
    assert "couldn't start" in result.message


def test_the_time_limit_ends_the_command_and_its_children(
    repo: tuple[Path, str], scratch: Path, tmp_path: Path
) -> None:
    pids = tmp_path / "pids"
    script = (
        "import os, subprocess, sys, time\n"
        f"child = subprocess.Popen([{PYTHON!r}, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{child.pid}}')\n"
        "time.sleep(60)\n"
    )

    started = time.monotonic()

    result = _check(repo, scratch, [[PYTHON, "-c", script]], minutes=0.03)

    assert time.monotonic() - started < 10
    assert not result.passed
    assert not result.stopped
    assert "1.8-second limit" in result.message
    command_pid, child_pid = (int(pid) for pid in pids.read_text().split())
    assert _wait_until_gone(command_pid)
    assert _wait_until_gone(child_pid)


def test_the_time_limit_sends_sigterm_first(
    repo: tuple[Path, str], scratch: Path, tmp_path: Path
) -> None:
    marker = tmp_path / "terminated"
    script = (
        "import signal, sys, time\n"
        "def stop(signum, frame):\n"
        f"    open({str(marker)!r}, 'w').write('SIGTERM')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "time.sleep(60)\n"
    )

    result = _check(repo, scratch, [[PYTHON, "-c", script]], minutes=0.02)

    assert not result.passed
    assert marker.read_text() == "SIGTERM"


@pytest.mark.parametrize("name", ["VIRTUAL_ENV", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"])
def test_the_checks_do_not_inherit_the_labs_environment(
    repo: tuple[Path, str], scratch: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "/somewhere/else")
    probe = f"import os, sys; sys.exit({name!r} in os.environ)"

    result = _check(repo, scratch, [[PYTHON, "-c", probe]])

    assert result.passed


def test_a_stop_before_the_checks_runs_nothing(
    repo: tuple[Path, str], scratch: Path, tmp_path: Path
) -> None:
    stop = asyncio.Event()
    stop.set()
    marker = tmp_path / "ran"

    result = _check(repo, scratch, [[PYTHON, "-c", f"open({str(marker)!r}, 'w')"]], stop=stop)

    assert (result.passed, result.stopped) == (False, True)
    assert result.message == "stopped by a signal"
    assert not marker.exists()


def test_a_command_that_ignores_sigterm_is_killed(
    repo: tuple[Path, str], scratch: Path, tmp_path: Path
) -> None:
    pid_file = tmp_path / "pid"
    script = (
        "import os, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )

    result = _check(repo, scratch, [[PYTHON, "-c", script]], minutes=0.02)

    assert not result.passed
    assert _wait_until_gone(int(pid_file.read_text()))


def test_a_stop_request_ends_the_check(repo: tuple[Path, str], scratch: Path) -> None:
    started = time.monotonic()

    result = _check(
        repo, scratch, [[PYTHON, "-c", "import time; time.sleep(60)"]], stop_after_s=0.5
    )

    assert not result.passed
    assert result.stopped
    assert "stopped" in result.message
    assert time.monotonic() - started < 20


def test_leaves_nothing_behind(repo: tuple[Path, str], scratch: Path, tmp_path: Path) -> None:
    root, _ = repo

    _check(repo, scratch, [[PYTHON, "-c", "raise SystemExit(1)"]])

    assert list(scratch.iterdir()) == []
    assert (tmp_path / "keep.txt").read_text() == "keep\n"
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_files_the_checks_leave_are_removed_with_the_worktree(
    repo: tuple[Path, str], scratch: Path
) -> None:
    root, _ = repo
    leave = "import pathlib; pathlib.Path('.venv').mkdir(); pathlib.Path('.venv/x').write_text('x')"

    result = _check(repo, scratch, [[PYTHON, "-c", leave]])

    assert result.passed
    assert list(scratch.iterdir()) == []
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_a_candidate_that_cannot_be_checked_out(repo: tuple[Path, str], scratch: Path) -> None:
    result = _check(repo, scratch, [[PYTHON, "-c", "pass"]], candidate="0" * 40)

    assert not result.passed
    assert "couldn't check out" in result.message
    assert list(scratch.iterdir()) == []
