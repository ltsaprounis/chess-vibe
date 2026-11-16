"""End-to-end tests: the real ``lab`` command, real git, and the real SPRT runner.

Each test builds a throwaway repository under ``tmp_path`` holding a scripted
UCI engine (``uci_engine.py``), registered as both ``lab-engine`` and
``random-engine``, and a one-position book. Its
``sprt-runner/.venv/bin/python`` is a shim that runs this checkout's own
runner, so the lab drives the real runner CLI and reads its real JSON lines.

A commit makes the engine stronger or weaker by changing its ``mode`` file:
``slow`` loses on time at its first move, ``fast`` never does. So SPRTs end
in a few seconds with a known result.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import select
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chess
import pytest
from lab.cli import app
from typer.testing import CliRunner

from tests.helpers import toml_dumps
from tests.repos import commit_files, git, init_repo

pytestmark = pytest.mark.integration

CHECKOUT = Path(__file__).resolve().parents[2]
RUNNER_PYTHON = CHECKOUT / "sprt-runner" / ".venv" / "bin" / "python"
ENGINE_SOURCE = Path(__file__).with_name("uci_engine.py")
ENGINES = ("engines/lab-engine", "engines/random-engine")
SHORT_CLOCK = "wtime=300,btime=300,winc=0,binc=0"

CONFIG: dict[str, Any] = {
    "concurrency": 2,
    "book": "books/test.epd",
    "correctness": {"commands": [[sys.executable, "-c", "pass"]], "timeout_minutes": 2},
    "fast": {
        "tc": SHORT_CLOCK,
        "elo0": 0.0,
        "elo1": 400.0,
        "alpha": 0.05,
        "beta": 0.05,
        "max_games": 60,
        "max_time_losses": 1000,
        "timeout_minutes": 4,
    },
    "confirm": {
        "tc": SHORT_CLOCK,
        "elo0": 0.0,
        "elo1": 400.0,
        "alpha": 0.05,
        "beta": 0.05,
        "max_games": 60,
        "max_time_losses": 1000,
        "timeout_minutes": 4,
    },
    "anchor": {"tc": SHORT_CLOCK, "games": 6, "max_time_losses": 1000, "timeout_minutes": 4},
}


@dataclass
class LabRepo:
    """A throwaway repository the lab can run in."""

    root: Path
    base: str
    config: Path

    def lab_main(self) -> str:
        return git(self.root, "rev-parse", "refs/heads/lab/main").strip()

    def commit_mode(self, mode: str, parent: str, engine: str = "engines/lab-engine") -> str:
        """Make a commit on ``parent`` that sets an engine's mode, without moving any branch."""
        git(self.root, "checkout", "-q", "--detach", parent)
        commit = commit_files(self.root, {f"{engine}/mode": f"{mode}\n"}, f"{engine} {mode}")
        git(self.root, "checkout", "-q", "main")
        return commit

    def move_lab_main(self, commit: str) -> None:
        git(self.root, "update-ref", "refs/heads/lab/main", commit)

    def ledger(self) -> list[dict[str, Any]]:
        text = git(self.root, "show", "refs/heads/lab/main:lab/ledger.jsonl")
        return [json.loads(line) for line in text.splitlines()]


