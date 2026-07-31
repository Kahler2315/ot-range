# S03 — Detection

## MODBUS_UNAUTHORIZED_WRITE (critical)

*ATT&CK: T0855 Unauthorized Command Message*

A write function code (5, 6, 15, 16, 23) from a source/unit/address
combination the baseline does not permit.

Asserted in CI by
`tests/test_attack_detection.py::test_s03_unauthorized_command_is_detected`,
including that the alert names the specific coils that were attacked
(`test_s03_write_targets_the_pump_and_mode_coils`).

### Why writes get their own rule at critical

A read is an information disclosure. A write **moves equipment**. In IT
those sit on a similar severity scale; in OT they do not, because a write
has a physical consequence that no amount of incident response can undo.
You cannot un-spill the tank.

So the rule deliberately does not care whether the source is otherwise
known. Even the HMI's own address writing a coil it has never written
before alerts — because a legitimate HMI in this plant only reads, and a
write from it means either an operator doing something unusual or an
attacker who has taken the HMI. Both warrant a look.
`tests/test_detect.py::test_write_from_allowlisted_read_only_source_still_alerts`
pins that behaviour.

### Alert content

```
[CRITICAL] MODBUS_UNAUTHORIZED_WRITE  (S03)
           Unauthorised write from 127.0.0.2: WRITE_SINGLE_COIL
           to address(es) [0, 4]
           ATT&CK: T0855 Unauthorized Command Message
```

Addresses 0 and 4 are `P101_RUN` and `MODE_AUTO`. Naming the points
rather than just the offsets is what lets an on-call analyst decide
severity in seconds instead of going to fetch the point map.

## MODBUS_UNAUTHORIZED_SOURCE (high)

Also fires, because the attacker is not in the baseline. In a variant
where the attacker has already taken the HMI, this one goes quiet and the
write rule is the only thing left — which is the argument for having
both.

## What this scenario cannot be detected by

**Not by a signature.** These are perfectly well-formed Modbus writes.
There is no malformed packet, no exploit, no shellcode, no anomaly in the
bytes at all. A signature IDS with no knowledge of the plant sees two
valid commands.

**Not by the alarm.** The alarm annunciated correctly and told the
operator the tank was high. It did not, and could not, say *why*. From the
control system's perspective the pump was commanded on and the pump ran.

**Not by the process data alone.** A level trend shows the tank filling
past setpoint, which is a symptom. Only the network data distinguishes
"the level controller is misconfigured" from "an unauthorised host issued
a command", and those two findings lead to completely different responses.

This is the argument for network monitoring in OT in one scenario: the
protocol has no concept of authorisation, so the only place the
distinction between legitimate and illegitimate exists is in *who sent
it*, which only the network layer knows.
