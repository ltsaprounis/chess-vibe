"""Unit tests for ``lab attempt``: its refusals, its verdicts, and what it records."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from lab.attempt import AttemptRequest, run_attempt
from lab.correctness import CorrectnessResult
from lab.ledger import LEDGER_PATH, AttemptEntry, Ledger
from lab.outcome import Outcome
from lab.runs import RunReport, RunRequest

from tests.doubles import BOOK_TEXT, CONFIG_SHA256, World, broken, finished, make_world, stopped
from tests.entries import attempt as attempt_entry
from tests.fake_repo import FakeRepo

LAB_MAIN = "refs/heads/lab/main"
HYPOTHESIS = "Search captures first"


def _attempt(world: World, *, base: str | None = None, candidate: str | None = None) -> Outcome:
    request = AttemptRequest(
        hypothesis=HYPOTHESIS,
        base=world.base if base is None else base,
        candidate=world.candidate if candidate is None else candidate,
    )
    return asyncio.run(run_attempt(world.lab, request, asyncio.Event()))


def _ledger(git: FakeRepo) -> Ledger:
    return Ledger.parse(git.read_file(git.refs[LAB_MAIN], LEDGER_PATH) or "")


def _only_entry(git: FakeRepo) -> AttemptEntry:
    (entry,) = _ledger(git).entries
    assert isinstance(entry, AttemptEntry)
    return entry


def _assert_refused(world: World, outcome: Outcome, check: str, *words: str) -> None:
    assert outcome.kind == "refused"
    assert outcome.check == check
    assert outcome.exit_code == 3
    assert outcome.entry is None
    for word in words:
        assert word in outcome.message
    assert world.git.refs[LAB_MAIN] == world.base
    assert not any(ref.startswith("refs/lab/") for ref in world.git.refs)
    assert world.runner.requests == []


class TestPreconditions:
    def test_no_identity(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        world.git.identity = False

        _assert_refused(world, _attempt(world), "identity", "user.name")

    def test_no_lab_main(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        del world.git.refs[LAB_MAIN]

        outcome = _attempt(world)

        assert outcome.kind == "refused"
        assert outcome.check == "lab-main"
        assert "git branch lab/main <commit>" in outcome.message
        assert LAB_MAIN not in world.git.refs

    def test_lab_main_checked_out(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        world.git.checked_out.add(LAB_MAIN)

        _assert_refused(world, _attempt(world), "checked-out", "lab/main")

    @pytest.mark.parametrize("which", ["base", "candidate"])
    def test_an_unknown_commit(self, tmp_path: Path, which: str) -> None:
        world = make_world(tmp_path)

        outcome = _attempt(world, **{which: "no-such-commit"})

        _assert_refused(world, outcome, "unknown-commit", f"--{which}", "no-such-commit")

    def test_the_base_must_be_lab_mains_tip(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        newer = world.git.add_commit({"engines/lab-engine/engine.py": "v3\n"}, [world.base])
        world.git.refs[LAB_MAIN] = newer

        outcome = _attempt(world)

        assert outcome.kind == "refused"
        assert outcome.check == "base"
        assert newer[:12] in outcome.message
        assert world.git.refs[LAB_MAIN] == newer
        assert world.runner.requests == []

    def test_the_base_may_be_named_by_the_branch(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished(), finished())

        assert _attempt(world, base="lab/main").kind == "accepted"

    def test_the_candidate_must_be_one_commit_on_the_base(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        grandchild = world.git.add_commit({"engines/lab-engine/x.py": "x\n"}, [world.candidate])

        _assert_refused(world, _attempt(world, candidate=grandchild), "one-commit", "one commit")

    def test_the_candidate_must_not_be_a_merge(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        other = world.git.add_commit({"engines/lab-engine/y.py": "y\n"}, [world.base])
        merge = world.git.add_commit({}, [world.base, other])

        _assert_refused(world, _attempt(world, candidate=merge), "one-commit")

    def test_a_corrupt_ledger(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        broken_tip = world.git.add_commit({LEDGER_PATH: "{not json\n"}, [world.base])
        world.git.refs[LAB_MAIN] = broken_tip
        candidate = world.git.add_commit({"engines/lab-engine/engine.py": "v2\n"}, [broken_tip])

        outcome = _attempt(world, base=broken_tip, candidate=candidate)

        assert (outcome.kind, outcome.check) == ("refused", "ledger")
        assert "line 1" in outcome.message
        assert world.runner.requests == []

    def test_a_leftover_rejected_ref(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        world.git.refs["refs/lab/rejected/1"] = world.base

        outcome = _attempt(world)

        assert (outcome.kind, outcome.check) == ("refused", "rejected-ref")
        assert "refs/lab/rejected/1" in outcome.message
        assert world.runner.requests == []

    def test_no_runner(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        (world.repo / "sprt-runner" / ".venv" / "bin" / "python").unlink()

        _assert_refused(world, _attempt(world), "runner", "make setup")


class TestScope:
    @pytest.mark.parametrize(
        "path",
        [
            "sprt-runner/runner.py",
            "shared/lib.py",
            "engines.json",
            "lab/ledger.jsonl",
            "engines/lab-engine-notes/readme.md",
        ],
    )
    def test_a_change_outside_the_engine(self, tmp_path: Path, path: str) -> None:
        world = make_world(tmp_path)
        candidate = world.git.add_commit(
            {"engines/lab-engine/engine.py": "v2\n", path: "changed\n"}, [world.base]
        )

        outcome = _attempt(world, candidate=candidate)

        _assert_refused(world, outcome, "scope", path)
        assert world.check.calls == []

    def test_no_change_at_all(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        empty = world.git.add_commit({}, [world.base])

        _assert_refused(world, _attempt(world, candidate=empty), "scope", "changes nothing")

    def test_deleting_the_engine(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        gone = world.git.add_commit({"engines/lab-engine/engine.py": None}, [world.base])

        _assert_refused(world, _attempt(world, candidate=gone), "scope", "no engines/lab-engine/")


class TestNoRetries:
    def _world_with_earlier_attempt(self, tmp_path: Path, verdict: str) -> World:
        world = make_world(tmp_path, finished(), finished())
        tree = world.git.tree(world.candidate, "engines/lab-engine")
        assert tree is not None
        reasons = [] if verdict == "accepted" else ["h0"]
        text = Ledger.parse("").append(
            attempt_entry(1, engine_tree=tree, verdict=verdict, reasons=reasons)
        )
        tip = world.git.add_commit({LEDGER_PATH: text}, [world.base])
        world.git.refs[LAB_MAIN] = tip
        retry = world.git.add_commit({"engines/lab-engine/engine.py": "v2\n"}, [tip])
        world.base, world.candidate = tip, retry
        return world

    @pytest.mark.parametrize("verdict", ["accepted", "rejected", "failed"])
    def test_the_same_engine_code_is_refused(self, tmp_path: Path, verdict: str) -> None:
        world = self._world_with_earlier_attempt(tmp_path, verdict)
        tip = world.base

        outcome = _attempt(world)

        assert (outcome.kind, outcome.check) == ("refused", "retry")
        assert "attempt 1" in outcome.message
        assert verdict in outcome.message
        assert world.git.refs[LAB_MAIN] == tip
        assert world.runner.requests == []
        assert world.check.calls == []

    def test_other_engine_code_is_tested(self, tmp_path: Path) -> None:
        world = self._world_with_earlier_attempt(tmp_path, "rejected")
        other = world.git.add_commit({"engines/lab-engine/engine.py": "v4\n"}, [world.base])

        outcome = _attempt(world, candidate=other)

        assert outcome.kind == "accepted"
        assert outcome.entry is not None
        assert outcome.entry.id == 2


class TestHarness:
    def test_a_dirty_harness(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        world.git.dirty_paths = ["sprt-runner/src/runner.py", "frontend/app.tsx"]

        outcome = _attempt(world)

        _assert_refused(world, outcome, "harness", "sprt-runner/src/runner.py")
        assert "frontend" not in outcome.message
        assert world.check.calls == []

    def test_a_missing_book(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        (world.repo / "books" / "test.epd").unlink()

        _assert_refused(world, _attempt(world), "book", "books/test.epd")

    def test_the_labs_own_code_is_part_of_the_harness(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        world.git.dirty_paths = ["lab/src/lab/verdict.py"]

        _assert_refused(world, _attempt(world), "harness", "lab/src/lab/verdict.py")

    def test_no_commit_checked_out(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        world.git.head_commit = None

        _assert_refused(world, _attempt(world), "harness", "no commit checked out")


class TestCorrectness:
    def test_failed_checks_refuse_the_attempt(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        world.check.result = CorrectnessResult(False, False, 4.0, "`pytest` exited 1: 2 failed")

        outcome = _attempt(world)

        _assert_refused(world, outcome, "correctness", "`pytest` exited 1: 2 failed")
        assert world.check.calls == [world.candidate]

    def test_a_stop_during_the_checks_records_nothing(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        world.check.result = CorrectnessResult(False, True, 4.0, "stopped by a signal")

        outcome = _attempt(world)

        assert outcome.kind == "failed"
        assert outcome.exit_code == 5
        assert outcome.entry is None
        assert world.git.refs[LAB_MAIN] == world.base
        assert world.runner.requests == []

    def test_a_harness_change_during_the_checks_records_nothing(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished())

        def edit_harness(stop: asyncio.Event) -> None:
            world.git.dirty_paths = ["sprt-runner/runner.py"]

        world.check.on_check = edit_harness

        outcome = _attempt(world)

        assert (outcome.kind, outcome.entry) == ("failed", None)
        assert world.git.refs[LAB_MAIN] == world.base
        assert world.runner.requests == []

    def test_a_stop_just_after_the_checks_records_nothing(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished())
        world.check.on_check = lambda stop: stop.set()

        outcome = _attempt(world)

        assert outcome.kind == "failed"
        assert outcome.entry is None
        assert world.git.refs[LAB_MAIN] == world.base
        assert world.runner.requests == []


class TestAccepted:
    def test_both_sprts_pass(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished("H1", seed=11), finished("H1", seed=22))

        outcome = _attempt(world)

        assert outcome.kind == "accepted"
        assert outcome.exit_code == 0
        tip = world.git.refs[LAB_MAIN]
        assert world.git.parents(tip) == [world.candidate]
        assert world.git.changed_paths(world.candidate, tip) == [LEDGER_PATH]
        assert not any(ref.startswith("refs/lab/") for ref in world.git.refs)
        entry = _only_entry(world.git)
        assert outcome.entry == entry
        summary = "H1 after 100 games: W 60 L 20 D 20, LLR 2.95"
        assert entry.message == (f"accepted: fast SPRT {summary}; confirmation SPRT {summary}")
        assert outcome.message == f"attempt 1 accepted: lab/main is now {tip[:12]}; {entry.message}"

    def test_records_everything_the_issue_lists(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished("H1", seed=11), finished("H1", seed=22))

        _attempt(world)
        entry = _only_entry(world.git)

        assert entry.id == 1
        assert entry.hypothesis == HYPOTHESIS
        assert (entry.base, entry.candidate) == (world.base, world.candidate)
        assert entry.engine_tree == world.git.tree(world.candidate, "engines/lab-engine")
        assert entry.harness == world.head
        assert entry.harness != world.base
        assert entry.book == "books/test.epd"
        assert entry.book_sha256 == hashlib.sha256(BOOK_TEXT.encode()).hexdigest()
        assert entry.concurrency == 2
        assert entry.config_sha256 == CONFIG_SHA256
        assert entry.correctness_commands == [["python3", "-c", "pass"]]
        assert entry.correctness_s == 61.5
        assert entry.verdict == "accepted"
        assert entry.reasons == []
        assert [s.name for s in entry.stages] == ["fast", "confirm"]
        fast, confirm = entry.stages
        assert (fast.tc, confirm.tc) == (
            "wtime=1000,btime=1000,winc=10,binc=10",
            "wtime=4000,btime=4000,winc=40,binc=40",
        )
        assert (fast.elo0, fast.elo1, fast.alpha, fast.beta) == (0.0, 10.0, 0.05, 0.05)
        assert (fast.max_games, fast.max_time_losses) == (100, 1)
        assert (fast.seed, confirm.seed) == (11, 22)
        assert (fast.result, fast.games, fast.wins, fast.losses, fast.draws) == (
            "H1",
            100,
            60,
            20,
            20,
        )
        assert (fast.errors, fast.llr) == (0, 2.95)
        assert fast.terminations is not None
        assert fast.terminations["checkmate"] == 100
        assert (fast.elo, fast.elo_lower, fast.elo_upper) == (141.0, 70.0, 220.0)
        assert fast.duration_s == 300.0
        assert (fast.outcome, fast.reasons) == ("pass", [])
        assert entry.finished_at > entry.started_at

    def test_runs_each_sprt_as_configured(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished(), finished())

        _attempt(world)

        fast, confirm = world.runner.requests
        assert fast == RunRequest(
            label="attempt 1 fast SPRT",
            base=f"lab-engine:{world.base}",
            test=f"lab-engine:{world.candidate}",
            tc="wtime=1000,btime=1000,winc=10,binc=10",
            concurrency=2,
            book="books/test.epd",
            max_games=100,
            timeout_s=1800.0,
            elo0=0.0,
            elo1=10.0,
            alpha=0.05,
            beta=0.05,
            early_stop=True,
        )
        assert confirm.label == "attempt 1 confirm SPRT"
        assert confirm.tc == "wtime=4000,btime=4000,winc=40,binc=40"
        assert (confirm.max_games, confirm.timeout_s) == (200, 3600.0)

    def test_each_stage_has_its_own_bounds_and_limits(self, tmp_path: Path) -> None:
        world = make_world(
            tmp_path,
            finished("H1"),
            finished("H1", timeout=3),
            **{
                "fast.max_time_losses": 0,
                "confirm.max_time_losses": 3,
                "confirm.alpha": 0.01,
                "confirm.beta": 0.02,
                "confirm.elo1": 5.0,
            },
        )

        outcome = _attempt(world)

        assert outcome.kind == "accepted"
        fast, confirm = world.runner.requests
        assert (fast.alpha, fast.beta, fast.elo1) == (0.05, 0.05, 10.0)
        assert (confirm.alpha, confirm.beta, confirm.elo1) == (0.01, 0.02, 5.0)
        recorded = _only_entry(world.git).stages[1]
        assert (recorded.alpha, recorded.beta, recorded.max_time_losses) == (0.01, 0.02, 3)

    def test_the_ledger_line_is_json_on_lab_main(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished(), finished())

        _attempt(world)

        text = world.git.read_file(world.git.refs[LAB_MAIN], LEDGER_PATH)
        assert text is not None
        assert json.loads(text)["verdict"] == "accepted"


class TestRejected:
    def test_h0_at_the_fast_sprt(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished("H0"))

        outcome = _attempt(world)

        assert outcome.kind == "rejected"
        assert outcome.exit_code == 4
        assert len(world.runner.requests) == 1
        tip = world.git.refs[LAB_MAIN]
        assert world.git.parents(tip) == [world.base]
        assert world.git.changed_paths(world.base, tip) == [LEDGER_PATH]
        assert world.git.refs["refs/lab/rejected/1"] == world.candidate
        entry = _only_entry(world.git)
        assert (entry.verdict, entry.reasons) == ("rejected", ["h0"])
        assert [s.name for s in entry.stages] == ["fast"]
        assert entry.message == (
            "rejected: at the fast SPRT, H0 after 100 games: W 60 L 20 D 20, LLR 2.95; "
            "SPRT accepted H0"
        )
        assert outcome.message == (
            f"attempt 1 {entry.message}; the candidate is kept as refs/lab/rejected/1"
        )

    def test_h0_at_the_confirmation(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished("H1"), finished("H0"))

        outcome = _attempt(world)

        assert outcome.kind == "rejected"
        entry = _only_entry(world.git)
        assert [s.outcome for s in entry.stages] == ["pass", "reject"]
        assert entry.reasons == ["h0"]
        assert world.git.refs["refs/lab/rejected/1"] == world.candidate
        assert "confirm" in outcome.message

    def test_a_crash_rejects_even_after_h1(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished("H1", engine_crash=1))

        outcome = _attempt(world)

        assert outcome.kind == "rejected"
        assert _only_entry(world.git).reasons == ["engine_crash"]
        assert len(world.runner.requests) == 1

    def test_errors_reject_and_are_recorded(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished("H1", errors=2))

        assert _attempt(world).kind == "rejected"
        entry = _only_entry(world.git)
        assert entry.reasons == ["errors"]
        assert entry.stages[0].errors == 2

    def test_inconclusive(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished("inconclusive"))

        assert _attempt(world).kind == "rejected"
        assert _only_entry(world.git).reasons == ["inconclusive"]

    def test_the_next_attempt_numbers_on(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished("H0"), finished("H1"), finished("H1"))
        _attempt(world)
        tip = world.git.refs[LAB_MAIN]
        second = world.git.add_commit({"engines/lab-engine/engine.py": "v3\n"}, [tip])

        outcome = _attempt(world, base=tip, candidate=second)

        assert outcome.kind == "accepted"
        entries = _ledger(world.git).entries
        assert [e.id for e in entries] == [1, 2]
        assert world.git.read_file(world.git.refs[LAB_MAIN], LEDGER_PATH) == Ledger.parse(
            world.git.read_file(tip, LEDGER_PATH) or ""
        ).append(entries[1])


class TestFailed:
    def test_a_broken_runner_is_recorded(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, broken("Failed to resolve engines: build failed"))

        outcome = _attempt(world)

        assert outcome.kind == "failed"
        assert outcome.exit_code == 5
        entry = _only_entry(world.git)
        assert (entry.verdict, entry.reasons) == ("failed", ["runner"])
        assert world.git.refs["refs/lab/rejected/1"] == world.candidate
        assert world.git.parents(world.git.refs[LAB_MAIN]) == [world.base]
        assert "build failed" in outcome.message

    def test_a_stop_during_the_sprt_is_recorded(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, stopped(games=5))

        outcome = _attempt(world)

        assert outcome.kind == "failed"
        entry = _only_entry(world.git)
        assert (entry.verdict, entry.reasons) == ("failed", ["interrupted"])
        (fast,) = entry.stages
        assert (fast.seed, fast.games, fast.wins, fast.losses, fast.draws) == (777, 5, 3, 1, 1)
        assert (fast.errors, fast.llr) == (1, 0.42)
        assert fast.result is None
        assert fast.terminations is None

    def test_a_stop_between_the_sprts_is_recorded(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished("H1"))
        world.runner.on_run = lambda request, stop: stop.set()

        outcome = _attempt(world)

        assert outcome.kind == "failed"
        entry = _only_entry(world.git)
        assert entry.reasons == ["interrupted"]
        assert entry.message == "failed: stopped by a signal before the confirm SPRT"
        assert [s.name for s in entry.stages] == ["fast"]
        assert len(world.runner.requests) == 1

    def test_a_harness_change_between_the_sprts(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished("H1"), finished("H1"))

        def edit_harness(request: RunRequest, stop: asyncio.Event) -> None:
            world.git.dirty_paths = ["shared/lib.py"]

        world.runner.on_run = edit_harness

        outcome = _attempt(world)

        assert outcome.kind == "failed"
        assert _only_entry(world.git).reasons == ["harness_changed"]
        assert len(world.runner.requests) == 1

    @pytest.mark.parametrize("path", ["sprt-runner/runner.py", "shared/lib.py", "lab/lab.toml"])
    def test_a_committed_harness_change_between_the_sprts(self, tmp_path: Path, path: str) -> None:
        world = make_world(tmp_path, finished("H1"), finished("H1"))

        def commit_harness(request: RunRequest, stop: asyncio.Event) -> None:
            world.git.head_commit = world.git.add_commit({path: "changed\n"}, [world.head])

        world.runner.on_run = commit_harness

        outcome = _attempt(world)

        assert outcome.kind == "failed"
        assert _only_entry(world.git).reasons == ["harness_changed"]
        assert len(world.runner.requests) == 1

    def test_unrelated_commits_on_main_between_the_sprts_are_fine(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished("H1"), finished("H1"))

        def commit_elsewhere(request: RunRequest, stop: asyncio.Event) -> None:
            world.git.head_commit = world.git.add_commit({"frontend/x.tsx": "x\n"}, [world.head])

        world.runner.on_run = commit_elsewhere

        assert _attempt(world).kind == "accepted"

    def test_a_book_change_between_the_sprts(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished("H1"), finished("H1"))

        def edit_book(request: RunRequest, stop: asyncio.Event) -> None:
            (world.repo / "books" / "test.epd").write_text("8/8/8/8/8/8/8/K6k w - -\n")

        world.runner.on_run = edit_book

        outcome = _attempt(world)

        assert outcome.kind == "failed"
        assert _only_entry(world.git).reasons == ["harness_changed"]

    def test_lab_main_moving_during_the_attempt(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished("H0"))
        elsewhere = world.git.add_commit({"elsewhere.txt": "x\n"}, [world.base])
        world.runner.on_run = lambda request, stop: world.git.refs.update({LAB_MAIN: elsewhere})

        outcome = _attempt(world)

        assert outcome.kind == "failed"
        assert outcome.check == "record"
        assert "couldn't record" in outcome.message
        assert isinstance(outcome.entry, AttemptEntry)
        assert outcome.entry.verdict == "rejected"
        assert world.git.refs[LAB_MAIN] == elsewhere
        assert "refs/lab/rejected/1" not in world.git.refs


def test_reports_carry_the_seed_even_when_the_runner_fails_after_starting(
    tmp_path: Path,
) -> None:
    report = RunReport(
        ending="exited", timeout_s=60.0, exit_code=1, seed=99, errors=["2 failed games in a row"]
    )
    world = make_world(tmp_path, report)

    _attempt(world)

    assert _only_entry(world.git).stages[0].seed == 99
