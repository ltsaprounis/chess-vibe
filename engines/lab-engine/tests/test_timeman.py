"""Tests for lab_engine.timeman: how long to think about a move."""

from __future__ import annotations

import pytest
from lab_engine.timeman import (
    DEFAULT_MOVES_TO_GO,
    MOVE_OVERHEAD_MS,
    TimeBudget,
    clock_budget,
    movetime_budget,
)


class TestTheNumbers:
    """The constants, and two budgets worked through by hand."""

    def test_constants(self) -> None:
        assert (MOVE_OVERHEAD_MS, DEFAULT_MOVES_TO_GO) == (30, 30)

    def test_ten_seconds_and_a_tenth(self) -> None:
        # 10,000 // 30 + 100 * 3 // 4 = 333 + 75; hard is three times that.
        assert clock_budget(10_000, 100, None) == TimeBudget(soft_ms=408, hard_ms=1_224)

    def test_one_second_a_move(self) -> None:
        assert movetime_budget(1_000) == TimeBudget(soft_ms=970, hard_ms=970)


class TestMovetime:
    """A fixed time per move is used whole, less the overhead."""

    def test_uses_the_movetime_less_the_overhead(self) -> None:
        assert movetime_budget(1_000) == TimeBudget(
            soft_ms=1_000 - MOVE_OVERHEAD_MS, hard_ms=1_000 - MOVE_OVERHEAD_MS
        )

    def test_a_movetime_inside_the_overhead_means_moving_at_once(self) -> None:
        assert movetime_budget(MOVE_OVERHEAD_MS // 2) == TimeBudget(soft_ms=0, hard_ms=0)


class TestClock:
    """On a clock, a move gets a share of the time left, plus most of the increment."""

    def test_spends_a_share_of_the_clock_and_most_of_the_increment(self) -> None:
        budget = clock_budget(10_000, 100, None)
        assert budget.soft_ms == 10_000 // DEFAULT_MOVES_TO_GO + 75
        assert budget.hard_ms == 3 * budget.soft_ms

    def test_never_plans_past_the_clock(self) -> None:
        for remaining in (0, 1, 10, 100, 1_000, 10_000, 100_000):
            for increment in (0, 10, 100, 1_000, 10_000):
                for moves_to_go in (None, 1, 2, 40):
                    budget = clock_budget(remaining, increment, moves_to_go)
                    limit = max(remaining - MOVE_OVERHEAD_MS, 0)
                    assert 0 <= budget.soft_ms <= budget.hard_ms <= limit, (
                        remaining,
                        increment,
                        moves_to_go,
                    )

    def test_an_empty_clock_means_moving_at_once(self) -> None:
        assert clock_budget(0, 0, None) == TimeBudget(soft_ms=0, hard_ms=0)
        assert clock_budget(MOVE_OVERHEAD_MS, 1_000, None) == TimeBudget(soft_ms=0, hard_ms=0)

    def test_spends_more_with_more_time(self) -> None:
        less = clock_budget(5_000, 0, None)
        more = clock_budget(50_000, 0, None)
        assert more.soft_ms > less.soft_ms
        assert more.hard_ms > less.hard_ms

    def test_spends_more_with_a_bigger_increment(self) -> None:
        assert clock_budget(60_000, 1_000, None).soft_ms > clock_budget(60_000, 0, None).soft_ms

    def test_moves_to_go_divides_the_clock(self) -> None:
        assert clock_budget(10_000, 0, 10).soft_ms == 1_000
        assert clock_budget(10_000, 0, 20).soft_ms == 500

    def test_the_last_move_before_the_time_control_may_use_all_of_it(self) -> None:
        budget = clock_budget(5_000, 0, 1)
        assert budget == TimeBudget(
            soft_ms=5_000 - MOVE_OVERHEAD_MS, hard_ms=5_000 - MOVE_OVERHEAD_MS
        )

    @pytest.mark.parametrize("moves_to_go", [0, -3])
    def test_a_meaningless_moves_to_go_is_ignored(self, moves_to_go: int) -> None:
        assert clock_budget(10_000, 0, moves_to_go) == clock_budget(10_000, 0, None)
