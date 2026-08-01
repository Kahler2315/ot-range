# S06 — Detection

## The honest headline: the program-download phase is not detected

This range has no detection for T0843 Program Download or T0889 Modify
Program. Building it means an HTTP-layer sensor watching OpenPLC's web
routes (`/login`, `/upload-program`, `/compile-program`) — the same
blocker already documented for S02's default-credential exfiltration
path in [`coverage-matrix.md`](../../docs/coverage-matrix.md). Nothing
in `sensor/` or `router/` inspects HTTP today; both the M1 tap and the
M4 Zeek/Suricata setup only ever look at Modbus traffic.

This is stated plainly rather than worked around, because it's the
scenario's actual teaching point: **the most damaging step of this
attack happens over a protocol nobody is watching.** A range that
quietly detected it anyway would be teaching the wrong lesson.

## What *is* detected — partial, and only downstream of the real damage

The scenario script's second step — raising `SP_LVL_HI` to 99.90% over
Modbus, so the demo doesn't have to wait on a real fill cycle — is a
plain unauthenticated holding-register write, same shape as every S03
write. Run against a baseline where the attacker's source isn't
allowlisted, the existing rules fire on it exactly the way they fire on
S03:

- `MODBUS_UNAUTHORIZED_SOURCE` (high) — the source isn't in the baseline
  at all.
- `MODBUS_UNAUTHORIZED_WRITE` (critical) — a write function code from
  outside the permitted (source, unit, function, address) tuples.

Not asserted in CI the way S01/S03/S05 are (no `tests/test_attack_
detection.py` entry for S06 yet), but the mechanism is identical to
`test_s03_unauthorized_command_is_detected` and would fire the same way
— confirm it yourself: run the scenario against a capture, then
`.venv/bin/python -m sensor.detect logs/modbus.log`.

**This does not detect the actual attack.** By the time this write
happens, the interlock is already gone — the plant's real defenses were
already compromised through a channel this range can't see at all. If a
real attacker skips the accelerating write (patient enough to wait on
the plant's own operating cycle to reach high-high level naturally, as
described in `expected-impact.md`), there is **no Modbus traffic to
alert on whatsoever.** The program swap is the entire attack, and it's
invisible end to end.

## What real detection would need to look like

- **HTTP-layer visibility into OpenPLC's admin routes** — the missing
  piece, blocking S02 too. Auth from an unexpected source, access
  outside a maintenance window, an upload-program POST at all outside a
  known change window.
- **Program/logic integrity checking** — hash the running program
  periodically (or on every restart) and alert on drift from a known-good
  value. This is the standard real-world answer for T0889 and doesn't
  need protocol-level visibility at all; it needs OpenPLC's own state
  compared against a baseline, which is a different kind of sensor than
  anything built here.
- **Restart/uptime monitoring.** A PLC runtime restart at all is
  unusual outside a planned maintenance window — worth alerting on by
  itself, independent of what changed.

None of these are built. This scenario exists specifically to make that
gap concrete rather than leave it as an abstract line in
`coverage-matrix.md`.
