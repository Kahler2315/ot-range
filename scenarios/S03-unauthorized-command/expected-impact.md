# S03 — Expected process impact

Loud, physical, and irreversible. The opposite of S01.

| Aspect | Effect |
|---|---|
| Tank level | Rises past the 85% stop setpoint to 100% and spills |
| `SP_ALM_HH` (95%) | Exceeded — alarm limit crossed on the way up |
| `LSHH_101` float | Makes at 98% |
| `ALARM_HORN` | Annunciates |
| Fill pump | Deadheads against the full tank, motor current climbs 31.5 A → 42 A, overload latches out |
| `P101_FAULT` | Latched true; pump unavailable until reset |
| Chlorine | Unaffected — dosing continues normally |
| Recovery | Requires a person on site. The fault is latching by design |

## Timeline (at `--speed 600`, starting from 90%)

| Elapsed | Event |
|---|---|
| 0 s | `MODE_AUTO` ← 0, `P101_RUN` ← 1 |
| ~0–9 s | Level climbs steadily; pump current nominal at 31.5 A |
| ~6 s | Level passes `SP_ALM_HH` (95%) — `ALARM_HORN` on |
| ~7 s | Level passes 98% — `LSHH_101` float makes |
| ~9 s | Level reaches 100% — **tank overflows** |
| ~9–12 s | Pump deadheads; current ramps toward 42 A |
| ~12 s | **Overload latches** — `P101_FAULT` true, pump stops |

Wall-clock times scale with `--speed`. At `--speed 1` this plays out over
roughly the 15 minutes the deadhead trip is modelled on.

## Two distinct kinds of damage

**Environmental / regulatory.** Treated water discharged to ground.
Depending on jurisdiction that is a reportable release, and for a
drinking-water system the loss of storage volume also threatens the
system pressure that keeps contaminants *out* of the distribution mains.

**Equipment.** The pump is out of service. A centrifugal pump running
against a closed system has nowhere to send its energy, so it goes into
the water as heat and into the motor as current. The overload trips to
save the motor — which is the protection working correctly, and still
leaves the plant with no fill pump until someone attends site.

The second point is the one students consistently underestimate. Stopping
the attack does not restore the plant. Somebody has to drive out, inspect
the pump, reset the overload, and decide whether the motor is still
trustworthy.
