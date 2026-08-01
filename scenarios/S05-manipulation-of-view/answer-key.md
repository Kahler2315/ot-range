# S05 — Answer key

*Instructor notes. Don't hand this out with the briefing.*

## 1. Do the HMI and the sensor capture agree?

No — and this is the crux of the whole scenario. The HMI shows `LT_101`
holding at 50.0% throughout. The capture, read correctly, shows
`LSHH_101` (the hardwired float) trip true partway through.

*How* to know which to trust, not just which one: `LSHH_101` is a
discrete input driven by a mechanical float switch — its state is a
direct physical fact about water touching a switch, with no software
translation layer between the physical event and the bit on the wire.
`LT_101` is an analog transmitter's *reported* value, which passed
through a reporting mechanism that this attack has already shown can be
subverted. When two measurements of the same fact disagree, trust the one
with fewer places a lie could have been inserted — here, that's the
float, categorically, not just in this specific capture.

## 2. Why can't LT_101 be attacked the way P101_RUN was in S03?

`LT_101` is read via function code 4 (Read Input Registers). The Modbus
protocol defines **no write function code that targets input registers at
all** — 5, 6, 15, and 16 write coils and holding registers only. There is
no such thing as "Write Input Register" in the spec. S03's attack (write
directly to a coil the protocol allows writing to, just not by this
source) is structurally unavailable against `LT_101` — not because of any
access control, but because the protocol itself was never designed to let
anyone write there, authorized or not.

This is worth dwelling on with students: it's the one place in this
scenario where the protocol's design actually helps, and the attacker has
to go around it rather than through it.

## 3. Where did the false reading actually come from?

The field device itself — `process_sim/server.py`'s undocumented
register 99 (`LT101_SPOOF_HR_INDEX`), which overrides what gets written
to the `LT_101` wire value on every tick, upstream of any legitimate
Modbus read. That register isn't part of `plc/modbus-map.yml` and isn't
reachable from any documented operator screen.

The implication students should reach: this isn't a network
man-in-the-middle and isn't a display bug. The *reporting device itself*
has been compromised at a level below the protocol — closer to firmware
or an engineering-level backdoor than to "someone intercepted a packet."
That's a materially worse finding for incident response, because it means
the device can't be trusted even over a clean, unmonitored network path —
the lie originates at the source, not in transit.

## 4. Why doesn't LSHH_101 waver, architecturally?

Because it's on a completely separate signal path from `LT_101` inside
the simulator, not just a separately-named point. `Plant._update_pump_electrical`
and the float properties on `PlantState` (`process_sim/plant.py`) compute
`lshh`/`lsll` straight from the true physics state; nothing about the
spoof register touches that path. In a real plant this maps to a genuine
hardwired float switch on its own terminals, wired straight to a discrete
input, with no microprocessor, no firmware, and no network stack between
the water and the bit — there is no reporting mechanism there to
compromise in the first place.

## 5. The two detections — find both, and which would you rather have

`MODBUS_UNAUTHORIZED_WRITE` fires on the arming write to register 99, at
the very start. `MODBUS_VIEW_MANIPULATION` fires on the LSHH/LT_101
contradiction, continuously, for as long as the lie persists.

The honest answer to "which would you rather have": the cross-consistency
one, and instructors should push students to justify it rather than
accept "both is fine." The write-based alert is a single event that has
to be seen once, at the right moment, in a log that may be busy with
routine traffic. The cross-consistency alert re-evaluates on every poll —
it's available for the entire remainder of the incident, and (per
`detection.md`) it's the one that still works even against a source the
baseline already trusts, which the write rule fundamentally cannot do.

## 6. Why spoof LT_101 at all, if the pump forcing already worked in S03?

Because S03's *impact* was also its *detection* — the moment the pump
started running against setpoint, the level visibly climbed on every
screen, and the operator in the S03 scenario noticed within minutes. S05
asks a different, harder question: what if the attacker also controls
what the operator *sees* while doing it? Forcing the pump without the
spoof is just S03 again — loud, and caught the same way. Adding the
spoof is what turns "an attack that gets noticed" into "an attack that
looks like a normal, quiet shift," which is the actual technique observed
in the 2026 CISA advisory this scenario models: attackers didn't just
issue commands, they specifically manipulated what operators saw while
doing it.

## Common wrong turns

- **"The alarm system failed."** It didn't — `ALARM_HORN` fired
  correctly and on time, because the annunciation logic reads ground
  truth, not the spoofed register. The failure is entirely in what number
  sat next to that alarm on the screen; a student who blames "the alarm"
  has misdiagnosed which component lied.
- **"This is a man-in-the-middle attack."** It isn't, in this scenario as
  built — no traffic is intercepted or altered in transit. The lie is
  injected at the source device, before any legitimate client ever polls
  it. (A real MITM version of this attack is architecturally possible and
  arguably *more* likely in a real deployment; it's just not what this
  specific attack script demonstrates. Worth raising as a discussion
  extension.)
- **"Just monitor LT_101 more closely / add more alarms on it."** Any
  detection built purely on `LT_101`'s own value inherits the same blind
  spot the operator had — it's the value that's being lied about. The
  fix has to come from a second, independent path, not from watching the
  compromised one harder.
- **"Encrypt the Modbus traffic."** Would not have prevented this attack
  at all — the lie is injected before the traffic exists, not read off
  the wire in transit. This is a good moment to distinguish
  confidentiality/integrity-in-transit controls from
  integrity-at-the-source, which is what this scenario actually breaks.
