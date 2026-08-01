# Known limitations

Documented openly rather than hidden — a limitation nobody wrote down is a
limitation somebody else discovers the hard way.

1. **Not an engineering-accurate hydraulic model.** Tank/pump/demand
   numbers are tuned so behavior is legible on a dashboard (a forced pump
   overflows the tank in a demo-friendly number of minutes at a normal
   `--speed`), not for real process engineering analysis.
2. **Two control-logic paths exist, and CI's detection tests still target
   the Python one.** `make sim` (default) runs the interim Python
   controller in `process_sim/server.py`, which is what the CI-gated
   `tests/test_attack_detection.py` runs against. `make up` runs the real
   thing — OpenPLC executing compiled IEC 61131-3
   (`plc/logic/cedar_hollow.st`) against `process_sim/server.py
   --field-only`. S01 and S03 both now also run against OpenPLC for real,
   through the M4 router (`make scenario-S01-docker` /
   `scenario-S03-docker`), verified in `tests/test_router.py` — but that
   is a separate, docker-marked test from the CI-gated loopback suite,
   not a replacement for it. S05 does not have a docker-networked path;
   see the M4 entry below.
3. **pymodbus is pinned to 3.6.9**, not the current 3.14+. 3.14 renamed
   `ModbusSlaveContext` → `ModbusDeviceContext` and replaced the datastore
   accessors used here with `SimData`/`SimDevice`; 4.0 will make that the
   only option. Most documentation and examples students find will still
   target the pre-4.0 API for a while, so the pin keeps this repo aligned
   with what's findable. Migrating to the new API is a known follow-up.
4. **The pump inflow model is simplified, not a real centrifugal curve.**
   Inflow is constant with respect to level; "deadheading" is defined as
   the pump continuing to run once the tank is already at its 100%
   structural limit — a simplification chosen so the S03 scenario (forced
   pump override → overflow → pump damage) reaches both outcomes within a
   legible timeframe, rather than modeling a real pump/system curve.
5. **The M1 loopback sensor (`sensor/tap.py`) is still an inline proxy,
   not Zeek — real Zeek exists now, but only in the M4 router.** The
   `router` container (`make up`) runs genuine Zeek + Suricata sniffing
   real packets, proven in `tests/test_router.py`: real protocol
   parsing, real reassembly, a real signature IDS layer, none of it
   emulated. `sensor/tap.py` itself is unchanged and still backs the
   fast loopback scenarios (`make scenario-S01` etc.) — the M1 chapter's
   own reasoning (no root, Docker bridges do MAC learning so a
   bridge-attached sniffer sees nothing between other containers) still
   holds for why that path stays a proxy rather than becoming real Zeek
   too. `sensor/zeek_reader.py` adapts real Zeek's two-log output
   (`modbus.log` + the custom `modbus_detailed.log`, see
   `router/local.zeek`) into the exact record shape `sensor/detect.py`
   already expected — proving the original "Zeek-compatible field
   names" design decision actually paid off.
6. **Source identity is IP-based, and Modbus has no authentication.** A
   spoofed source address defeats every source-based detection rule here.
   That is a faithful reflection of the protocol rather than a modelling
   shortcut, and it is the reason segmentation matters more than
   detection — but teaching material should say so rather than imply the
   rules are stronger than they are.
7. **Reconnaissance detections fade as the attacker learns.**
   `MODBUS_EXCEPTION_SPIKE`, `MODBUS_POINT_ENUMERATION` and
   `MODBUS_UNIT_ID_SWEEP` all measure the attacker's *ignorance* of the
   plant. Narrow the sweep to points that exist and they go quiet. See
   [`coverage-matrix.md`](coverage-matrix.md).
8. **Only one zone boundary is instrumented (M4 v1).** The full
   architecture (`docs/architecture.md`) sketches L0–L4 as five layers;
   what actually exists is two Docker networks — `zone-enterprise`
   (attacker) and `zone-ops` (everything else: process-sim, OpenPLC,
   HMI, historian, Grafana) — bridged only by `router`, which is where
   Zeek/Suricata sit. Traffic *within* zone-ops (e.g. HMI ↔ OpenPLC) is
   not segmented or observed at all. This was always the documented
   plan for v1 ("most scenarios start in L4 anyway"), not a shortfall
   discovered late.
9. **S05's spoof cannot be reproduced through the M4 router — a real
   finding, not a bug.** S05 writes an undocumented register directly on
   process-sim's own field-device slave (`LT101_SPOOF_HR_INDEX`), which
   sits *below* OpenPLC in the topology and is only reachable from
   zone-ops, never from zone-enterprise even via router. An attacker
   confined to zone-enterprise genuinely cannot pull off this specific
   lie against this specific topology — which is the segmentation
   working as intended, and is itself worth teaching: S05 models an
   attacker with field-bus-level access (a different, more privileged
   position than S01/S03's "reaches the PLC's Modbus interface"), and
   the M4 network correctly narrows what's reachable from further out.
   No `scenario-S05-docker` Makefile target exists because of this —
   see `docs/architecture.md` M4.
10. **OpenPLC's holding-register (setpoint) layout doesn't mirror
    `plc/modbus-map.yml`'s addressing.** Field I/O offsets (input
    registers +100, coils/discrete inputs +800) are confirmed correct —
    see `plc/modbus-map-openplc.yml` and `hmi/app.py`/`historian/
    ingest.py`, which already relied on them. Setpoint holding
    registers were authored independently in `plc/logic/cedar_hollow.st`
    and do not follow the same tag order (confirmed via S01 recon
    output: index 2 is `SP_ALM_HH`, not `SP_CL_DOSE`). Nothing in
    `attacker/` currently writes a setpoint against OpenPLC, so this is
    deferred rather than mapped out fully.
11. **No Sigma rule export.** Detection rules are Python. Sigma output is
    planned so the rules are portable to other analyst stacks.
12. **S06 is a fully built, playable scenario with no detection at all.**
    `attacker/s06_logic_modification.py` and its full scenario docs
    (`scenarios/S06-logic-modification/`) exist, but the compromise
    happens over HTTP to OpenPLC's web UI, and nothing in `sensor/` or
    `router/` inspects HTTP — same root cause as S02's blocker below.
    This is by design, not an oversight left unlabeled: see the
    scenario's own `detection.md` for why leaving it undetected is the
    actual lesson.
13. **S02 (default creds / project exfiltration) is not built at all.**
    Blocked on the same missing HTTP-layer sensor as S06, above.
