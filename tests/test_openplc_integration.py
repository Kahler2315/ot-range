"""OpenPLC integration tests — the M1.5 verification.

Every test here stands up the real OpenPLC image (built from pinned
source, plc/openplc/Dockerfile) and the real process-sim field-only
container, wires them together with the same tools/openplc_configure.py
used for real bring-up, and drives the *real* compiled ST program
(plc/logic/cedar_hollow.st) — not a mock, not a re-implementation.

Slow (image build on first run, then container startup + compile per
test) and requires Docker, so these are marked `docker` and auto-skipped
when Docker isn't reachable (see conftest.py). Run explicitly with
`pytest -m docker`.
"""

from __future__ import annotations

import textwrap
import time

import pytest

from tests.docker_harness import REPO_ROOT, stack_context

pytestmark = pytest.mark.docker

ST_PROGRAM = str(REPO_ROOT / "plc" / "logic" / "cedar_hollow.st")


def _read_state_script() -> str:
    """A tiny inline script run inside the openplc netns to read process
    state through OpenPLC's own Modbus slave interface. Offset-100
    located variables land at input register 100+ / discrete+coil bit
    800+ per docs/openplc-integration.md."""
    return textwrap.dedent(
        """
        from pymodbus.client import ModbusTcpClient
        c = ModbusTcpClient("127.0.0.1", port=502)
        c.connect()
        lt = c.read_input_registers(100, count=1).registers[0]
        run = c.read_coils(800, count=1).bits[0]
        fb = c.read_discrete_inputs(802, count=1).bits[0]
        lshh = c.read_discrete_inputs(800, count=1).bits[0]
        alarm = c.read_coils(803, count=1).bits[0]
        print(f"LT_101={lt} P101_RUN={run} P101_FB={fb} LSHH={lshh} ALARM={alarm}")
        c.close()
        """
    ).strip()


def _parse_state(stdout: str) -> dict:
    line = [ln for ln in stdout.splitlines() if ln.startswith("LT_101=")][-1]
    state = {}
    for field in line.split():
        key, value = field.split("=")
        state[key] = value
    return {
        "level_pct": int(state["LT_101"]) / 100,
        "pump_run": state["P101_RUN"] == "True",
        "pump_fb": state["P101_FB"] == "True",
        "lshh": state["LSHH"] == "True",
        "alarm": state["ALARM"] == "True",
    }


def test_openplc_boots_and_runs_cedar_hollow(tmp_path):
    """The baseline: image builds, container boots, program uploads and
    compiles, PLC starts, and it's actually polling the field device."""
    with stack_context(level_pct=55.0, speed=60.0) as stack:
        result = stack.configure(ST_PROGRAM)
        assert result.returncode == 0, result.stdout + result.stderr

        time.sleep(2)
        read = stack.run_in_openplc_netns(_read_state_script())
        assert read.returncode == 0, read.stdout + read.stderr
        state = _parse_state(read.stdout)
        # Level near the 55% start — proves OpenPLC is polling live data,
        # not stale/default zeros.
        assert 40 < state["level_pct"] < 70


def test_auto_start_and_stop_at_setpoints(tmp_path):
    """Rung 1: starting just below SP_LVL_LO, the pump must auto-start
    and then auto-stop again once it crosses SP_LVL_HI — driven entirely
    by the compiled ST program, not any Python control logic.

    Tolerances here are loose on purpose: each poll is a fresh `docker
    run` (real wall-clock seconds), and at any --speed multiplier that
    translates to a lot of sim-time between samples — this is a
    behavioral check (did it start near LO and stop near HI), not a
    precise-crossing measurement. Precise crossing behavior is already
    proven at the Python-physics level in tests/test_plant.py.
    """
    with stack_context(level_pct=38.0, speed=100.0) as stack:
        stack.configure(ST_PROGRAM)

        started = False
        stopped_after_start = False
        deadline = time.time() + 90
        while time.time() < deadline:
            read = stack.run_in_openplc_netns(_read_state_script())
            state = _parse_state(read.stdout)
            if state["pump_run"] and not started:
                started = True
                assert state["level_pct"] <= 55, (
                    f"pump started well above SP_LVL_LO (40%): {state['level_pct']}%"
                )
            if started and not state["pump_run"]:
                stopped_after_start = True
                assert state["level_pct"] >= 75, (
                    f"pump stopped well below SP_LVL_HI (85%): {state['level_pct']}%"
                )
                break

        assert started, "pump never auto-started"
        assert stopped_after_start, "pump never auto-stopped after starting"


def test_interlock_fires_independently_of_hi_setpoint(tmp_path):
    """Rung 2, isolated: raise SP_LVL_HI above the interlock threshold so
    rung 1 cannot be what stops the pump — only the hardwired LSHH float
    (rung 2) can. Also confirms rung 3 (alarm) fires at SP_ALM_HH."""
    with stack_context(level_pct=40.0, speed=600.0) as stack:
        stack.configure(ST_PROGRAM)

        raise_setpoint = textwrap.dedent(
            """
            from pymodbus.client import ModbusTcpClient
            c = ModbusTcpClient("127.0.0.1", port=502)
            c.connect()
            c.write_register(0, 9990)  # SP_LVL_HI = 99.90%, above LSHH's 98%
            c.close()
            """
        ).strip()
        result = stack.run_in_openplc_netns(raise_setpoint)
        assert result.returncode == 0, result.stdout + result.stderr

        alarm_seen = False
        interlock_stopped = False
        deadline = time.time() + 90
        last_level = None
        while time.time() < deadline:
            read = stack.run_in_openplc_netns(_read_state_script())
            state = _parse_state(read.stdout)
            last_level = state["level_pct"]

            if state["alarm"]:
                alarm_seen = True

            # The pump must never reach 99% while running — if it did,
            # rung 1's HI setpoint (99.9%) rather than the interlock
            # would be the thing that (eventually) stopped it, which
            # would not prove rung 2 works at all.
            if state["pump_run"]:
                assert state["level_pct"] < 99, (
                    "pump still running past 99% — interlock did not fire "
                    "independently of SP_LVL_HI"
                )

            if not state["pump_run"] and state["level_pct"] > 90:
                interlock_stopped = True
                break
            time.sleep(1)

        assert alarm_seen, "ALARM_HORN never annunciated at SP_ALM_HH"
        assert interlock_stopped, f"interlock never stopped the pump (last level={last_level})"
