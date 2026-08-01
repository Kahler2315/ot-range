# S05 — Detection

Two independent detections fire, at very different points in the attack,
by two structurally different mechanisms. That's the design, not a
coincidence — see "why two" below.

## MODBUS_UNAUTHORIZED_WRITE (critical) — fires first

*ATT&CK: T0855 Unauthorized Command Message*

The write that arms the spoof (register 99) is outside the published
point map's address range, from a source the baseline doesn't know. This
is the *same rule and mechanism* S03 is caught by — a write outside the
allowlist. It fires the instant the attack starts, before the process has
moved at all.

Asserted in CI by
`tests/test_attack_detection.py::test_s05_initial_spoof_write_is_also_caught_by_the_baseline_rule`.

## MODBUS_VIEW_MANIPULATION (critical) — fires once the lie diverges from reality

*ATT&CK: T0856 Spoof Reporting Message*

`sensor/detect.py`'s `_view_manipulation` rule, added for this scenario.
Cross-checks two independent descriptions of the same physical fact:
`LSHH_101` (hardwired float, function code 2, address 0) and `LT_101`
(analog transmitter, function code 4, address 0). If the float has
tripped true while the transmitter reports a comfortable level (below
`view_manipulation_max_pct`, 90% by default), that's a physical
contradiction — the two readings cannot both be true, and the float is
the one that structurally can't be lying.

Asserted in CI by
`tests/test_attack_detection.py::test_s05_manipulation_of_view_is_detected`.

### Why this rule doesn't care about source or baseline

Every other rule in this range answers "who did something unexpected."
This one answers a different question entirely: "are these two facts
about the physical world consistent with each other," regardless of who
asked for either one. `tests/test_detect.py::test_view_manipulation_fires_even_from_an_allowlisted_source`
pins this explicitly — the rule fires even when both reads come from the
HMI's own baselined address, because the contradiction is in the *values*,
not the *source*.

That's what makes it catch something the allowlist-based rules
structurally cannot: a read from a legitimate, expected source, asking
for exactly the point it's supposed to ask for, on schedule, getting back
a false answer. Nothing about that read looks anomalous by source,
function code, or address. Only the content is wrong.

### Alert content

```
[CRITICAL] MODBUS_VIEW_MANIPULATION  (S05)
           Physical contradiction: the hardwired high-high float
           (LSHH_101) tripped while the analog transmitter (LT_101)
           reported 50.0% — these cannot both be true. LT_101 is being
           spoofed.
           ATT&CK: T0856 Spoof Reporting Message
```

## Why two detections, at two different points

The write-based alert is earlier and cheaper to check, but it's also
easier to miss in practice — it's a single write, once, that can get
buried in a busy log, and if an environment's sensor coverage started
recording *after* that moment (a gap, a restart, a segment the tap
doesn't see), there's nothing left to catch it on. The cross-consistency
alert doesn't have that weakness: it re-evaluates on every single poll
for as long as the lie continues, so it's available for the entire
remainder of the incident, not just the opening seconds.

Relying on either one alone is a real risk profile to name explicitly:
source/baseline rules catch the *access*, physical cross-checks catch the
*deception*. An attacker sophisticated enough to also spoof from an
already-trusted source (a compromised HMI, not an external attacker
address) defeats every rule in this range except the cross-consistency
one — which is the entire argument for building it in the first place.

## What this scenario cannot be detected by

**Not by watching `LT_101` alone, ever, at any layer.** A historian
trending `LT_101` shows the same flat, reassuring line the HMI does. A
SIEM correlating "read from known-good source, to known-good address,
value in normal range" sees nothing unusual — because by every metric
that description is designed to check, nothing *is* unusual. The lie is
specifically engineered to be invisible to exactly that kind of check.

**Not by the write rule alone, reliably.** It catches the arming write,
once, if you're watching at that exact moment. It says nothing about the
minutes or hours the lie continues afterward.

**Only by having a second, independent path to the same physical fact,
and a rule that actually compares them.** That's the structural reason
this scenario needed the hardwired float in the design from the start
(`docs/architecture.md`, `plc/modbus-map.yml`) — not as a nice-to-have
sensor, but as the only thing in this architecture that a network-level
compromise of the reporting device cannot touch.
