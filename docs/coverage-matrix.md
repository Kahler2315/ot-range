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
| **S05** Manipulation of view | T0855 Unauthorized Command Message | `MODBUS_UNAUTHORIZED_WRITE` (spoof-arming write) | critical | `test_s05_initial_spoof_write_is_also_caught_by_the_baseline_rule` |
| S05 | T0856 Spoof Reporting Message | `MODBUS_VIEW_MANIPULATION` (LSHH_101 vs. LT_101 cross-check) | critical | `test_s05_manipulation_of_view_is_detected` |
| S05 | T0832 Manipulation of View | *(process impact — the point of the scenario)* | — | `test_s05_reaches_hardwired_trip_while_reported_level_stays_frozen` |

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
| `MODBUS_VIEW_MANIPULATION` | LSHH_101 tripped while LT_101 reports a comfortable level — a physical impossibility, checked regardless of source | Only as good as the specific point pair it's written for; doesn't generalize to a spoof of some other value with no independent hardwired cross-check |

The last three are all *reconnaissance* detections and share a weakness:
they measure the attacker's ignorance, so they fade as the attacker
learns. That is an argument for treating S01 alerts as urgent rather than
informational — they are loudest at the only point where the plant hasn't
been touched yet.

## Built, detection deliberately incomplete

| Scenario | Technique | Detection status | CI assertion |
|---|---|---|---|
| **S06** Logic modification, safety disabled | T0889 Modify Program, T0843 Program Download, T0837 Loss of Protection, T0880 Loss of Safety | **Not detected.** The compromise is over HTTP (OpenPLC's web UI); no `sensor/` or `router/` component inspects HTTP. The scenario's own accelerating `SP_LVL_HI` write is Modbus-visible and would fire `MODBUS_UNAUTHORIZED_SOURCE`/`MODBUS_UNAUTHORIZED_WRITE` the same as S03, but that's downstream of the real damage, not the attack itself. See [`scenarios/S06-logic-modification/detection.md`](../scenarios/S06-logic-modification/detection.md) | Mechanism proven end-to-end: `tests/test_openplc_integration.py::test_s06_program_swap_disables_interlock_even_in_auto_mode`. Attack script (`attacker/s06_logic_modification.py`) and full scenario docs exist; no `tests/test_attack_detection.py` entry, because there's no detection to assert |

Listed separately from "Built and regression-tested" on purpose: S06 is
a fully playable scenario with real teaching material, and it would be
dishonest to either bury it in "not yet built" or imply it has detection
coverage it doesn't. The gap is the point — see the scenario's own
`detection.md`.

## Not yet built

| Scenario | Technique | Planned detection | Blocked on |
|---|---|---|---|
| **S02** Default creds / project exfil | T0812, T0822, T0859, T0845 | Auth from unexpected source; off-hours access; outbound volume anomaly | HTTP-layer sensor — same blocker as S06 above (see [`openplc-integration.md`](openplc-integration.md)) |
| **S04** Setpoint drift | T0836, T0831 | Historian trend analysis — deliberately *not* signature-based | Historian is built (M3, `historian/`); no `attacker/` drift script, Grafana alert rule, or scenario docs yet |
| **S07** Denial of control | T0813, T0814, T0827 | Connection rate/count anomaly; HMI polling gaps | — (buildable now) |
| **S08** Replay | T0855, T0842 | Transaction ID reuse; command outside operational envelope | — (buildable now) |

## Honest gaps in what exists today

1. **The M1 loopback sensor is still an inline proxy, not Zeek — but real
   Zeek now exists in the M4 router.** `sensor/tap.py` (backing the fast
   `make scenario-S01` etc. path) emits Zeek `modbus.log`-compatible
   records without being Zeek. The M4 `router` container runs genuine
   Zeek + Suricata against real captured packets instead — real protocol
   parsing, real reassembly, a real signature layer — and
   `sensor/detect.py`'s rules catch attacks through that path unmodified
   (`sensor/zeek_reader.py`, `tests/test_router.py`). Both paths coexist
   on purpose: loopback for fast iteration, `router` for the real thing.
2. **Source identity is IP-based**, and Modbus has no authentication, so
   a spoofed source address defeats the source-based rules. Realistic —
   this is exactly why segmentation matters more than detection — but it
   should be stated in any teaching material rather than implied away.
3. **No timing/rate analytics yet.** Polling-interval deviation is
   specified in the plan and would catch S07; not built.
4. **No Sigma rule export.** Rules are Python. Sigma output is planned so
   they are portable to other stacks.
5. **Only cross-zone traffic is visible, and only at one boundary.** M4
   v1 is two zones (`zone-enterprise`, `zone-ops`) bridged by `router`;
   an attacker already inside `zone-ops` is invisible to it, same as the
   plan always anticipated. Documented in [`limitations.md`](limitations.md).
