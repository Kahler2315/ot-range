"""Scoring arithmetic must be deterministic and tested — this is the
authoritative spec panel/static/training.js's JS mirror is verified
against by hand (no JS test runner in this repo)."""

from __future__ import annotations

import pytest

from scenarios.scoring import MINIMUM_POINTS, hint_cost, remaining_points, scenario_max_points


def test_hint_cost_is_20_percent_for_level_1():
    assert hint_cost(10, 1) == 2
    assert hint_cost(5, 1) == 1


def test_hint_cost_is_35_percent_for_level_2():
    assert hint_cost(10, 2) == 4
    assert hint_cost(8, 2) == 3


def test_hint_cost_rejects_unknown_level():
    with pytest.raises(ValueError):
        hint_cost(10, 3)


def test_remaining_points_with_no_hints_is_full_base():
    assert remaining_points(10, []) == 10


def test_remaining_points_deducts_each_revealed_level_once():
    assert remaining_points(10, [1]) == 8
    assert remaining_points(10, [1, 2]) == 4


def test_remaining_points_revealing_the_same_hint_twice_does_not_double_deduct():
    # A flag's UI must track revealed levels as a set — this pins the
    # arithmetic side of that guarantee: duplicate entries in the input
    # list must not deduct twice.
    assert remaining_points(10, [1, 1, 1]) == remaining_points(10, [1])
    assert remaining_points(10, [1, 2, 1, 2]) == remaining_points(10, [1, 2])


def test_remaining_points_never_drops_below_the_floor():
    assert remaining_points(1, [1, 2]) >= MINIMUM_POINTS
    assert remaining_points(3, [1, 2]) >= MINIMUM_POINTS


def test_remaining_points_is_deterministic():
    # Same inputs, same output, every time — no hidden state.
    results = {remaining_points(9, [1, 2]) for _ in range(50)}
    assert len(results) == 1


def test_scenario_max_points_sums_base_points_not_reduced_by_hints():
    class FakeFlag:
        def __init__(self, points):
            self.points = points

    flags = [FakeFlag(6), FakeFlag(5), FakeFlag(8), FakeFlag(7), FakeFlag(9)]
    assert scenario_max_points(flags) == 35
