"""Pure, deterministic scoring arithmetic — the single source of truth
for how flag points and hint costs are computed.

Deliberately has zero knowledge of attempts, localStorage, or HTTP —
it's just functions of numbers, so it's trivially unit-testable
(tests/test_scoring.py) and easy to hand-verify against the JS mirror
in panel/static/training.js's SCORING block, which must implement the
exact same two formulas. There is no JS test runner in this repo (no
Node/npm, and the panel redesign deliberately avoids adding a build
chain) — this module is the tested spec; the JS is a manually-verified
transliteration of it, not an independent implementation.
"""

from __future__ import annotations

# Hint 1 ("investigation direction") costs 20% of a flag's base points.
# Hint 2 ("specific data source/tool") costs an additional 35%. A flag
# is never worth zero once solved — there is always some credit for
# having found the right answer at all, even with every hint used.
HINT_LEVEL_RATES = {1: 0.20, 2: 0.35}
MINIMUM_POINTS = 1


def hint_cost(base_points: int, level: int) -> int:
    """Points deducted for revealing this one hint level, in isolation."""
    rate = HINT_LEVEL_RATES.get(level)
    if rate is None:
        raise ValueError(f"no such hint level: {level}")
    return round(base_points * rate)


def remaining_points(base_points: int, revealed_levels: list[int]) -> int:
    """Points a flag is still worth, given which hint levels have been
    revealed so far. `revealed_levels` should already be deduplicated
    (revealing the same hint twice must never deduct twice) — callers
    are expected to track revealed levels as a set/unique list, the
    same way panel/static/training.js's TrainingStore does.
    """
    unique_levels = set(revealed_levels)
    total_cost = sum(hint_cost(base_points, level) for level in unique_levels)
    return max(MINIMUM_POINTS, base_points - total_cost)


def scenario_max_points(flags) -> int:
    """Fixed denominator for a scenario's completion percentage — the
    sum of every flag's *base* points, unaffected by hints used. This
    is deliberately different from the sum of current remaining
    points, which shrinks as hints are revealed.
    """
    return sum(flag.points for flag in flags)
