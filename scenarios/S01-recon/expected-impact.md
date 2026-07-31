# S01 — Expected process impact

**None. That is the entire point of the scenario.**

| Aspect | Effect |
|---|---|
| Tank level | Unchanged — continues its normal fill/drain cycle |
| Pump | Unchanged — starts and stops at its setpoints |
| Chlorine residual | Unchanged |
| Alarms | None. `ALARM_HORN` stays off |
| HMI | Entirely normal. An operator watching all shift sees nothing |
| Equipment | No damage, no wear |

Every value the attacker touched was **read**. No coil was written, no
setpoint changed, no controller mode altered.

## Why a scenario with no impact is worth teaching

Because it is the only phase where defence is cheap.

Once the attacker knows the point map — that coil 0 runs the fill pump,
that coil 4 switches the plant out of automatic control, that holding
register 3 is the high-high alarm limit — every subsequent scenario in
this library becomes a couple of packets. S03 is *two writes*. The
expensive, noisy, error-generating work is all here, in the reconnaissance
that produces no process effect at all.

An organisation that only investigates incidents where something visibly
broke will never see this. They will meet the attacker for the first time
at S03, with the tank already overflowing.

The corollary matters for the analyst too: **absence of process impact is
not evidence of absence of intrusion.** The plant running normally tells
you nothing about whether someone is on the control network. Only the
network data does.
