"""Scenario metadata: the single source of truth for what scenarios exist,
their teaching hooks, and how to run them.

Shared by `scenarios/menu.py` (terminal picker) and `panel/app.py` (web
control panel) so the two never drift out of sync with each other.
"""

from __future__ import annotations

import socket
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

    @property
    def dirname(self) -> Path:
        return next(REPO_ROOT.glob(f"scenarios/{self.id}-*"))


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

SCENARIOS_BY_ID = {s.id: s for s in SCENARIOS}


def docker_stack_is_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 502), timeout=1.0):
            return True
    except OSError:
        return False
