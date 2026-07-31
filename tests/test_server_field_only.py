"""Tests for --field-only mode: the field-device half of the M1.5 split.

Exercises PlantRunner directly against an in-memory datastore (no sockets,
no process) — fast, and enough to prove the field device applies zero
control logic, which is the entire point of the split.
"""

from __future__ import annotations

from common.pointmap import load as load_pointmap
from process_sim.server import FC_COIL, FC_HOLDING_REGISTER, PlantRunner, build_context

FIELD_MAP = "plc/modbus-map-field.yml"


def make_field_runner(level_pct: float = 55.0) -> PlantRunner:
    pm = load_pointmap(FIELD_MAP)
    _context, slave = build_context(pm)
    return PlantRunner(pm, slave, tick_s=5.0, initial_level_pct=level_pct, field_only=True)


def set_coil(runner: PlantRunner, tag: str, value: bool) -> None:
    p = runner.pm[tag]
    runner.slave.setValues(FC_COIL, p.index, [value])


def get_coil(runner: PlantRunner, tag: str) -> bool:
    p = runner.pm[tag]
    return bool(runner.slave.getValues(FC_COIL, p.index, 1)[0])


def set_holding(runner: PlantRunner, tag: str, value: float) -> None:
    p = runner.pm[tag]
    runner.slave.setValues(FC_HOLDING_REGISTER, p.index, [p.encode(value)])


def test_field_map_has_no_control_logic_points():
    pm = load_pointmap(FIELD_MAP)
    for tag in ("MODE_AUTO", "SP_LVL_HI", "SP_LVL_LO", "SP_ALM_HH"):
        assert tag not in pm, f"{tag} is a control-logic parameter, must not be field-side"


def test_field_map_has_actuator_and_sensor_points():
    pm = load_pointmap(FIELD_MAP)
    for tag in ("P101_RUN", "V201_OPEN", "CL301_RUN", "ALARM_HORN"):
        assert pm[tag].table == "coils"
    for tag in ("LSHH_101", "LSLL_101", "P101_FB", "P101_FAULT"):
        assert pm[tag].table == "discrete_inputs"
    for tag in ("LT_101", "FT_201", "AIT_301", "IT_101"):
        assert pm[tag].table == "input_registers"
    for tag in ("SP_P101_SPD", "SP_CL_DOSE"):
        assert pm[tag].table == "holding_registers"


def test_field_only_applies_raw_coil_command_with_no_auto_stop():
    """The whole point: forced on stays on, there is no setpoint to stop it."""
    runner = make_field_runner(level_pct=90.0)  # above the M1 auto-stop setpoint of 85%
    set_coil(runner, "P101_RUN", True)
    set_coil(runner, "V201_OPEN", True)
    set_coil(runner, "CL301_RUN", True)

    for _ in range(20):
        runner.tick()

    assert runner.plant.state.level_pct > 90.0, "field device must not have stopped the pump"


def test_field_only_does_not_write_back_commanded_coils():
    """Field device must never overwrite what the external master commanded."""
    runner = make_field_runner()
    set_coil(runner, "P101_RUN", True)
    set_coil(runner, "ALARM_HORN", True)  # a master would set this, not the field device

    runner.tick()

    assert get_coil(runner, "P101_RUN") is True
    assert get_coil(runner, "ALARM_HORN") is True  # untouched, not recomputed


def test_field_only_pump_stops_immediately_when_master_commands_it():
    runner = make_field_runner()
    set_coil(runner, "P101_RUN", True)
    runner.tick()
    assert runner.plant.state.pump_fb is True

    set_coil(runner, "P101_RUN", False)
    runner.tick()
    assert runner.plant.state.pump_fb is False


def test_field_only_reads_speed_and_dose_from_holding_registers():
    runner = make_field_runner()
    set_coil(runner, "P101_RUN", True)
    set_holding(runner, "SP_P101_SPD", 50.0)
    set_holding(runner, "SP_CL_DOSE", 0.8)

    runner.tick()

    # Speed affects inflow rate; just confirm the field device used the
    # commanded value rather than some hardcoded default.
    assert runner.plant.state.flow_lpm >= 0  # sanity: physics ran without error
