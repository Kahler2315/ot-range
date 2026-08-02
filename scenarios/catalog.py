"""Scenario metadata: the single source of truth for what scenarios exist,
their teaching hooks, and how to run them.

Shared by `scenarios/menu.py` (terminal picker) and `panel/app.py` (web
control panel) so the two never drift out of sync with each other.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Referenced by id from each Scenario's `objectives` and each Flag's
# `objective_ids` (scenarios/flags.py) — one shared vocabulary so a
# flag's objective always resolves to real text, not a dangling string.
LEARNING_OBJECTIVES = {
    "obj-recon": "Distinguish reconnaissance from normal HMI polling",
    "obj-unauth-cmd": "Identify unauthorized Modbus function usage",
    "obj-correlate": "Correlate process behavior with network telemetry",
    "obj-view-manip": "Detect discrepancies between physical state and HMI presentation",
    "obj-logic-mod": "Identify changes to PLC control logic",
    "obj-blind-spot": "Recognize monitoring blind spots",
    "obj-impact": "Explain the physical consequence of a cyber action",
}


@dataclass
class Mode:
    label: str
    description: str
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
    # "informational" | "critical" — drawn from this scenario's own
    # detection severities in docs/coverage-matrix.md, not assigned
    # independently. S01's highest rule is "high", not "critical" —
    # the only scenario with no process impact — everything else here
    # reaches the same physical endpoint (tank overflow) and is rated
    # "critical" in coverage-matrix.md.
    severity: str = "critical"
    objectives: list[str] = field(default_factory=list)
    difficulty: str = "Intermediate"
    estimated_duration: str = "30 to 45 minutes"
    prerequisites: list[str] = field(default_factory=list)
    primary_skills: list[str] = field(default_factory=list)
    evidence_sources: list[str] = field(default_factory=list)
    recommended_training_mode: str = "Independent investigation"
    process_impact_rating: str = "Critical"
    detection_coverage_state: str = "Critical alert"

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
        severity="informational",
        objectives=["obj-recon", "obj-correlate"],
        difficulty="Beginner",
        estimated_duration="20 to 30 minutes",
        prerequisites=["Basic IP networking", "Introductory Modbus concepts"],
        primary_skills=["Modbus traffic analysis", "Reconnaissance triage"],
        evidence_sources=["Modbus capture log", "Detection output", "Expected-impact notes"],
        recommended_training_mode="Independent investigation",
        process_impact_rating="None",
        detection_coverage_state="High-severity alerts",
        modes=[
            Mode(
                "Quick Simulation",
                "Fast local execution using the simplified scenario environment.",
                ["bash", "scenarios/run_scenario.sh", "S01"],
            ),
            Mode(
                "Full Monitored Network",
                "Runs through the routed environment with Zeek and Suricata visibility.",
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
        severity="critical",
        objectives=["obj-unauth-cmd", "obj-correlate", "obj-impact"],
        difficulty="Intermediate",
        estimated_duration="30 to 45 minutes",
        prerequisites=["S01 or equivalent Modbus log familiarity", "Basic control-loop concepts"],
        primary_skills=["Unauthorized command analysis", "Process/network correlation"],
        evidence_sources=["Modbus capture log", "Point map", "PLC logic", "HMI process state"],
        recommended_training_mode="Independent investigation",
        process_impact_rating="Critical",
        detection_coverage_state="Critical alert",
        modes=[
            Mode(
                "Quick Simulation",
                "Fast local execution using the simplified scenario environment.",
                ["bash", "scenarios/run_scenario.sh", "S03"],
            ),
            Mode(
                "Full Monitored Network",
                "Runs through the routed environment with Zeek and Suricata visibility.",
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
        severity="critical",
        objectives=["obj-view-manip", "obj-correlate"],
        difficulty="Advanced",
        estimated_duration="45 to 60 minutes",
        prerequisites=["S01 and S03", "Modbus function-code familiarity"],
        primary_skills=["Manipulation-of-view detection", "Independent sensor correlation"],
        evidence_sources=["HMI", "Hardwired float state", "Modbus capture log", "Detection output"],
        recommended_training_mode="Independent investigation",
        process_impact_rating="Critical",
        detection_coverage_state="Two critical alerts",
        modes=[
            Mode(
                "Quick Simulation",
                "Fast local execution using the simplified scenario environment.",
                ["bash", "scenarios/run_scenario.sh", "S05"],
            ),
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
        severity="critical",
        objectives=["obj-logic-mod", "obj-blind-spot", "obj-impact"],
        difficulty="Advanced",
        estimated_duration="45 to 60 minutes",
        prerequisites=["Basic IEC 61131-3 logic reading", "OpenPLC and Modbus familiarity"],
        primary_skills=["PLC logic integrity review", "Monitoring-gap analysis"],
        evidence_sources=["OpenPLC web UI", "Structured Text diff", "Detection documentation"],
        recommended_training_mode="Guided learning for first attempt",
        process_impact_rating="Critical",
        detection_coverage_state="Monitoring gap — program change not detected",
        modes=[
            Mode(
                "Live PLC Investigation",
                "Uses the running OpenPLC environment and its web and Modbus interfaces.",
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
