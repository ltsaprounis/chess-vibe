"""Tests for the ledger: its entries, its text, and what the lab looks up in it."""

from __future__ import annotations

import json

import pytest
from lab.ledger import AnchorEntry, AttemptEntry, Ledger, LedgerError

from tests.entries import FINISHED, STARTED, anchor, attempt


class TestText:
    def test_an_empty_ledger(self) -> None:
        ledger = Ledger.parse("")

        assert ledger.entries == ()
        assert ledger.next_id() == 1

    def test_an_entry_round_trips(self) -> None:
        entry = attempt(1)

        text = Ledger.parse("").append(entry)

        assert text.endswith("\n")
        assert text.count("\n") == 1
        assert Ledger.parse(text).entries == (entry,)

    def test_each_entry_is_one_json_object_naming_its_kind_first(self) -> None:
        text = Ledger.parse("").append(attempt(1))

        data = json.loads(text)

        assert next(iter(data)) == "kind"
        assert data["kind"] == "attempt"
        assert data["started_at"] == "2026-11-13T15:00:00Z"
        assert data["stages"][0]["name"] == "fast"

    def test_appending_keeps_the_text_before_it(self) -> None:
        first = Ledger.parse("").append(attempt(1))

        second = Ledger.parse(first).append(anchor(2))

        assert second.startswith(first)
        assert Ledger.parse(second).entries == (attempt(1), anchor(2))

    def test_a_missing_final_newline_is_mended_on_append(self) -> None:
        first = Ledger.parse("").append(attempt(1)).rstrip("\n")

        second = Ledger.parse(first).append(attempt(2, engine_tree="1" * 40))

        assert second.startswith(first + "\n")
        assert len(Ledger.parse(second).entries) == 2

    def test_times_are_utc(self) -> None:
        (entry,) = Ledger.parse(Ledger.parse("").append(attempt(1))).entries

        assert entry.started_at == STARTED
        assert entry.finished_at == FINISHED
        assert entry.duration_s == 5400.0


class TestAppendKeepsTheSequence:
    def test_the_next_id_only(self) -> None:
        with pytest.raises(LedgerError, match="entry 3 isn't the next one, 2"):
            _ledger(attempt(1)).append(attempt(3))

    def test_the_next_milestone_only(self) -> None:
        with pytest.raises(LedgerError, match="milestone 2 isn't the next one, 1"):
            _ledger(attempt(1)).append(anchor(2, milestone=2))

    def test_a_flagged_run_needs_no_number(self) -> None:
        text = _ledger(anchor(1, milestone=1)).append(anchor(2, milestone=None))

        assert [e.id for e in Ledger.parse(text).entries] == [1, 2]


class TestLookups:
    def test_next_id(self) -> None:
        text = Ledger.parse(Ledger.parse("").append(attempt(1))).append(anchor(2))

        assert Ledger.parse(text).next_id() == 3

    def test_attempt_for_tree(self) -> None:
        entries = [
            attempt(1, engine_tree="1" * 40),
            anchor(2, engine_tree="2" * 40),
            attempt(3, engine_tree="2" * 40, verdict="rejected", reasons=["h0"]),
            attempt(4, engine_tree="2" * 40, verdict="failed", reasons=["runner"]),
        ]
        ledger = _ledger(*entries)

        found = ledger.attempt_for_tree("2" * 40)

        assert isinstance(found, AttemptEntry)
        assert found.id == 3
        assert ledger.attempt_for_tree("1" * 40) == entries[0]
        assert ledger.attempt_for_tree("3" * 40) is None

    def test_an_anchor_is_not_an_attempt(self) -> None:
        ledger = _ledger(anchor(1, engine_tree="2" * 40))

        assert ledger.attempt_for_tree("2" * 40) is None

    def test_anchor_for_tree(self) -> None:
        ledger = _ledger(
            attempt(1, engine_tree="1" * 40),
            anchor(2, milestone=None, engine_tree="1" * 40, outcome="failed", reasons=["runner"]),
            anchor(3, milestone=1, engine_tree="2" * 40),
        )

        found = ledger.anchor_for_tree("1" * 40)

        assert isinstance(found, AnchorEntry)
        assert (found.id, found.outcome) == (2, "failed")
        assert ledger.anchor_for_tree("2" * 40) == ledger.entries[2]
        assert ledger.anchor_for_tree("3" * 40) is None

    def test_milestones_in_order(self) -> None:
        ledger = _ledger(
            anchor(1, milestone=1, version="1" * 40),
            attempt(2),
            anchor(3, milestone=None, version="3" * 40),
            anchor(4, milestone=2, version="4" * 40),
        )

        milestones = ledger.milestones()

        assert [m.milestone for m in milestones] == [1, 2]
        assert all(m.outcome == "milestone" for m in milestones)
        assert [m.version for m in milestones] == ["1" * 40, "4" * 40]
        assert all(isinstance(m, AnchorEntry) for m in milestones)


def _ledger(*entries: AttemptEntry | AnchorEntry) -> Ledger:
    text = ""
    for entry in entries:
        text = Ledger.parse(text).append(entry)
    return Ledger.parse(text)


def _line(entry: AttemptEntry | AnchorEntry) -> str:
    return entry.model_dump_json()


class TestRefusesACorruptLedger:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("{not json\n", "line 1", id="not-json"),
            pytest.param('{"kind": "attempt"}\n', "line 1", id="missing-fields"),
            pytest.param('{"kind": "surprise", "id": 1}\n', "line 1", id="unknown-kind"),
            pytest.param("\n", "line 1", id="blank-line"),
        ],
    )
    def test_names_the_line(self, text: str, expected: str) -> None:
        with pytest.raises(LedgerError, match=expected):
            Ledger.parse(text)

    def test_an_unknown_field(self) -> None:
        data = json.loads(_line(attempt(1)))
        data["surprise"] = 1

        with pytest.raises(LedgerError, match="line 1"):
            Ledger.parse(json.dumps(data) + "\n")

    def test_ids_must_count_up_from_one(self) -> None:
        text = _line(attempt(1)) + "\n" + _line(attempt(3)) + "\n"

        with pytest.raises(LedgerError, match="line 2 has id 3, expected 2"):
            Ledger.parse(text)

    @pytest.mark.parametrize(
        ("outcome", "milestone"),
        [("milestone", None), ("flagged", 1), ("failed", 1)],
    )
    def test_an_outcome_must_agree_with_its_milestone(
        self, outcome: str, milestone: int | None
    ) -> None:
        data = json.loads(_line(anchor(1, milestone=1)))
        data["outcome"], data["milestone"] = outcome, milestone

        with pytest.raises(LedgerError, match="line 1"):
            Ledger.parse(json.dumps(data) + "\n")

    def test_milestones_must_count_up_from_one(self) -> None:
        text = _line(anchor(1, milestone=2)) + "\n"

        with pytest.raises(LedgerError, match="line 1 freezes milestone 2, expected 1"):
            Ledger.parse(text)

    def test_the_error_names_the_ledger_line_after_the_first(self) -> None:
        text = _line(attempt(1)) + "\n" + '{"kind": "anchor", "id": 2}\n'

        with pytest.raises(LedgerError, match="line 2"):
            Ledger.parse(text)
