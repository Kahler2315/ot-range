"""Regression + model-shape tests for scenarios/flags.py — existing
check()/normalize() behavior must be unchanged, and every flag must
carry the new training fields (points, hints, category, objectives)
in a well-formed way."""

from __future__ import annotations

from scenarios.catalog import LEARNING_OBJECTIVES, SCENARIOS, SCENARIOS_BY_ID
from scenarios.flags import FLAGS_BY_SCENARIO, Flag, check, normalize
from scenarios.scoring import hint_cost


def test_normalize_is_case_and_whitespace_insensitive():
    assert normalize("  Hello   World.  ") == "hello world"


def test_check_accepts_any_listed_alternative():
    flag = Flag("x", "prompt", ["a", "b c"])
    assert check(flag, "A")
    assert check(flag, "b   c.")
    assert not check(flag, "z")


def test_every_scenario_has_flags():
    for scenario_id in SCENARIOS_BY_ID:
        assert FLAGS_BY_SCENARIO.get(scenario_id), scenario_id


def test_every_flag_has_at_least_one_hint():
    for scenario_id, flags in FLAGS_BY_SCENARIO.items():
        for flag in flags:
            assert flag.hints, f"{scenario_id}/{flag.id} has no hints"


def test_every_flag_has_positive_points():
    for flags in FLAGS_BY_SCENARIO.values():
        for flag in flags:
            assert flag.points > 0, flag.id


def test_hint_costs_are_strictly_increasing_by_level():
    # Level 2 must cost more than level 1 for every flag that has both
    # — otherwise "progressively revealing" hints wouldn't be true.
    for flags in FLAGS_BY_SCENARIO.values():
        for flag in flags:
            if len(flag.hints) >= 2:
                assert hint_cost(flag.points, 1) < hint_cost(flag.points, 2), flag.id


def test_every_flag_objective_id_resolves():
    for scenario_id, flags in FLAGS_BY_SCENARIO.items():
        for flag in flags:
            for objective_id in flag.objective_ids:
                assert objective_id in LEARNING_OBJECTIVES, (scenario_id, flag.id, objective_id)


def test_every_flag_has_category_and_evidence_source():
    for flags in FLAGS_BY_SCENARIO.values():
        for flag in flags:
            assert flag.category, flag.id
            assert flag.evidence_source, flag.id


def test_flag_answers_are_never_equal_to_their_own_hints():
    # A hint that accidentally states the literal accepted answer
    # defeats the whole point of a hint. Cheap guard: no accepted
    # answer string should appear verbatim inside its own hint text.
    for flags in FLAGS_BY_SCENARIO.values():
        for flag in flags:
            for hint in flag.hints:
                hint_norm = normalize(hint.text)
                for accepted in flag.accepted:
                    accepted_norm = normalize(accepted)
                    if len(accepted_norm) <= 2:
                        continue  # too short to meaningfully collide-check
                    assert accepted_norm not in hint_norm, (flag.id, accepted)


def test_scenarios_have_phase_2a_training_metadata():
    for scenario in SCENARIOS:
        assert scenario.difficulty in {"Beginner", "Intermediate", "Advanced"}
        assert scenario.estimated_duration
        assert scenario.prerequisites
        assert scenario.primary_skills
        assert scenario.evidence_sources
        assert scenario.recommended_training_mode
        assert scenario.process_impact_rating
        assert scenario.detection_coverage_state


def test_execution_modes_use_friendly_labels_and_keep_fixed_technical_commands():
    friendly_labels = {"Quick Simulation", "Full Monitored Network", "Live PLC Investigation"}
    for scenario in SCENARIOS:
        for mode in scenario.modes:
            assert mode.label in friendly_labels
            assert mode.description
            assert isinstance(mode.command, list)
            assert mode.command
