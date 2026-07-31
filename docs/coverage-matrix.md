# Coverage matrix

Scenario × ATT&CK for ICS technique × detection rule × CI assertion.

Published so the gaps are visible. A scenario with no detection, or a
detection with no CI assertion, is an incomplete scenario — the point of
this table is to make that impossible to hide.

> Technique IDs should be re-verified against the current ATT&CK for ICS
> matrix before any public release.

## Built and regression-tested

| Scenario | Technique | Detection rule | Severity | CI assertion |
|---|---|---|---|---|
| **S01** Recon & point enumeration | T0846 Remote System Discovery | `MODBUS_UNAUTHORIZED_SOURCE` | high | `test_s01_recon_is_detected` |
| S01 | T0861 Point & Tag Identification | `MODBUS_POINT_ENUMERATION` | high | `test_s01_recon_is_detected` |
| S01 | T0846 Remote System Discovery | `MODBUS_UNIT_ID_SWEEP` | medium | `test_detect.py::test_unit_id_sweep_alerts` |
| S01 | T0861 Point & Tag Identification | `MODBUS_EXCEPTION_SPIKE` | medium | `test_detect.py::test_exception_spike_alerts` |
| S01 | T0885 Commonly Used Port | *(implicit — traffic on 502 is what the tap observes)* | — | — |
| **S03** Unauthorised command | T0855 Unauthorized Command Message | `MODBUS_UNAUTHORIZED_WRITE` | critical | `test_s03_unauthorized_command_is_detected` |
| S03 | T0831 Manipulation of Control | `MODBUS_UNAUTHORIZED_WRITE` (mode coil) | critical | `test_s03_write_targets_the_pump_and_mode_coils` |
| S03 | T0826 Loss of Availability | *(process impact, not network-detectable)* | — | `test_s03_reaches_overflow_and_pump_damage` |

**False-positive guard:** `test_clean_run_produces_no_alerts` asserts a
normal run of the plant generates zero alerts. Every rule above must
coexist with that.

## Detection rules and what they cost

| Rule | Detects | Blind spot |
|---|---|---|
| `MODBUS_UNAUTHORIZED_SOURCE` | Any host not in the baseline | Silent if the attacker operates from a legitimate host (compromised HMI) |
| `MODBUS_UNAUTHORIZED_WRITE` | Any state-changing command outside baseline | Silent if the HMI is *supposed* to write that point and is compromised |
| `MODBUS_POINT_ENUMERATION` | Sweeping addresses outside baseline | Degrades as the attacker learns the point map and narrows their reads |
| `MODBUS_UNIT_ID_SWEEP` | Device discovery | Trivially evaded by targeting one known unit ID |
| `MODBUS_EXCEPTION_SPIKE` | Probing addresses that don't exist | Degrades to zero once the attacker knows the map |

The last three are all *reconnaissance* detections and share a weakness:
they measure the attacker's ignorance, so they fade as the attacker
learns. That is an argument for treating S01 alerts as urgent rather than
informational — they are loudest at the only point where the plant hasn't
been touched yet.

## Not yet built

| Scenario | Technique | Planned detection | Blocked on |
|---|---|---|---|
| **S02** Default creds / project exfil | T0812, T0822, T0859, T0845 | Auth from unexpected source; off-hours access; outbound volume anomaly | HTTP-layer sensor (see [`openplc-integration.md`](openplc-integration.md)) |
| **S04** Setpoint drift | T0836, T0831 | Historian trend analysis — deliberately *not* signature-based | Historian (M3) |
| **S05** Manipulation of view | T0832, T0856, T0815 | Cross-layer consistency: network-observed vs. HMI-rendered vs. historian vs. hardwired float | HMI (M2) + historian (M3) |
| **S06** Logic modification, safety disabled | T0889, T0843, T0837, T0880 | Program upload outside maintenance window; logic checksum drift; runtime restart | OpenPLC (M1.5) |
| **S07** Denial of control | T0813, T0814, T0827 | Connection rate/count anomaly; HMI polling gaps | — (buildable now) |
| **S08** Replay | T0855, T0842 | Transaction ID reuse; command outside operational envelope | — (buildable now) |

## Honest gaps in what exists today

1. **The sensor is an inline proxy, not Zeek.** It emits Zeek
   `modbus.log`-compatible records so rules port over, but it is not the
   real thing and does not do protocol validation, reassembly edge cases,
   or anything Suricata would give. Replaced/complemented at M4.
2. **Source identity is IP-based**, and Modbus has no authentication, so
   a spoofed source address defeats the source-based rules. Realistic —
   this is exactly why segmentation matters more than detection — but it
   should be stated in any teaching material rather than implied away.
3. **No timing/rate analytics yet.** Polling-interval deviation is
   specified in the plan and would catch S07; not built.
4. **No Sigma rule export.** Rules are Python. Sigma output is planned so
   they are portable to other stacks.
5. **Only cross-zone traffic will be visible once M4 lands.** An attacker
   already inside the control zone is invisible to a sensor at the zone
   boundary. Documented in [`limitations.md`](limitations.md).
