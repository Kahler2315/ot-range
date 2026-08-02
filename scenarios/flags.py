"""CTF-style flags for each scenario — short, specific, checkable facts
pulled directly from that scenario's own answer-key.md, not invented
separately from it. Answers are validated server-side (panel/app.py)
so they never sit in page source; only prompts, points, hint *costs*
(never hint text) are sent to the client up front. Hint text is
fetched on demand, one level at a time, only when a student actually
reveals it — see panel/app.py's hint endpoint.

Point values and hint costs are not arbitrary: see the point-value
table and hint-cost formula in this project's planning notes (also
summarized in README.md's training section). Hint cost itself is
*computed*, not stored on each Hint, via scenarios.scoring.hint_cost —
one formula, so cost can never drift from what's actually charged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Hint:
    text: str


@dataclass
class Flag:
    id: str
    prompt: str
    accepted: list[str]  # normalized alternatives; see normalize() below
    points: int = 10
    hints: list[Hint] = field(default_factory=list)
    # Process state | Network evidence | Detection evidence | Controller
    # state | Scenario output
    category: str = ""
    evidence_source: str = ""  # where a student would actually go look
    # ids resolve against scenarios/catalog.py's LEARNING_OBJECTIVES
    objective_ids: list[str] = field(default_factory=list)


def normalize(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[.\s]+", " ", text)
    return text.strip().rstrip(".")


def check(flag: Flag, answer: str) -> bool:
    normalized = normalize(answer)
    return any(normalized == normalize(a) for a in flag.accepted)


FLAGS_BY_SCENARIO: dict[str, list[Flag]] = {
    "S01": [
        Flag(
            "s01-hosts",
            "How many distinct hosts spoke Modbus during the capture?",
            ["2", "two"],
            points=6,
            category="Network evidence",
            evidence_source="logs/modbus.log",
            objective_ids=["obj-recon"],
            hints=[
                Hint(
                    "Look at who's actually talking Modbus during the capture — "
                    "how many distinct source addresses show up making requests?"
                ),
                Hint(
                    "Filter the log for request PDUs and count distinct sources: "
                    '`grep \'"pdu_type": "request"\' logs/modbus.log | '
                    "jq -r '.\"id.orig_h\"' | sort | uniq -c`"
                ),
            ],
        ),
        Flag(
            "s01-source",
            "What is the attacker's source IP address?",
            ["127.0.0.2"],
            points=5,
            category="Network evidence",
            evidence_source="logs/modbus.log",
            objective_ids=["obj-recon"],
            hints=[
                Hint("It's whichever address isn't the HMI's."),
                Hint(
                    "The HMI's baselined source reads a fixed set of 18 points on a "
                    "steady interval. Whichever address reads far more than that, "
                    "and isn't the HMI's own, is the attacker."
                ),
            ],
        ),
        Flag(
            "s01-writes",
            "How many write function codes (5, 6, 15, 16) appear anywhere "
            "in the attacker's traffic?",
            ["0", "none", "zero", "no writes"],
            points=8,
            category="Network evidence",
            evidence_source="logs/modbus.log",
            objective_ids=["obj-recon", "obj-unauth-cmd"],
            hints=[
                Hint(
                    "Check the *function codes* the attacker used, not just which "
                    "addresses they touched — Modbus separates reads from writes "
                    "by function code number."
                ),
                Hint(
                    "Write function codes are 5, 6, 15, 16 (23 for read/write). "
                    "Filter the attacker's traffic for those: `jq -r "
                    '\'select(."id.orig_h"=="127.0.0.2" and .pdu_type=="request") '
                    "| .func' logs/modbus.log | sort | uniq -c`"
                ),
            ],
        ),
        Flag(
            "s01-exception",
            "What Modbus exception comes back when the attacker probes an "
            "address past what the device implements?",
            ["illegal data address", "illegal_data_address"],
            points=7,
            category="Detection evidence",
            evidence_source="sensor/detect.py output",
            objective_ids=["obj-recon"],
            hints=[
                Hint(
                    "The attacker's sweep asks for addresses the device doesn't "
                    "implement. Modbus has a specific exception response for that."
                ),
                Hint(
                    "Look at the sensor's detect output or the log's exception "
                    "field for responses to out-of-range addresses — the name "
                    "follows the pattern ILLEGAL_<WHAT>_<PROBLEM>."
                ),
            ],
        ),
        Flag(
            "s01-escalate",
            "True or false: since nothing alarmed and nothing changed, "
            "this incident isn't worth escalating.",
            ["false"],
            points=9,
            category="Scenario output",
            evidence_source="expected-impact.md",
            objective_ids=["obj-correlate"],
            hints=[
                Hint(
                    "Re-read the briefing's closing question about whether 'no "
                    "impact' means 'no incident' — what does this scenario's own "
                    "expected-impact.md argue?"
                ),
                Hint(
                    "expected-impact.md's own words: 'absence of process impact "
                    "is not evidence of absence of intrusion.' What does that "
                    "imply about whether recon-only traffic is worth escalating?"
                ),
            ],
        ),
    ],
    "S03": [
        Flag(
            "s03-mode-coil",
            "Which coil address does the attacker write first — the one "
            "that drops the plant out of automatic control?",
            ["4"],
            points=6,
            category="Network evidence",
            evidence_source="logs/modbus.log",
            objective_ids=["obj-unauth-cmd"],
            hints=[
                Hint(
                    "Look at the very first write in the capture, before the pump "
                    "write — what does the attacker turn off first, and why?"
                ),
                Hint(
                    "Filter for write requests in order: `jq -r 'select(.pdu_type"
                    '=="request" and .is_write==true) | "\\(.ts) \\(."id.orig_h") '
                    "\\(.func) addr=\\(.address)\"' logs/modbus.log` — the address "
                    "written first is the one that matters here."
                ),
            ],
        ),
        Flag(
            "s03-pump-coil",
            "Which coil address commands the fill pump on?",
            ["0"],
            points=6,
            category="Network evidence",
            evidence_source="logs/modbus.log",
            objective_ids=["obj-unauth-cmd"],
            hints=[
                Hint(
                    "The second write in the sequence is the one that actually "
                    "starts equipment moving."
                ),
                Hint(
                    "Same write-request filter as the mode-coil flag — the second "
                    "address written, right after the mode coil, is the pump "
                    "command."
                ),
            ],
        ),
        Flag(
            "s03-mode-tag",
            "What tag name is coil address 4?",
            ["mode_auto", "mode auto"],
            points=7,
            category="Controller state",
            evidence_source="plc/modbus-map.yml",
            objective_ids=["obj-unauth-cmd"],
            hints=[
                Hint(
                    "You already have the coil address from the previous flag — "
                    "now match it to a human-readable tag name."
                ),
                Hint(
                    "Check plc/modbus-map.yml's coils table for the tag whose "
                    "addr matches the mode-selector coil you already found."
                ),
            ],
        ),
        Flag(
            "s03-why-no-stop",
            "The automatic high-high interlock didn't stop the pump "
            "because the plant was in which mode?",
            ["manual"],
            points=9,
            category="Controller state",
            evidence_source="plc/logic/cedar_hollow.st",
            objective_ids=["obj-correlate"],
            hints=[
                Hint(
                    "The interlock rung didn't fail — it's *gated* on something. "
                    "What condition has to be true for it to act at all?"
                ),
                Hint(
                    "Both the level-control rung and the high-high interlock rung "
                    "are conditioned on the plant's mode-selector coil — the same "
                    "one you found the tag name for in the previous flag. "
                    "Whichever of its two states is *not* automatic is the mode "
                    "the plant was in."
                ),
            ],
        ),
        Flag(
            "s03-what-stops-it",
            "What ultimately stops the pump — name the physical protection, not a network control.",
            ["thermal overload", "thermal", "overload"],
            points=10,
            category="Process state",
            evidence_source="expected-impact.md / HMI",
            objective_ids=["obj-impact"],
            hints=[
                Hint(
                    "Something eventually halts the pump — but it isn't a "
                    "network control or an operator command. Think about what "
                    "happens physically when a pump has nowhere to send its flow."
                ),
                Hint(
                    "expected-impact.md walks through it: current climbs from "
                    "nominal toward a much higher value as the pump 'deadheads' "
                    "against the full tank, and a protective trip latches out "
                    "before the motor is damaged. Name that protection."
                ),
            ],
        ),
    ],
    "S05": [
        Flag(
            "s05-hmi-value",
            "What value does the HMI display for LT_101 throughout the incident?",
            ["50", "50%", "50.0", "50.0%"],
            points=5,
            category="Process state",
            evidence_source="HMI",
            objective_ids=["obj-view-manip"],
            hints=[
                Hint(
                    "Watch LT_101 on the HMI (or the reported log field) for the "
                    "whole incident — does it ever move?"
                ),
                Hint(
                    "The spoof holds LT_101 at one fixed percentage the entire "
                    "time, regardless of what the tank is actually doing. Read "
                    "the frozen value directly off the HMI display."
                ),
            ],
        ),
        Flag(
            "s05-ground-truth",
            "Which point is the ground truth that exposes the lie — the "
            "one that can't be spoofed over Modbus?",
            ["lshh_101", "lshh"],
            points=8,
            category="Process state",
            evidence_source="HMI / LSHH_101",
            objective_ids=["obj-view-manip"],
            hints=[
                Hint(
                    "One point in this plant is on a completely separate "
                    "physical path from the network-facing transmitter — no "
                    "microprocessor, no firmware, between the water and the bit."
                ),
                Hint(
                    "It's the hardwired high-high float switch, not the analog "
                    "level transmitter. What tag name is that float?"
                ),
            ],
        ),
        Flag(
            "s05-function-code",
            "What Modbus function code is LT_101 read with?",
            ["4", "fc4", "fc 4"],
            points=8,
            category="Network evidence",
            evidence_source="logs/modbus.log",
            objective_ids=["obj-view-manip"],
            hints=[
                Hint("It's an input register — see detection.md's protocol note."),
                Hint(
                    "Input registers are read with function code 4 (Read Input "
                    "Registers), and the protocol defines no write function code "
                    "that targets them at all — that's *why* this point can't be "
                    "attacked the same direct way S03's coil was."
                ),
            ],
        ),
        Flag(
            "s05-detection-rule",
            "Which detection rule fires continuously for as long as the "
            "lie persists, not just once at the start?",
            ["modbus_view_manipulation"],
            points=7,
            category="Detection evidence",
            evidence_source="sensor/detect.py output",
            objective_ids=["obj-correlate"],
            hints=[
                Hint(
                    "Two rules fire in this scenario at different points — you "
                    "want the one that keeps firing as long as the lie "
                    "continues, not the one-time write alert."
                ),
                Hint(
                    "It's the rule that cross-checks the hardwired float against "
                    "the analog transmitter's reported value — check "
                    "detection.md's second rule heading for its exact name."
                ),
            ],
        ),
        Flag(
            "s05-injection-point",
            "Where is the fake reading actually injected — the network, "
            "or the field device itself? (one or two words)",
            ["field device", "the field device", "device", "field-device"],
            points=12,
            category="Controller state",
            evidence_source="process_sim/server.py behavior",
            objective_ids=["obj-view-manip", "obj-blind-spot"],
            hints=[
                Hint(
                    "Ask where the false value actually gets written into the "
                    "data path — is it altered somewhere in transit, or is it "
                    "wrong from the moment it's created?"
                ),
                Hint(
                    "detection.md and expected-impact.md both point to the same "
                    "place: an undocumented register in process_sim/server.py "
                    "(LT101_SPOOF_HR_INDEX) overrides the reported value before "
                    "any legitimate read ever happens — nothing is intercepted "
                    "in transit. Name the general category that place belongs "
                    "to, contrasted with 'network'."
                ),
            ],
        ),
    ],
    "S06": [
        Flag(
            "s06-creds",
            "What default credential did the attacker use to log into "
            "OpenPLC's web UI? (user/pass)",
            ["openplc/openplc", "openplc / openplc"],
            points=5,
            category="Controller state",
            evidence_source="OpenPLC web UI",
            objective_ids=["obj-logic-mod"],
            hints=[
                Hint(
                    "The attacker didn't guess or crack anything — they used the "
                    "credential OpenPLC ships with, unchanged."
                ),
                Hint(
                    "It's documented in OpenPLC's own public setup instructions "
                    "and in this range's SECURITY.md — the username and "
                    "password are the same word."
                ),
            ],
        ),
        Flag(
            "s06-lines-removed",
            "How many lines of *functional* logic (the real interlock "
            "rung) were actually removed from the ladder program?",
            ["4", "four"],
            points=9,
            category="Controller state",
            evidence_source="plc/logic/*.st diff",
            objective_ids=["obj-logic-mod"],
            hints=[
                Hint(
                    "Diff the two ladder-logic files, but be careful — a raw "
                    "diff shows more changed than actually matters functionally."
                ),
                Hint(
                    "`diff plc/logic/cedar_hollow.st "
                    "plc/logic/cedar_hollow_s06_no_interlock.st` shows comment "
                    "rewrites too. The *functional* change is only rung 2 — a "
                    "short IF block. Count just those lines."
                ),
            ],
        ),
        Flag(
            "s06-alarm-rung",
            "Annunciation (ALARM_HORN) runs on a separate rung from the "
            "interlock. Which rung number is it?",
            ["3", "rung 3", "three"],
            points=7,
            category="Controller state",
            evidence_source="plc/logic/cedar_hollow.st",
            objective_ids=["obj-logic-mod"],
            hints=[
                Hint(
                    "The alarm and the interlock are driven by two different "
                    "rungs in the ladder program — only one of them was touched."
                ),
                Hint(
                    "Open plc/logic/cedar_hollow.st and find the rung comments: "
                    "annunciation is a separate, untouched rung from the "
                    "interlock rung that was deleted. Which rung number is it?"
                ),
            ],
        ),
        Flag(
            "s06-ids-visible",
            "Was this attack visible to a signature-based network IDS "
            "watching Modbus traffic? (yes/no)",
            ["no"],
            points=9,
            category="Detection evidence",
            evidence_source="detection.md",
            objective_ids=["obj-blind-spot"],
            hints=[
                Hint(
                    "Think about which protocol the compromise itself happens "
                    "over, and whether anything in this range's sensor stack "
                    "inspects that protocol."
                ),
                Hint(
                    "The compromise is entirely over HTTP to OpenPLC's web UI "
                    "(login, upload-program, compile-program). Check "
                    "detection.md's opening section — does sensor/ or router/ "
                    "inspect HTTP at all?"
                ),
            ],
        ),
        Flag(
            "s06-persists",
            "Unlike S03, does this attack's effect persist even after "
            "the attacker's network access is revoked? (yes/no)",
            ["yes"],
            points=12,
            category="Scenario output",
            evidence_source="answer-key.md comparison table",
            objective_ids=["obj-impact", "obj-blind-spot"],
            hints=[
                Hint(
                    "Compare S03 versus S06 if the attacker's network access "
                    "gets cut off right now — does the plant's behavior revert "
                    "in both cases?"
                ),
                Hint(
                    "answer-key.md's comparison table spells it out: S03's "
                    "writes stop working and the plant reverts on the next "
                    "control scan once access is revoked. S06's malicious "
                    "*program* keeps running regardless, because it was never a "
                    "live command — it's the configuration itself."
                ),
            ],
        ),
    ],
}
