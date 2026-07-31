"""Unit tests for the Cedar Hollow Pump Station physics model.

Pure physics only — no networking, no Modbus. Control-logic behavior
(auto start/stop, the protective interlock) is exercised through
process_sim.server and covered by tests/smoke.sh instead.
"""

from __future__ import annotations

import dataclasses

from process_sim.plant import (
    DEADHEAD_CURRENT_A,
    DEADHEAD_TRIP_S,
    NOMINAL_CURRENT_A,
    Plant,
    PlantState,
    demand_lpm,
)

DT = 5.0


def run(plant: Plant, seconds: float, **commands) -> PlantState:
    steps = int(seconds / DT)
    state = plant.state
    for _ in range(steps):
        state = plant.step(DT, **commands)
    return state


def make_plant(**overrides) -> Plant:
    return Plant(PlantState(**overrides))


def test_pump_fills_tank_when_isolated():
    plant = make_plant(level_pct=50.0)
    state = run(
        plant,
        300,
        pump_run=True,
        valve_open=False,
        cl_run=False,
        pump_speed_pct=75.0,
        cl_dose_setpoint_mg_l=1.2,
    )
    assert state.level_pct > 50.0


def test_valve_drains_tank_when_pump_off():
    plant = make_plant(level_pct=50.0)
    state = run(
        plant,
        300,
        pump_run=False,
        valve_open=True,
        cl_run=False,
        pump_speed_pct=0.0,
        cl_dose_setpoint_mg_l=1.2,
    )
    assert state.level_pct < 50.0


def test_level_never_exceeds_100():
    plant = make_plant(level_pct=95.0)
    state = run(
        plant,
        3000,
        pump_run=True,
        valve_open=True,
        cl_run=True,
        pump_speed_pct=100.0,
        cl_dose_setpoint_mg_l=1.2,
    )
    assert state.level_pct <= 100.0


def test_level_never_below_0():
    plant = make_plant(level_pct=5.0)
    state = run(
        plant,
        3000,
        pump_run=False,
        valve_open=True,
        cl_run=False,
        pump_speed_pct=0.0,
        cl_dose_setpoint_mg_l=1.2,
    )
    assert state.level_pct >= 0.0


def test_lshh_trips_at_threshold():
    below = PlantState(level_pct=97.9)
    above = PlantState(level_pct=98.0)
    assert not below.lshh
    assert above.lshh


def test_lsll_trips_at_threshold():
    above = PlantState(level_pct=3.1)
    below = PlantState(level_pct=3.0)
    assert not above.lsll
    assert below.lsll


def test_forced_pump_eventually_faults():
    plant = make_plant(level_pct=95.0)
    state = run(
        plant,
        DEADHEAD_TRIP_S + 3000,
        pump_run=True,
        valve_open=True,
        cl_run=False,
        pump_speed_pct=100.0,
        cl_dose_setpoint_mg_l=1.2,
    )
    assert state.pump_fault
    assert state.pump_fb is False
    assert state.pump_current_a == 0.0


def test_deadhead_current_ramps_between_nominal_and_trip():
    plant = make_plant(level_pct=100.0)
    state = plant.step(
        DT,
        pump_run=True,
        valve_open=True,
        cl_run=False,
        pump_speed_pct=100.0,
        cl_dose_setpoint_mg_l=1.2,
    )
    assert NOMINAL_CURRENT_A <= state.pump_current_a < DEADHEAD_CURRENT_A
    assert state.deadhead_s > 0.0


def test_deadhead_resets_when_pump_stops():
    plant = make_plant(level_pct=100.0)
    plant.step(
        DT * 10,
        pump_run=True,
        valve_open=True,
        cl_run=False,
        pump_speed_pct=100.0,
        cl_dose_setpoint_mg_l=1.2,
    )
    assert plant.state.deadhead_s > 0.0
    state = plant.step(
        DT,
        pump_run=False,
        valve_open=True,
        cl_run=False,
        pump_speed_pct=100.0,
        cl_dose_setpoint_mg_l=1.2,
    )
    assert state.deadhead_s == 0.0


def test_pump_not_running_has_zero_current():
    plant = make_plant(level_pct=50.0)
    state = plant.step(
        DT,
        pump_run=False,
        valve_open=True,
        cl_run=False,
        pump_speed_pct=75.0,
        cl_dose_setpoint_mg_l=1.2,
    )
    assert state.pump_current_a == 0.0
    assert state.pump_fb is False


def test_chlorine_chases_setpoint_when_dosing():
    plant = make_plant(level_pct=50.0, chlorine_mg_l=0.0)
    state = run(
        plant,
        600,
        pump_run=False,
        valve_open=True,
        cl_run=True,
        pump_speed_pct=0.0,
        cl_dose_setpoint_mg_l=1.5,
    )
    assert 1.0 < state.chlorine_mg_l <= 1.5


def test_chlorine_decays_when_not_dosing():
    plant = make_plant(level_pct=50.0, chlorine_mg_l=1.2)
    state = run(
        plant,
        1800,
        pump_run=False,
        valve_open=True,
        cl_run=False,
        pump_speed_pct=0.0,
        cl_dose_setpoint_mg_l=1.2,
    )
    assert state.chlorine_mg_l < 1.2


def test_demand_curve_peaks_above_baseline():
    midnight = demand_lpm(0.0)
    morning_peak = demand_lpm(0.29)
    evening_peak = demand_lpm(0.79)
    assert morning_peak > midnight
    assert evening_peak > midnight


def test_demand_curve_wraps_across_day_boundary():
    assert demand_lpm(1.29) == demand_lpm(0.29)


def test_deterministic_given_same_commands():
    commands = {
        "pump_run": True,
        "valve_open": True,
        "cl_run": True,
        "pump_speed_pct": 75.0,
        "cl_dose_setpoint_mg_l": 1.2,
    }
    plant_a = make_plant(level_pct=55.0)
    plant_b = make_plant(level_pct=55.0)
    state_a = run(plant_a, 600, **commands)
    state_b = run(plant_b, 600, **commands)
    assert dataclasses.asdict(state_a) == dataclasses.asdict(state_b)
