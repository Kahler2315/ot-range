#!/usr/bin/env python3
"""Interactive picker for the scenario library.

Lists every scenario with a one-line hook, its process impact, and
what catches it, then runs the one you pick. No need to remember
`scenarios/run_scenario.sh` vs `make scenario-S06` vs the -docker
targets — this dispatches to the right one for you.

Usage: scenarios/menu.py
"""

from __future__ import annotations

import subprocess  # nosec B404 -- literal args only, see the call sites below
import sys

from scenarios.catalog import REPO_ROOT, SCENARIOS, Mode, Scenario, docker_stack_is_up


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
    dirname = s.dirname
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
