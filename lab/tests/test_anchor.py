"""Unit tests for ``lab anchor``: its matches, its milestones, and what it records."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from lab.anchor import run_anchor
from lab.ledger import LEDGER_PATH, AnchorEntry, Ledger
from lab.outcome import Outcome
from lab.runs import RunReport, RunRequest

from tests.doubles import BOOK_TEXT, CONFIG_SHA256, World, broken, finished, make_world, stopped
from tests.entries import anchor as anchor_entry
from tests.entries import attempt as attempt_entry

LAB_MAIN = "refs/heads/lab/main"
ENGINE_DIR = "engines/lab-engine"


def _anchor(world: World, stop: asyncio.Event | None = None) -> Outcome:
    async def go() -> Outcome:
        return await run_anchor(world.lab, stop or asyncio.Event())

    return asyncio.run(go())


def _match(elo_lower: float | None = 12.0, **complete: Any) -> RunReport:
    return finished("fixed", elo_lower=elo_lower, **complete)


def _ledger(world: World) -> Ledger:
    return Ledger.parse(world.git.read_file(world.git.refs[LAB_MAIN], LEDGER_PATH) or "")


def _last_anchor(world: World) -> AnchorEntry:
    entry = _ledger(world).entries[-1]
    assert isinstance(entry, AnchorEntry)
    return entry


def _with_milestones(world: World, *engines: str, tip_engine: str = "newest\n") -> list[str]:
    """Freeze a commit of each engine text as milestones 1, 2, ...; then put a new engine on top.

    Returns the milestones' commits. lab/main's tip has ``tip_engine``.
    """
    tip, text = world.base, ""
    versions: list[str] = []
    for number, engine in enumerate(engines, start=1):
        version = world.git.add_commit({f"{ENGINE_DIR}/engine.py": engine}, [tip])
        tree = world.git.tree(version, ENGINE_DIR)
        entry = anchor_entry(number, milestone=number, version=version, engine_tree=tree)
        text = Ledger.parse(text).append(entry)
        tip = world.git.add_commit({LEDGER_PATH: text}, [version])
        versions.append(version)
    tip = world.git.add_commit({f"{ENGINE_DIR}/engine.py": tip_engine}, [tip])
    world.git.refs[LAB_MAIN] = tip
    return versions


def _assert_refused(world: World, outcome: Outcome, check: str, *words: str) -> None:
    assert (outcome.kind, outcome.check, outcome.exit_code) == ("refused", check, 3)
    assert outcome.entry is None
    for word in words:
        assert word in outcome.message
    assert world.runner.requests == []


class TestRefusals:
    def test_no_identity(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        world.git.identity = False

        _assert_refused(world, _anchor(world), "identity")

    def test_no_lab_main(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        del world.git.refs[LAB_MAIN]

        _assert_refused(world, _anchor(world), "lab-main", "git branch lab/main <commit>")

    def test_lab_main_checked_out(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        world.git.checked_out.add(LAB_MAIN)

        _assert_refused(world, _anchor(world), "checked-out")

    def test_a_corrupt_ledger(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        world.git.refs[LAB_MAIN] = world.git.add_commit({LEDGER_PATH: "nonsense\n"}, [world.base])

        _assert_refused(world, _anchor(world), "ledger", "line 1")

    def test_no_runner(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        (world.repo / "sprt-runner" / ".venv" / "bin" / "python").unlink()

        _assert_refused(world, _anchor(world), "runner")

    def test_a_dirty_harness(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        world.git.dirty_paths = ["shared/lib.py"]

        _assert_refused(world, _anchor(world), "harness", "shared/lib.py")

    def test_a_missing_book(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        (world.repo / "books" / "test.epd").unlink()

        _assert_refused(world, _anchor(world), "book")

    def test_lab_main_without_the_engine(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        world.git.refs[LAB_MAIN] = world.git.add_commit(
            {f"{ENGINE_DIR}/engine.py": None}, [world.base]
        )

        _assert_refused(world, _anchor(world), "lab-main", "no engines/lab-engine/")


class TestOneRunPerEngine:
    """Anchoring the same engine code again can't freeze it by chance."""

    def test_the_newest_milestone(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        _with_milestones(world, "m1\n", "m2\n", tip_engine="m2\n")

        _assert_refused(world, _anchor(world), "retry", "anchor run 2", "milestone 2")

    def test_a_flagged_tip(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match(-5.0))
        assert _anchor(world).kind == "flagged"

        outcome = _anchor(world)

        assert (outcome.kind, outcome.check) == ("refused", "retry")
        assert "anchor run 1" in outcome.message
        assert "flagged" in outcome.message
        assert len(world.runner.requests) == 1

    def test_a_tip_whose_run_failed(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, broken())
        assert _anchor(world).kind == "failed"

        outcome = _anchor(world)

        assert (outcome.kind, outcome.check) == ("refused", "retry")
        assert "failed" in outcome.message

    def test_an_older_milestones_engine(self, tmp_path: Path) -> None:
        world = make_world(tmp_path)
        _with_milestones(world, "m1\n", "m2\n", tip_engine="m1\n")

        _assert_refused(world, _anchor(world), "retry", "anchor run 1", "milestone 1")


class TestFirstRun:
    def test_freezes_a_tip_clearly_stronger_than_random_engine(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match(elo_lower=300.0))

        outcome = _anchor(world)

        assert (outcome.kind, outcome.exit_code) == ("milestone", 0)
        assert [r.base for r in world.runner.requests] == [f"random-engine:{world.head}"]
        entry = _last_anchor(world)
        assert outcome.entry == entry
        assert (entry.outcome, entry.milestone, entry.previous) == ("milestone", 1, "random-engine")
        assert entry.reasons == []
        assert "milestone 1" in outcome.message
        tip = world.git.refs[LAB_MAIN]
        assert world.git.parents(tip) == [world.base]
        assert world.git.changed_paths(world.base, tip) == [LEDGER_PATH]

    def test_plays_each_match_as_configured(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match())

        _anchor(world)

        (request,) = world.runner.requests
        assert request == RunRequest(
            label="anchor 1: lab/main vs random-engine",
            base=f"random-engine:{world.head}",
            test=f"lab-engine:{world.base}",
            tc="wtime=1000,btime=1000,winc=10,binc=10",
            concurrency=2,
            book="books/test.epd",
            max_games=20,
            timeout_s=900.0,
            early_stop=False,
        )

    def test_records_the_run(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match(wins=15, losses=2, draws=3, seed=31))

        _anchor(world)
        entry = _last_anchor(world)

        assert entry.id == 1
        assert entry.version == world.base
        assert entry.engine_tree == world.git.tree(world.base, ENGINE_DIR)
        assert entry.harness == world.head
        assert entry.harness != entry.version
        assert entry.book == "books/test.epd"
        assert entry.book_sha256 == hashlib.sha256(BOOK_TEXT.encode()).hexdigest()
        assert entry.config_sha256 == CONFIG_SHA256
        assert (entry.concurrency, entry.games, entry.max_time_losses) == (2, 20, 0)
        assert entry.tc == "wtime=1000,btime=1000,winc=10,binc=10"
        (match,) = entry.matches
        assert match.opponent == "random-engine"
        assert match.engine == f"random-engine:{world.head}"
        assert (match.seed, match.games, match.wins, match.losses, match.draws) == (
            31,
            20,
            15,
            2,
            3,
        )
        assert (match.elo, match.elo_lower, match.elo_upper) == (141.0, 12.0, 220.0)
        assert match.terminations is not None
        assert (match.outcome, match.reasons) == ("pass", [])
        assert match.duration_s == 300.0
        assert entry.finished_at > entry.started_at

    def test_numbers_on_from_earlier_entries(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match())
        text = Ledger.parse("").append(attempt_entry(1, verdict="rejected", reasons=["h0"]))
        world.git.refs[LAB_MAIN] = world.git.add_commit({LEDGER_PATH: text}, [world.base])

        _anchor(world)

        assert [e.id for e in _ledger(world).entries] == [1, 2]
        assert _last_anchor(world).milestone == 1


class TestLaterRuns:
    def test_plays_every_milestone_oldest_first(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match(), _match(), _match())
        first, second = _with_milestones(world, "m1\n", "m2\n")

        outcome = _anchor(world)

        assert [r.base for r in world.runner.requests] == [
            f"random-engine:{world.head}",
            f"lab-engine:{first}",
            f"lab-engine:{second}",
        ]
        assert [r.label for r in world.runner.requests] == [
            "anchor 3: lab/main vs random-engine",
            "anchor 3: lab/main vs milestone 1",
            "anchor 3: lab/main vs milestone 2",
        ]
        entry = _last_anchor(world)
        assert [m.opponent for m in entry.matches] == [
            "random-engine",
            "milestone 1",
            "milestone 2",
        ]
        assert entry.previous == "milestone 2"
        assert (entry.outcome, entry.milestone) == ("milestone", 3)
        assert outcome.kind == "milestone"

    def test_flagged_when_not_clearly_stronger_than_the_newest(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match(300.0), _match(40.0), _match(-5.0))
        _with_milestones(world, "m1\n", "m2\n")
        before = world.git.refs[LAB_MAIN]

        outcome = _anchor(world)

        assert (outcome.kind, outcome.exit_code) == ("flagged", 4)
        entry = _last_anchor(world)
        assert (entry.outcome, entry.milestone) == ("flagged", None)
        assert entry.reasons == ["not_clearly_stronger"]
        assert "milestone 2" in outcome.message
        assert "+141.0 [-5.0, +220.0]" in outcome.message
        assert world.git.parents(world.git.refs[LAB_MAIN]) == [before]

    def test_flagged_when_a_match_was_not_clean(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match(300.0, engine_crash=1))

        outcome = _anchor(world)

        assert outcome.kind == "flagged"
        entry = _last_anchor(world)
        assert entry.milestone is None
        assert entry.reasons == ["engine_crash"]
        assert entry.matches[0].reasons == ["engine_crash"]
        assert "engine_crash" in outcome.message

    def test_an_unclean_match_before_the_last_flags_the_tip(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match(300.0, engine_crash=1), _match(80.0))
        _with_milestones(world, "m1\n")

        outcome = _anchor(world)

        assert outcome.kind == "flagged"
        entry = _last_anchor(world)
        assert entry.reasons == ["engine_crash"]
        assert "against random-engine" in outcome.message

    def test_a_reason_shared_by_two_matches_is_given_once(self, tmp_path: Path) -> None:
        world = make_world(
            tmp_path, _match(300.0, engine_crash=1), _match(80.0, engine_crash=2, errors=1)
        )
        _with_milestones(world, "m1\n")

        _anchor(world)

        entry = _last_anchor(world)
        assert entry.reasons == ["engine_crash", "errors"]
        assert [m.errors for m in entry.matches] == [0, 1]

    def test_time_losses_within_the_limit_are_clean(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match(300.0, timeout=2), **{"anchor.max_time_losses": 2})

        outcome = _anchor(world)

        assert outcome.kind == "milestone"
        assert _last_anchor(world).matches[0].outcome == "pass"

    def test_every_game_won_is_clearly_stronger(self, tmp_path: Path) -> None:
        report = finished(
            "fixed", wins=20, losses=0, draws=0, elo=None, elo_lower=None, elo_upper=None
        )
        world = make_world(tmp_path, report)

        outcome = _anchor(world)

        assert outcome.kind == "milestone"
        assert "+inf [+inf, +inf]" in outcome.message


class TestFailures:
    """A run that has started a match is recorded however it ends, so it can't be retried."""

    def test_a_failed_match_is_recorded(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match(), broken("Failed to resolve engines"), _match())
        _with_milestones(world, "m1\n", "m2\n")
        before = world.git.refs[LAB_MAIN]

        outcome = _anchor(world)

        assert (outcome.kind, outcome.exit_code) == ("failed", 5)
        assert "milestone 1" in outcome.message
        assert "Failed to resolve engines" in outcome.message
        assert len(world.runner.requests) == 2
        entry = _last_anchor(world)
        assert outcome.entry == entry
        assert (entry.outcome, entry.milestone, entry.reasons) == ("failed", None, ["runner"])
        assert [m.outcome for m in entry.matches] == ["pass", "fail"]
        assert entry.previous == "milestone 2"
        assert world.git.parents(world.git.refs[LAB_MAIN]) == [before]

    def test_a_result_that_is_not_a_fixed_match(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, finished("H1"))

        outcome = _anchor(world)

        assert outcome.kind == "failed"
        entry = _last_anchor(world)
        assert entry.reasons == ["unexpected_result"]
        (match,) = entry.matches
        assert (match.games, match.wins) == (100, 60)
        assert (match.terminations, match.elo, match.elo_lower, match.elo_upper) == (
            None,
            None,
            None,
            None,
        )

    def test_a_stop_during_a_match_is_recorded(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, stopped(games=5))

        outcome = _anchor(world)

        assert outcome.kind == "failed"
        entry = _last_anchor(world)
        assert entry.reasons == ["interrupted"]
        (match,) = entry.matches
        assert (match.games, match.wins, match.losses, match.draws) == (5, 3, 1, 1)
        assert match.errors == 1
        assert (match.outcome, match.terminations) == ("fail", None)

    def test_a_stop_between_matches_is_recorded(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match(), _match())
        _with_milestones(world, "m1\n")
        world.runner.on_run = lambda request, stop: stop.set()

        outcome = _anchor(world)

        assert outcome.kind == "failed"
        assert len(world.runner.requests) == 1
        entry = _last_anchor(world)
        assert entry.reasons == ["interrupted"]
        assert [m.opponent for m in entry.matches] == ["random-engine"]

    def test_a_stop_before_the_first_match_records_nothing(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match())
        stop = asyncio.Event()
        stop.set()

        outcome = _anchor(world, stop)

        assert (outcome.kind, outcome.entry) == ("failed", None)
        assert world.runner.requests == []
        assert world.git.refs[LAB_MAIN] == world.base

    def test_a_harness_change_between_matches_is_recorded(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match(), _match())
        _with_milestones(world, "m1\n")

        def edit(request: RunRequest, stop: asyncio.Event) -> None:
            world.git.dirty_paths = ["sprt-runner/runner.py"]

        world.runner.on_run = edit

        outcome = _anchor(world)

        assert outcome.kind == "failed"
        assert len(world.runner.requests) == 1
        assert _last_anchor(world).reasons == ["harness_changed"]

    def test_lab_main_moving_during_the_run(self, tmp_path: Path) -> None:
        world = make_world(tmp_path, _match())
        elsewhere = world.git.add_commit({"elsewhere.txt": "x\n"}, [world.base])
        world.runner.on_run = lambda request, stop: world.git.refs.update({LAB_MAIN: elsewhere})

        outcome = _anchor(world)

        assert (outcome.kind, outcome.check) == ("failed", "record")
        assert isinstance(outcome.entry, AnchorEntry)
        assert world.git.refs[LAB_MAIN] == elsewhere


def test_only_the_newest_milestone_decides(tmp_path: Path) -> None:
    world = make_world(tmp_path, _match(), _match(-50.0), _match(80.0))
    _with_milestones(world, "m1\n", "m2\n")

    outcome = _anchor(world)

    entry = _last_anchor(world)
    assert entry.previous == "milestone 2"
    assert (outcome.kind, entry.milestone) == ("milestone", 3)
