# S03 — Answer key

*Instructor notes. Don't hand this out with the briefing.*

## 1. Timeline

```bash
jq -r 'select(.pdu_type=="request" and .is_write==true)
       | "\(.ts) \(."id.orig_h") \(.func) addr=\(.address)"' logs/modbus.log
```

First hostile action: `WRITE_SINGLE_COIL` to address 4 (`MODE_AUTO` ← 0)
from `127.0.0.2`. Immediately after: `WRITE_SINGLE_COIL` to address 0
(`P101_RUN` ← 1), then that same write repeated on a loop for the rest of
the incident.

Last normal action: the HMI's read poll immediately preceding, which
continues *throughout* the attack unchanged — the HMI keeps polling
normally the whole time the plant is being destroyed.

## 2. Why didn't the control logic stop the pump?

Because the attacker turned the control logic off first.

Rung 1 (level control) and rung 2 (the high-high interlock) are both
gated on `MODE_AUTO`. Setting that coil to 0 puts the plant in manual,
where by design the operator's command is authoritative and the automatic
stop logic stands down. The interlock did not fail — it was never
consulted.

This models real plants honestly: manual override that defeats automatic
protective action is a genuine and common design, because operators need
to be able to run equipment during maintenance. The lesson is that the
safety of that arrangement depends entirely on *only authorised people
being able to select manual*, and Modbus offers no way to enforce that.

Worth drawing out: this is exactly the seam S06 attacks differently.
There, the interlock is removed from the ladder logic itself, so it fails
even in automatic mode — no operator-visible mode change at all.

## 3. The two writes

| Address | Tag | Effect |
|---|---|---|
| 4 | `MODE_AUTO` | 1 → 0. Drops the plant out of automatic control, standing down rungs 1 and 2 |
| 0 | `P101_RUN` | 0 → 1. Commands the fill pump to run |

Both are needed. Writing `P101_RUN` alone in automatic mode achieves
nothing durable — the next control scan sees level ≥ `SP_LVL_HI` and
commands the pump straight back off. The attacker must disable the
control logic *before* the command sticks.

Students who spot this have understood something important: the attacker
needed to know how this plant's control logic was structured, not just
which coil was the pump. Which is what S01 was for.

## 4. Did the float switch work?

Yes. `LSHH_101` made at 98% and is true in the capture, and `ALARM_HORN`
annunciated correctly.

The float is a hardwired mechanical device on a completely separate path
from the analog level transmitter `LT_101`. Nothing on the network can
make a float say the wrong thing — it responds to water, not to Modbus.

That independence is why it is trustworthy, and it is the ground truth
S05 later depends on: when the analog transmitter is being spoofed to
show a normal level, the float still says the tank is full. Two
independent measurement paths disagreeing is a signal that neither path
can produce on its own.

In this scenario the two paths agreed — both said "full". The teaching
point lands harder in S05, but it is worth planting here.

## 5. What did the attacker need to know?

- That something at this address speaks Modbus TCP (S01 phase 1)
- Which coil is the pump — address 0 (S01 phase 3)
- That coil 4 is the automatic/manual selector, and that the interlock is
  gated on it

All of it comes from reconnaissance, and none of it requires vendor
documentation or insider access. The point map is *readable over the same
unauthenticated protocol* used to attack it — which is the recurring
structural problem with Modbus, not a misconfiguration at Cedar Hollow.

## 6. Why does forcing a pump on damage it?

A centrifugal pump moves energy into the water it discharges. With the
tank at its physical limit there is nowhere for that flow to go, so the
pump is running against a closed system — "deadheading". The energy has
to go somewhere: into the water as heat, and into the motor as increased
current draw.

In the model, current climbs from 31.5 A nominal toward 42 A, and after
15 simulated minutes the thermal overload latches out.

Note that the overload trip is *protection working correctly*. It saved
the motor. It also left the plant with no fill pump until a human
attends. Both things are true, and the second is the one that turns a
security incident into an operations problem.

## 7. What single control would have prevented this?

The strongest answer: **network segmentation with an enforced allowlist
of who may reach TCP/502.** The attack requires an arbitrary host to open
a Modbus session to the PLC. Remove that reachability and the rest never
happens. Cost: real network engineering, a firewall or data diode, and an
inventory of what legitimately talks to the PLC — which many small
utilities do not have.

Other defensible answers, with their limits:

- **Authenticate the protocol.** Correct in principle, unavailable in
  practice — Modbus TCP has no authentication, and the installed base
  cannot be replaced on any useful timescale. This is why compensating
  controls carry the load.
- **Alert on any write from a non-HMI source** (what this range does).
  Detects, does not prevent. Still valuable: it converts an invisible
  event into a page.
- **A physical high-level overflow that stops the pump in hardware**,
  independent of the PLC. Prevents the spill regardless of what the
  network does. This is the control an OT engineer reaches for first, and
  security people often overlook — the most robust protections in a plant
  are frequently not computers at all.

The instructor's goal here is to get students to stop looking for a
software patch. There isn't one. The protocol is behaving as designed.

## Common wrong turns

- **"Patch the vulnerability."** There is no vulnerability. Nothing was
  exploited. Two valid commands were sent by someone who shouldn't have
  been able to send them.
- **"The interlock is broken, fix the PLC logic."** The interlock worked
  as designed; manual mode legitimately stands it down. Changing that
  without understanding why manual override exists would break
  maintenance work and get quietly reverted by operations.
- **"The alarm failed."** The alarm annunciated correctly and promptly.
  It reported the symptom it exists to report. Alarms tell you the
  process is wrong, never why.