@pytest.fixture
def lab_repo(tmp_path: Path) -> LabRepo:
    if not RUNNER_PYTHON.exists():
        pytest.skip("the runner's venv isn't built: run make setup")
    root = init_repo(tmp_path / "repo")
    script = f"#!{sys.executable}\n{ENGINE_SOURCE.read_text()}"
    registry = [
        {"id": "lab-engine", "name": "Lab", "dir": ENGINES[0], "build": None, "run": "engine.py"},
        {"id": "random-engine", "name": "R", "dir": ENGINES[1], "build": None, "run": "engine.py"},
    ]
    files = {
        ".gitignore": ".venv/\n.worktrees/\n",
        "engines.json": json.dumps(registry, indent=2) + "\n",
        "books/test.epd": f"{chess.STARTING_FEN}\n",
        "sprt-runner/README.md": "the runner\n",
        "shared/README.md": "the shared library\n",
    }
    for engine in ENGINES:
        files[f"{engine}/engine.py"] = script
        files[f"{engine}/mode"] = "slow\n"
    executable = frozenset(f"{engine}/engine.py" for engine in ENGINES)
    base = commit_files(root, files, "baseline", executable=executable)
    shim = root / "sprt-runner" / ".venv" / "bin" / "python"
    shim.parent.mkdir(parents=True)
    shim.write_text(f'#!/bin/sh\nexec "{RUNNER_PYTHON}" "$@"\n')
    shim.chmod(0o755)
    git(root, "branch", "lab/main", base)
    config = tmp_path / "lab.toml"
    config.write_text(toml_dumps(CONFIG))
    return LabRepo(root, base, config)


def _cli(*argv: str) -> tuple[int, dict[str, Any]]:
    result = CliRunner().invoke(app, list(argv), catch_exceptions=False)
    return result.exit_code, json.loads(result.stdout)


def _attempt(repo: LabRepo, base: str, candidate: str, *config: str) -> tuple[int, dict[str, Any]]:
    return _cli(
        "attempt",
        *("--repo", str(repo.root)),
        *("--config", config[0] if config else str(repo.config)),
        *("--base", base),
        *("--candidate", candidate),
        *("--hypothesis", "Move faster"),
    )


