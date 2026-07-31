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

import re
import textwrap
import time

import pytest

from tests.docker_harness import REPO_ROOT, stack_context

pytestmark = pytest.mark.docker

ST_PROGRAM = str(REPO_ROOT / "plc" / "logic" / "cedar_hollow.st")
S06_PROGRAM = str(REPO_ROOT / "plc" / "logic" / "cedar_hollow_s06_no_interlock.st")


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

    Polls with one persistent Modbus connection inside a single script
    (see test_s06_program_swap... for why: reconnecting fresh on every
    poll proved flaky). Tolerances are loose on purpose — this is a
    behavioral check (did it start near LO and stop near HI), not a
    precise-crossing measurement; that's already proven at the
    Python-physics level in tests/test_plant.py.
    """
    with stack_context(level_pct=38.0, speed=100.0) as stack:
        stack.configure(ST_PROGRAM)

        watch_script = textwrap.dedent(
            """
            from pymodbus.client import ModbusTcpClient
            import time

            c = ModbusTcpClient("127.0.0.1", port=502)
            c.connect()

            started_at = None
            stopped_at = None
            deadline = time.time() + 90

            while time.time() < deadline:
                lt = c.read_input_registers(100, count=1).registers[0] / 100
                run = c.read_coils(800, count=1).bits[0]

                if run and started_at is None:
                    started_at = lt
                if started_at is not None and not run and stopped_at is None:
                    stopped_at = lt
                    break
                time.sleep(1)

            c.close()
            print(f"RESULT started_at={started_at} stopped_at={stopped_at}")
            """
        ).strip()
        result = stack.run_in_openplc_netns(watch_script, timeout=110)
        assert result.returncode == 0, result.stdout + result.stderr

        match = re.search(r"RESULT started_at=(\S+) stopped_at=(\S+)", result.stdout)
        assert match, f"could not parse watch script output:\n{result.stdout}"
        started_at, stopped_at = match.group(1), match.group(2)

        assert started_at != "None", "pump never auto-started"
        assert float(started_at) <= 55, f"pump started well above SP_LVL_LO (40%): {started_at}%"
        assert stopped_at != "None", "pump never auto-stopped after starting"
        assert float(stopped_at) >= 75, f"pump stopped well below SP_LVL_HI (85%): {stopped_at}%"


def test_interlock_fires_independently_of_hi_setpoint(tmp_path):
    """Rung 2, isolated: raise SP_LVL_HI above the interlock threshold so
    rung 1 cannot be what stops the pump — only the hardwired LSHH float
    (rung 2) can. Also confirms rung 3 (alarm) fires at SP_ALM_HH."""
    with stack_context(level_pct=40.0, speed=600.0) as stack:
        stack.configure(ST_PROGRAM)

        watch_script = textwrap.dedent(
            """
            from pymodbus.client import ModbusTcpClient
            import time

            c = ModbusTcpClient("127.0.0.1", port=502)
            c.connect()
            c.write_register(0, 9990)  # SP_LVL_HI = 99.90%, above LSHH's 98%

            alarm_seen = False
            max_level_while_running = 0.0
            stopped_at = None
            over_99_while_running = False
            deadline = time.time() + 90

            while time.time() < deadline:
                lt = c.read_input_registers(100, count=1).registers[0] / 100
                run = c.read_coils(800, count=1).bits[0]
                alarm = c.read_coils(803, count=1).bits[0]

                if alarm:
                    alarm_seen = True
                if run:
                    max_level_while_running = max(max_level_while_running, lt)
                    if lt >= 99:
                        over_99_while_running = True
                elif max_level_while_running > 0 and stopped_at is None:
                    stopped_at = max_level_while_running
                    break
                time.sleep(1)

            c.close()
            print(
                f"RESULT alarm={alarm_seen} stopped_at={stopped_at} "
                f"over_99={over_99_while_running}"
            )
            """
        ).strip()
        result = stack.run_in_openplc_netns(watch_script, timeout=110)
        assert result.returncode == 0, result.stdout + result.stderr

        match = re.search(r"RESULT alarm=(\w+) stopped_at=(\S+) over_99=(\w+)", result.stdout)
        assert match, f"could not parse watch script output:\n{result.stdout}"
        alarm_seen = match.group(1) == "True"
        stopped_at = match.group(2)
        over_99 = match.group(3) == "True"

        # The pump must never reach 99% while running — if it did, rung
        # 1's HI setpoint (99.9%), not the interlock, would eventually be
        # what stopped it, which would not prove rung 2 works at all.
        assert not over_99, (
            "pump still running past 99% — interlock did not fire independently of SP_LVL_HI"
        )
        assert alarm_seen, "ALARM_HORN never annunciated at SP_ALM_HH"
        assert stopped_at != "None" and float(stopped_at) > 90, (
            f"interlock never stopped the pump (stopped_at={stopped_at})"
        )


def test_s06_program_swap_disables_interlock_even_in_auto_mode(tmp_path):
    """S06, the actual attack path: a live PLC running the safe program
    gets the interlock rung removed via a live program swap over
    OpenPLC's web interface — the same route, same default credentials,
    an attacker would use (T0843 Program Download / T0889 Modify
    Program) — and the interlock genuinely stops protecting, in auto
    mode, with no operator action and no mode change. This is the
    opposite proof from test_interlock_fires_independently_of_hi_setpoint:
    there, the interlock fires; here, after the swap, it must not.

    Rung 3 (annunciation) is untouched by the swap, so the alarm must
    still fire — the operator sees a warning, they just no longer get
    the automatic protective action that used to go with it. That gap is
    the whole point of S06.
    """
    # Low speed and a low starting level are deliberate: swap_program()
    # restarts the runtime, which resets SP_LVL_HI to its ST-declared
    # default (85%) until we raise it again below — the restart plus
    # per-step docker-run overhead costs several real seconds, and at a
    # high --speed multiplier that alone is enough sim-time for the
    # level to cross the *old* 85% setpoint before we get to raise it.
    # Plenty of buffer avoids that race entirely instead of chasing it.
    with stack_context(level_pct=20.0, speed=60.0) as stack:
        bring_up = stack.configure(ST_PROGRAM)
        assert bring_up.returncode == 0, bring_up.stdout + bring_up.stderr

        attack = stack.swap_program(S06_PROGRAM)
        assert attack.returncode == 0, attack.stdout + attack.stderr

        # Poll with one persistent Modbus connection inside a single
        # script, not repeated fresh `docker run` + reconnect per poll —
        # the latter proved flaky (reconnecting a new session against
        # OpenPLC's slave port on every single poll occasionally caught
        # a stale/transient coil read that looked like the pump had
        # stopped when it hadn't; verified by hand with a persistent
        # connection, which never showed a false stop).
        watch_script = textwrap.dedent(
            """
            from pymodbus.client import ModbusTcpClient
            import time

            c = ModbusTcpClient("127.0.0.1", port=502)
            c.connect()
            c.write_register(0, 9990)  # SP_LVL_HI = 99.90%, above LSHH's 98%

            max_level_while_running = 0.0
            alarm_seen = False
            stopped_at = None
            deadline = time.time() + 200

            while time.time() < deadline:
                lt = c.read_input_registers(100, count=1).registers[0] / 100
                run = c.read_coils(800, count=1).bits[0]
                lshh = c.read_discrete_inputs(800, count=1).bits[0]
                alarm = c.read_coils(803, count=1).bits[0]

                if alarm:
                    alarm_seen = True
                if run:
                    max_level_while_running = max(max_level_while_running, lt)
                elif max_level_while_running > 0 and stopped_at is None:
                    stopped_at = max_level_while_running

                if lshh and run:
                    break
                time.sleep(1)

            c.close()
            print(
                f"RESULT max_level={max_level_while_running} "
                f"alarm={alarm_seen} stopped_at={stopped_at}"
            )
            """
        ).strip()
        result = stack.run_in_openplc_netns(watch_script, timeout=220)
        assert result.returncode == 0, result.stdout + result.stderr

        match = re.search(r"RESULT max_level=([\d.]+) alarm=(\w+) stopped_at=(\S+)", result.stdout)
        assert match, f"could not parse result from watch script output:\n{result.stdout}"
        max_level = float(match.group(1))
        alarm_seen = match.group(2) == "True"
        stopped_at = match.group(3)

        assert stopped_at == "None", (
            f"pump stopped at {stopped_at}% — something is still protecting the process "
            "after the interlock rung was removed"
        )
        assert max_level >= 98, (
            f"never observed the pump running past the interlock threshold "
            f"(max seen: {max_level}%) — swap may not have taken effect"
        )
        assert alarm_seen, "ALARM_HORN should still annunciate — only the protective action is gone"
