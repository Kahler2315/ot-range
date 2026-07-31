# S01 — Detection

Four rules fire on this scenario. Each is asserted in CI by
`tests/test_attack_detection.py::test_s01_recon_is_detected`, so a change
that silently breaks one fails the build.

## MODBUS_UNAUTHORIZED_SOURCE (high)

*ATT&CK: T0846 Remote System Discovery*

A host that is not in the baseline allowlist spoke Modbus at all.

This is the highest-value rule in OT and the one with the least
equivalent in IT security. An office network has laptops joining and
leaving constantly, so "unrecognised host" is noise. A control network
does not: the set of devices that talk to a PLC is small, fixed, and
known. Anything else is worth waking someone up for.

## MODBUS_POINT_ENUMERATION (high)

*ATT&CK: T0861 Point & Tag Identification*

A source touched more than 12 distinct function/address combinations
**outside its baseline**.

The "outside its baseline" qualifier is what makes this rule usable. The
legitimate HMI polls 18 distinct points every cycle — a naive
"touched many points" rule fires on normal operation forever and gets
switched off within a week. Because the HMI always polls *the same*
points, it scores zero here regardless of volume, while a sweep across
unknown addresses scores high immediately.

This distinction is regression-tested:
`tests/test_detect.py::test_many_baseline_points_do_not_trip_enumeration`.

## MODBUS_UNIT_ID_SWEEP (medium)

*ATT&CK: T0846 Remote System Discovery*

A source addressed more than 3 distinct unit IDs. Real masters know which
unit they are talking to; they do not enumerate.

## MODBUS_EXCEPTION_SPIKE (medium)

*ATT&CK: T0861 Point & Tag Identification*

More than 20% of a source's responses were Modbus exceptions.

A legitimate master holds the point map and asks only for addresses that
exist, so its exception rate sits near zero. A scanner that is *building*
the point map necessarily asks for addresses that don't, and the device
answers `ILLEGAL_DATA_ADDRESS` every time. The error rate is a direct
measurement of the attacker's ignorance of the plant — and it drops as
they learn it.

There is a minimum sample size (10 responses) so that a handful of errors
during, say, a device restart doesn't alert.

## What none of these rules use

No signature, no payload matching, no threat intelligence, no IOC list.
Nothing here would need updating if the attacker changed tooling
tomorrow. The rules describe *this plant's normal behaviour* and alert on
deviation from it — which is viable in OT precisely because the process is
deterministic and the traffic is repetitive.