def _assert_clean(repo: LabRepo) -> None:
    """The checkout and its worktrees are as they were: the lab changed only refs."""
    assert git(repo.root, "status", "--porcelain") == ""
    assert git(repo.root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert git(repo.root, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert not any((repo.root / ".worktrees").glob("*"))


class TestAttempt:
    def test_accepts_a_stronger_candidate(self, lab_repo: LabRepo) -> None:
        candidate = lab_repo.commit_mode("fast", lab_repo.base)

        code, printed = _attempt(lab_repo, lab_repo.base, candidate)

        assert code == 0, printed
        assert printed["outcome"] == "accepted"
        (entry,) = lab_repo.ledger()
        assert entry == printed["entry"]
        assert entry["config_sha256"] == hashlib.sha256(lab_repo.config.read_bytes()).hexdigest()
        assert (entry["verdict"], entry["base"], entry["candidate"]) == (
            "accepted",
            lab_repo.base,
            candidate,
        )
        assert (
            entry["engine_tree"]
            == git(lab_repo.root, "rev-parse", f"{candidate}:engines/lab-engine").strip()
        )
        assert entry["harness"] == lab_repo.base
        fast, confirm = entry["stages"]
        assert (fast["result"], confirm["result"]) == ("H1", "H1")
        assert fast["losses"] == 0
        assert isinstance(fast["seed"], int)
        assert fast["terminations"]["timeout"] == fast["games"]
        tip = lab_repo.lab_main()
        assert git(lab_repo.root, "rev-parse", f"{tip}^").strip() == candidate
        assert git(lab_repo.root, "diff", "--name-only", candidate, tip).split() == [
            "lab/ledger.jsonl"
        ]
        _assert_clean(lab_repo)

    def test_rejects_a_weaker_candidate_and_refuses_a_retry(self, lab_repo: LabRepo) -> None:
        strong = lab_repo.commit_mode("fast", lab_repo.base)
        lab_repo.move_lab_main(strong)
        weak = lab_repo.commit_mode("slow", strong)

        code, printed = _attempt(lab_repo, strong, weak)

        assert code == 4, printed
        assert printed["outcome"] == "rejected"
        assert printed["entry"]["reasons"] == ["h0"]
        assert git(lab_repo.root, "rev-parse", "refs/lab/rejected/1").strip() == weak
        tip = lab_repo.lab_main()
        assert git(lab_repo.root, "rev-parse", f"{tip}^").strip() == strong
        _assert_clean(lab_repo)

        again = lab_repo.commit_mode("slow", tip)
        code, printed = _attempt(lab_repo, tip, again)

        assert (code, printed["outcome"], printed["check"]) == (3, "refused", "retry")
        assert lab_repo.lab_main() == tip

    def test_refuses_a_rename_out_of_the_harness(self, lab_repo: LabRepo) -> None:
        git(lab_repo.root, "mv", "shared/README.md", "engines/lab-engine/README.md")
        git(lab_repo.root, "commit", "-q", "-m", "move")
        candidate = git(lab_repo.root, "rev-parse", "HEAD").strip()
        git(lab_repo.root, "reset", "-q", "--hard", lab_repo.base)

        code, printed = _attempt(lab_repo, lab_repo.base, candidate)

        assert (code, printed["check"]) == (3, "scope")
        assert "shared/README.md" in printed["message"]
        assert lab_repo.lab_main() == lab_repo.base

    def test_failing_checks_refuse_the_attempt(self, lab_repo: LabRepo, tmp_path: Path) -> None:
        failing = dict(CONFIG)
        failing["correctness"] = {
            "commands": [
                [sys.executable, "-c", "print(1 + 2, 'tests failed'); raise SystemExit(1)"]
            ],
            "timeout_minutes": 2,
        }
        config = tmp_path / "failing.toml"
        config.write_text(toml_dumps(failing))
        candidate = lab_repo.commit_mode("fast", lab_repo.base)

        code, printed = _attempt(lab_repo, lab_repo.base, candidate, str(config))

        assert (code, printed["check"]) == (3, "correctness")
        assert printed["message"].endswith("` exited 1: 3 tests failed")
        assert lab_repo.lab_main() == lab_repo.base
        _assert_clean(lab_repo)


class TestAnchorAndReport:
    def test_freezes_a_milestone_then_reports_it(self, lab_repo: LabRepo) -> None:
        strong = lab_repo.commit_mode("fast", lab_repo.base)
        lab_repo.move_lab_main(strong)

        code, printed = _cli(
            "anchor", "--repo", str(lab_repo.root), "--config", str(lab_repo.config)
        )

        assert code == 0, printed
        assert printed["outcome"] == "milestone"
        (entry,) = lab_repo.ledger()
        assert entry["milestone"] == 1
        (match,) = entry["matches"]
        assert match["opponent"] == "random-engine"
        assert match["engine"] == f"random-engine:{lab_repo.base}"
        assert (match["games"], match["wins"]) == (6, 6)
        _assert_clean(lab_repo)

        result = CliRunner().invoke(app, ["report", "--repo", str(lab_repo.root)])

        assert result.exit_code == 0
        assert "Milestones:\n  1  " in result.stdout
        assert "Latest anchor run, 1: lab/main at " in result.stdout


class TestRefusals:
    def test_a_directory_that_is_not_a_repository(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()

        code, printed = _cli("anchor", "--repo", str(plain))

        assert (code, printed["check"]) == (3, "repo")

    def test_a_subdirectory_of_the_repository(self, lab_repo: LabRepo) -> None:
        code, printed = _cli("anchor", "--repo", str(lab_repo.root / "engines"))

        assert (code, printed["check"]) == (3, "repo")
        assert "top-level" in printed["message"]

    def test_a_missing_config(self, lab_repo: LabRepo) -> None:
        code, printed = _cli("anchor", "--repo", str(lab_repo.root))

        assert (code, printed["check"]) == (3, "config")
        assert "lab/lab.toml" in printed["message"]


def _session_members(sid: int) -> list[int]:
    out = subprocess.run(
        ["ps", "-A", "-o", "pid="], capture_output=True, text=True, timeout=10, check=True
    ).stdout
    members: list[int] = []
    for word in out.split():
        with contextlib.suppress(OSError):
            if os.getsid(int(word)) == sid:
                members.append(int(word))
    return members


def _drain(process: subprocess.Popen[str], seconds: float) -> str:
    """Read both pipes to their ends, so a full one can't block the process; return stdout."""
    assert process.stdout is not None and process.stderr is not None
    out: list[str] = []
    open_pipes = [process.stdout, process.stderr]
    deadline = time.monotonic() + seconds
    while open_pipes:
        assert time.monotonic() < deadline, "the lab didn't close its output in time"
        ready, _, _ = select.select(open_pipes, [], [], 1.0)
        for pipe in ready:
            line = pipe.readline()
            if not line:
                open_pipes.remove(pipe)
            elif pipe is process.stdout:
                out.append(line)
    return "".join(out)


def _kill_session(sid: int) -> None:
    for pid in _session_members(sid):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


class TestProcess:
    def test_python_dash_m_lab(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "lab", "--help"], capture_output=True, text=True, timeout=60
        )

        assert result.returncode == 0
        assert "attempt" in result.stdout

    def test_a_crash_is_a_plain_traceback_and_exit_1(self) -> None:
        program = (
            "import sys\n"
            "import lab.cli as cli\n"
            "def broken(repo, config):\n"
            "    raise RuntimeError('the lab broke')\n"
            "cli.open_session = broken\n"
            "sys.argv = ['lab', 'anchor', '--repo', '.']\n"
            "cli.main()\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, timeout=60
        )

        assert result.returncode == 1
        assert result.stdout == ""
        assert "Traceback (most recent call last):" in result.stderr
        assert "RuntimeError: the lab broke" in result.stderr
        assert not any(c in result.stderr for c in "╭╮╰╯│─")

    def test_bad_flags_exit_2(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "lab", "attempt"], capture_output=True, text=True, timeout=60
        )

        assert result.returncode == 2
        assert result.stdout == ""

    def test_sigterm_stops_an_attempt_and_records_it(
        self, lab_repo: LabRepo, tmp_path: Path
    ) -> None:
        slow_games = dict(CONFIG)
        slow_games["fast"] = dict(CONFIG["fast"], tc="wtime=60000,btime=60000,winc=0,binc=0")
        config = tmp_path / "slow-games.toml"
        config.write_text(toml_dumps(slow_games))
        thinking = lab_repo.commit_mode("think:200", lab_repo.base)
        lab_repo.move_lab_main(thinking)
        candidate = lab_repo.commit_mode("think:150", thinking)
        argv = [
            *(sys.executable, "-m", "lab", "attempt", "--repo", str(lab_repo.root)),
            *("--config", str(config), "--base", thinking, "--candidate", candidate),
            *("--hypothesis", "Think less"),
        ]
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            assert process.stderr is not None
            started = ""
            deadline = time.monotonic() + 60
            while "fast SPRT: started" not in started:
                assert time.monotonic() < deadline, f"the SPRT didn't start:\n{started}"
                ready, _, _ = select.select([process.stderr], [], [], 1.0)
                if ready:
                    started += process.stderr.readline()
            time.sleep(1.0)

            process.send_signal(signal.SIGTERM)
            stdout = _drain(process, 120)
            # Wait for the exit without reaping it, so the lab's pid can't be
            # reused while its session is checked and cleaned up.
            exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
            assert exited is not None

            assert exited.si_status == 5, stdout
            printed = json.loads(stdout)
            assert printed["outcome"] == "failed"
            assert printed["entry"]["reasons"] == ["interrupted"]
            (entry,) = lab_repo.ledger()
            assert (entry["verdict"], entry["candidate"]) == ("failed", candidate)
            assert git(lab_repo.root, "rev-parse", "refs/lab/rejected/1").strip() == candidate
            deadline = time.monotonic() + 30
            while [pid for pid in _session_members(process.pid) if pid != process.pid]:
                assert time.monotonic() < deadline, "processes outlived the attempt"
                time.sleep(0.2)
            _assert_clean(lab_repo)
        finally:
            # The lab hasn't been reaped yet, running or not, so its pid can't
            # have been reused: only its own session is killed. Then reap it.
            _kill_session(process.pid)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)
