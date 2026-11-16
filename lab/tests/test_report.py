"""Tests for ``lab report``: the ledger summed up, in plain text."""

from __future__ import annotations

import pytest
from lab.ledger import Ledger
from lab.refusal import Refused
from lab.report import render_report, report_text

from tests.entries import anchor, attempt, match, stage
from tests.fake_repo import FakeRepo

TIP = "a" * 40


def _ledger(*entries: object) -> Ledger:
    text = ""
    for entry in entries:
        text = Ledger.parse(text).append(entry)  # type: ignore[arg-type]
    return Ledger.parse(text)


FULL_REPORT = """\
lab/main at aaaaaaaaaaaa: 5 ledger entries

Attempts: 3
  accepted  1
  rejected  1  (h0 1)
  failed    1  (interrupted 1)
Acceptance rate: 33% (1 of 3)

Attempt 1, accepted: Order captures by MVV-LVA
  fast     H1 after 100 games, W 50 L 30 D 20, Elo +70.4 [+20.1, +122.7]
  confirm  H1 after 100 games, W 50 L 30 D 20, Elo +70.4 [+20.1, +122.7]
Attempt 3, rejected (h0): Prune bad captures
  fast     H0 after 100 games, W 20 L 50 D 30, Elo -107.5 [-160.0, -56.2]
Attempt 4, failed (interrupted): Extend checks
  fast     no result after 5 games, W 3 L 1 D 1

Milestones:
  1  ffffffffffff  frozen by anchor run 2, against random-engine: +636.0 [+390.0, +inf]

Latest anchor run, 5: lab/main at ffffffffffff, flagged (not_clearly_stronger)
  random-engine  W 19 L 0 D 1, Elo +636.0 [+390.0, +inf]
  milestone 1    W 10 L 8 D 2, Elo +34.9 [-20.0, +90.0]

Machine hours: 7.5 (attempts 4.5, anchor runs 3.0)
"""


def test_a_full_report() -> None:
    ledger = _ledger(
        attempt(1),
        anchor(2, milestone=1),
        attempt(
            3,
            engine_tree="1" * 40,
            hypothesis="Prune bad captures",
            verdict="rejected",
            reasons=["h0"],
            stages=[
                stage(
                    "fast",
                    result="H0",
                    wins=20,
                    losses=50,
                    draws=30,
                    elo=-107.5,
                    elo_lower=-160.0,
                    elo_upper=-56.2,
                    outcome="reject",
                    reasons=["h0"],
                )
            ],
        ),
        attempt(
            4,
            engine_tree="2" * 40,
            hypothesis="Extend checks",
            verdict="failed",
            reasons=["interrupted"],
            stages=[
                stage(
                    "fast",
                    result=None,
                    games=5,
                    wins=3,
                    losses=1,
                    draws=1,
                    llr=None,
                    terminations=None,
                    elo=None,
                    elo_lower=None,
                    elo_upper=None,
                    outcome="fail",
                    reasons=["interrupted"],
                )
            ],
        ),
        anchor(
            5,
            milestone=None,
            matches=[
                match(),
                match(
                    "milestone 1",
                    engine=f"lab-engine:{'f' * 40}",
                    wins=10,
                    losses=8,
                    draws=2,
                    elo=34.9,
                    elo_lower=-20.0,
                    elo_upper=90.0,
                ),
            ],
            previous="milestone 1",
        ),
    )

    assert render_report(ledger, TIP) == FULL_REPORT


EMPTY_REPORT = """\
lab/main at aaaaaaaaaaaa: no ledger entries yet

Attempts: 0
  accepted  0
  rejected  0
  failed    0
Acceptance rate: n/a

Milestones: none yet

Latest anchor run: none yet

Machine hours: 0.0 (attempts 0.0, anchor runs 0.0)
"""


def test_an_empty_ledger() -> None:
    assert render_report(Ledger.parse(""), TIP) == EMPTY_REPORT


def test_a_milestone_run_is_named_as_such() -> None:
    text = render_report(_ledger(anchor(1, milestone=1)), TIP)

    assert "Latest anchor run, 1: lab/main at ffffffffffff, milestone 1\n" in text


def test_a_failed_run_is_named_as_such() -> None:
    failed = match(
        "milestone 1",
        games=3,
        wins=2,
        losses=0,
        draws=1,
        terminations=None,
        elo=None,
        elo_lower=None,
        elo_upper=None,
        outcome="fail",
        reasons=["runner"],
    )
    ledger = _ledger(
        anchor(1, milestone=None, outcome="failed", reasons=["runner"], matches=[match(), failed])
    )

    text = render_report(ledger, TIP)

    assert "Latest anchor run, 1: lab/main at ffffffffffff, failed (runner)\n" in text
    assert "  milestone 1    no result after 3 games, W 2 L 0 D 1  (runner)\n" in text
    assert "Milestones: none yet\n" in text


def test_breakdowns_list_reasons_by_name() -> None:
    ledger = _ledger(
        attempt(1, engine_tree="1" * 40, verdict="rejected", reasons=["time_losses", "h0"]),
        attempt(2, engine_tree="2" * 40, verdict="rejected", reasons=["engine_crash"]),
        attempt(3, engine_tree="3" * 40, verdict="rejected", reasons=["h0"]),
    )

    text = render_report(ledger, TIP)

    assert "  rejected  3  (engine_crash 1, h0 2, time_losses 1)\n" in text
    assert "Acceptance rate: 0% (0 of 3)\n" in text


class TestReportText:
    def test_reads_lab_main(self) -> None:
        git = FakeRepo()
        text = Ledger.parse("").append(attempt(1))
        tip = git.add_commit({"lab/ledger.jsonl": text})
        git.refs["refs/heads/lab/main"] = tip

        assert report_text(git) == render_report(Ledger.parse(text), tip)

    def test_refuses_without_lab_main(self) -> None:
        with pytest.raises(Refused) as caught:
            report_text(FakeRepo())

        assert caught.value.check == "lab-main"

    def test_refuses_a_corrupt_ledger(self) -> None:
        git = FakeRepo()
        git.refs["refs/heads/lab/main"] = git.add_commit({"lab/ledger.jsonl": "{\n"})

        with pytest.raises(Refused) as caught:
            report_text(git)

        assert caught.value.check == "ledger"
