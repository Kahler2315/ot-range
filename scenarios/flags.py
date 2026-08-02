"""CTF-style flags for each scenario — short, specific, checkable facts
pulled directly from that scenario's own answer-key.md, not invented
separately from it. Answers are validated server-side (panel/app.py)
so they never sit in page source; only the prompts are ever sent to
the client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Flag:
    id: str
    prompt: str
    accepted: list[str]  # normalized alternatives; see normalize() below
    hint: str = ""


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
        ),
        Flag(
            "s01-source",
            "What is the attacker's source IP address?",
            ["127.0.0.2"],
            hint="It's whichever address isn't the HMI's.",
        ),
        Flag(
            "s01-writes",
            "How many write function codes (5, 6, 15, 16) appear anywhere "
            "in the attacker's traffic?",
            ["0", "none", "zero", "no writes"],
        ),
        Flag(
            "s01-exception",
            "What Modbus exception comes back when the attacker probes an "
            "address past what the device implements?",
            ["illegal data address", "illegal_data_address"],
        ),
        Flag(
            "s01-escalate",
            "True or false: since nothing alarmed and nothing changed, "
            "this incident isn't worth escalating.",
            ["false"],
        ),
    ],
    "S03": [
        Flag(
            "s03-mode-coil",
            "Which coil address does the attacker write first — the one "
            "that drops the plant out of automatic control?",
            ["4"],
        ),
        Flag(
            "s03-pump-coil",
            "Which coil address commands the fill pump on?",
            ["0"],
        ),
        Flag(
            "s03-mode-tag",
            "What tag name is coil address 4?",
            ["mode_auto", "mode auto"],
            hint="Check plc/modbus-map.yml, or the briefing's setup.",
        ),
        Flag(
            "s03-why-no-stop",
            "The automatic high-high interlock didn't stop the pump "
            "because the plant was in which mode?",
            ["manual"],
        ),
        Flag(
            "s03-what-stops-it",
            "What ultimately stops the pump — name the physical protection, not a network control.",
            ["thermal overload", "thermal", "overload"],
        ),
    ],
    "S05": [
        Flag(
            "s05-hmi-value",
            "What value does the HMI display for LT_101 throughout the incident?",
            ["50", "50%", "50.0", "50.0%"],
        ),
        Flag(
            "s05-ground-truth",
            "Which point is the ground truth that exposes the lie — the "
            "one that can't be spoofed over Modbus?",
            ["lshh_101", "lshh"],
        ),
        Flag(
            "s05-function-code",
            "What Modbus function code is LT_101 read with?",
            ["4", "fc4", "fc 4"],
            hint="It's an input register — see detection.md's protocol note.",
        ),
        Flag(
            "s05-detection-rule",
            "Which detection rule fires continuously for as long as the "
            "lie persists, not just once at the start?",
            ["modbus_view_manipulation"],
        ),
        Flag(
            "s05-injection-point",
            "Where is the fake reading actually injected — the network, "
            "or the field device itself? (one or two words)",
            ["field device", "the field device", "device", "field-device"],
        ),
    ],
    "S06": [
        Flag(
            "s06-creds",
            "What default credential did the attacker use to log into "
            "OpenPLC's web UI? (user/pass)",
            ["openplc/openplc", "openplc / openplc"],
        ),
        Flag(
            "s06-lines-removed",
            "How many lines of *functional* logic (the real interlock "
            "rung) were actually removed from the ladder program?",
            ["4", "four"],
        ),
        Flag(
            "s06-alarm-rung",
            "Annunciation (ALARM_HORN) runs on a separate rung from the "
            "interlock. Which rung number is it?",
            ["3", "rung 3", "three"],
            hint="See plc/logic/cedar_hollow.st's rung comments.",
        ),
        Flag(
            "s06-ids-visible",
            "Was this attack visible to a signature-based network IDS "
            "watching Modbus traffic? (yes/no)",
            ["no"],
        ),
        Flag(
            "s06-persists",
            "Unlike S03, does this attack's effect persist even after "
            "the attacker's network access is revoked? (yes/no)",
            ["yes"],
        ),
    ],
}
