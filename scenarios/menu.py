#!/usr/bin/env python3
"""Interactive picker for the scenario library.

Lists every scenario with a one-line hook, its process impact, and
what catches it, then runs the one you pick. No need to remember
`scenarios/run_scenario.sh` vs `make scenario-S06` vs the -docker
targets — this dispatches to the right one for you.

Usage: scenarios/menu.py
"""

from __future__ import annotations

import socket
import subprocess  # nosec B404 -- literal args only, see the call sites below
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Mode:
    label: str
    command: list[str]
    requires_docker_stack: bool = False


@dataclass
class Scenario:
    id: str
    title: str
    hook: str
    impact: str
    caught_by: str
    modes: list[Mode]


SCENARIOS = [
    Scenario(
        id="S01",
        title="Recon & point enumeration",
        hook="An alert fired overnight. Nothing else did — the operators "
        "swear it was a normal shift. Figure out what the sensor saw.",
        impact="None on the process — that's the lesson: recon looks quiet.",
        caught_by="4 detection rules (unauthorized source, point/unit-ID sweeps, exception spikes)",
        modes=[
            Mode("loopback (fast, self-contained)", ["bash", "scenarios/run_scenario.sh", "S01"]),
            Mode(
                "real network, via router/Zeek/Suricata (requires `make up`)",
                ["make", "scenario-S01-docker"],
                requires_docker_stack=True,
            ),
        ],
    ),
    Scenario(
        id="S03",
        title="Unauthorised command",
        hook="06:42. The tank is overflowing onto the yard, the pump just "
        "faulted, and the operator swears he never touched manual mode.",
        impact="Tank overflows, pump destroys itself running dry/blocked.",
        caught_by="MODBUS_UNAUTHORIZED_WRITE (critical)",
        modes=[
            Mode("loopback (fast, self-contained)", ["bash", "scenarios/run_scenario.sh", "S03"]),
            Mode(
                "real network, via router/Zeek/Suricata (requires `make up`)",
                ["make", "scenario-S03-docker"],
                requires_docker_stack=True,
            ),
        ],
    ),
    Scenario(
        id="S05",
        title="Manipulation of view (flagship)",
        hook="Nothing looks wrong. That's the whole problem — the HMI has "
        "read a calm, steady 50% for the last several minutes.",
        impact="Tank overflows for real while every screen stays calm.",
        caught_by="MODBUS_VIEW_MANIPULATION (critical) — hardwired float "
        "vs. spoofed transmitter, source-independent",
        modes=[
            Mode("loopback (fast, self-contained)", ["bash", "scenarios/run_scenario.sh", "S05"]),
        ],
    ),
    Scenario(
        id="S06",
        title="Logic modification, safety disabled",
        hook="A few seconds of dropped connections at 02:14, then "
        "everything read normal again. Three hours later the tank "
        "overflowed anyway — the interlock that should've stopped it "
        "was just gone.",
        impact="Interlock deleted from the PLC program itself; latent "
        "until the tank reaches high-high with the pump still running.",
        caught_by="Not detected — the compromise is over HTTP (OpenPLC's "
        "web UI), which nothing in this range inspects. That gap is the "
        "scenario's own teaching point.",
        modes=[
            Mode(
                "OpenPLC web UI + Modbus, live (requires `make up`)",
                ["make", "scenario-S06"],
                requires_docker_stack=True,
            ),
        ],
    ),
]


def docker_stack_is_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 502), timeout=1.0):
            return True
    except OSError:
        return False


def prompt(text: str) -> str:
    try:
        return input(text)
    except EOFError:
        return ""


def choose_scenario() -> Scenario | None:
    print("=" * 68)
    print(" OT RANGE — scenario picker")
    print("=" * 68)
    for i, s in enumerate(SCENARIOS, start=1):
        print(f"\n[{i}] {s.id} — {s.title}")
        print(f"    {s.hook}")
        print(f"    impact    : {s.impact}")
        print(f"    caught by : {s.caught_by}")
    print(f"\n[{len(SCENARIOS) + 1}] briefing / answer key only (no attack run)")
    print("[q] quit")

    choice = prompt("\nPick a scenario: ").strip().lower()
    if choice in ("q", "quit", "exit", ""):
        return None
    if not choice.isdigit():
        print("not a number, try again")
        return choose_scenario()
    idx = int(choice)
    if idx == len(SCENARIOS) + 1:
        show_docs_only()
        return choose_scenario()
    if 1 <= idx <= len(SCENARIOS):
        return SCENARIOS[idx - 1]
    print("out of range, try again")
    return choose_scenario()


def show_docs_only() -> None:
    for i, s in enumerate(SCENARIOS, start=1):
        print(f"  [{i}] {s.id}")
    choice = prompt("Which scenario's docs? ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(SCENARIOS)):
        return
    s = SCENARIOS[int(choice) - 1]
    dirname = next(REPO_ROOT.glob(f"scenarios/{s.id}-*"))
    print(f"\nbriefing   : {dirname / 'briefing.md'}")
    print(f"answer key : {dirname / 'answer-key.md'}")
    print(f"detection  : {dirname / 'detection.md'}")
    print(f"impact     : {dirname / 'expected-impact.md'}")


def choose_mode(scenario: Scenario) -> Mode | None:
    if len(scenario.modes) == 1:
        return scenario.modes[0]
    print(f"\n{scenario.id} can run:")
    for i, m in enumerate(scenario.modes, start=1):
        print(f"  [{i}] {m.label}")
    choice = prompt("Pick a mode: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(scenario.modes)):
        print("out of range")
        return None
    return scenario.modes[int(choice) - 1]


def main() -> int:
    scenario = choose_scenario()
    if scenario is None:
        return 0
    mode = choose_mode(scenario)
    if mode is None:
        return 1

    if mode.requires_docker_stack and not docker_stack_is_up():
        print(
            "\n[!] This mode needs the docker-compose stack running "
            "(OpenPLC's Modbus port 502 isn't answering)."
        )
        ans = prompt("    Run `make up` now? [y/N] ").strip().lower()
        if ans != "y":
            print("    Bring up the stack yourself with `make up`, then re-run this.")
            return 1
        result = subprocess.run(["make", "up"], cwd=REPO_ROOT)  # nosec B603 B607
        if result.returncode != 0:
            return result.returncode

    print(f"\n[*] running: {' '.join(mode.command)}\n")
    result = subprocess.run(mode.command, cwd=REPO_ROOT)  # nosec B603 B607
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
