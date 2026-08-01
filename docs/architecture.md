# Architecture and design plan

## Design goals

These are the constraints everything else is judged against. If a feature
doesn't serve one of these, cut it.

1. **Runs on a 16 GB laptop.** The default stack must come up on a
   student's machine, not just a workstation. Heavier options are opt-in
   profiles.
2. **Two commands to a running plant.** `git clone` then `make setup &&
   make sim`. If setup takes more than five minutes, nobody gets to the
   security content.
3. **The physics has to be legible.** A non-expert should look at the
   HMI, see a tank overflowing, and understand that something bad
   happened.
4. **Every attack ships with its detection.** An attack without a
   corresponding rule is an incomplete scenario.
5. **Detections are regression-tested in CI.** This is the
   differentiator. Almost nothing in OT training has it.
6. **Fully reproducible reset.** State resets to clean in seconds,
   infinitely.
7. **100% open source.** No proprietary tooling, no vendor engineering
   software, no license that blocks classroom or commercial use.

## Why water, why now

Grounding the scenario library in real, public advisories rather than
invented threats:

- Coordinated attacks on water and wastewater systems have repeatedly
  targeted internet-exposed PLCs with default credentials, forcing
  utilities to manual operation.
- CISA has directed water utilities to disconnect internet-exposed PLCs
  and monitor Modbus (port 502) traffic, among other guidance — so rules
  built here map onto federal guidance utilities are actively told to
  follow.
- Documented attacker behaviors include project file exfiltration,
  disabled safety interlocks/alarms with downstream logic left intact,
  and manipulated HMI/SCADA displays showing normal values while
  equipment ran unsafe.
- A 2024 case saw a municipal water tank overflow for 30–45 minutes after
  remote access to an industrial interface.

**Audience:** the ~50,000 US water systems, most with no dedicated
security staff; students entering OT; instructors who need a range they
can hand out.

> Verify all dates, advisory IDs, and figures against primary CISA/MITRE
> sources before citing them anywhere public-facing.

## Architecture

Purdue-style zones as separate Docker networks. Inter-zone traffic is
forced through a single router container, which is where the network
sensor sits — mirroring where a real OT tap lives.

```
  L4  IT / ENTERPRISE          [attacker]  [jump-host]
                                     |
                            =========|=========
  L3.5 DMZ                      [ router/sensor ]  <-- Zeek + Suricata
                            =========|=========
                                     |
  L3  OPERATIONS              [historian]  [grafana]
                                     |
  L2  SUPERVISORY                  [ HMI ]
                                     |
  L1  CONTROL                     [ PLC ]  (OpenPLC, from M1.5)
                                     |
  L0  PROCESS                  [ process-sim ]  (tank, pumps, valves, chlorine)
```

**Why a router container:** Docker bridge networks do MAC learning, so a
sniffer attached to a bridge will not see unicast traffic between other
containers. Routing all cross-zone traffic through one container solves
the visibility problem and is architecturally honest.

**Known limitation:** intra-zone traffic (attacker already inside L2/L1)
is invisible to the sensor. Accepted for v1 — most scenarios start in L4
anyway, which is realistic. See [`limitations.md`](limitations.md).

## M4: the router, as actually built

v1 scope is two Docker networks, not five: `zone-enterprise` (the
`attacker` container) and `zone-ops` (process-sim, OpenPLC, HMI,
historian, Grafana — everything else). `router` is the only container on
both. This collapses L2–L3.5 of the diagram above into one boundary,
which is the one the scenario library actually needs — see the "Known
limitation" note above, which already anticipated this.

**How `router` actually relays traffic — a deliberate deviation from the
plan's original "route all cross-zone traffic through one container"
phrasing.** Raw IP routing between two Docker bridge networks needs
`NET_ADMIN` and an explicit static route added to *every* container on
both sides (Docker does not propagate routes across bridges on its own,
and without a route back, replies from openplc would have nowhere to
go). That means modifying postgres's and grafana's containers just to
carry traffic they're not even part of. Instead, `router` runs an
**application-layer relay** — `sensor/tap.py`, the same already-tested
TCP proxy the M1 loopback stack uses, unmodified, listening on
zone-enterprise and forwarding to `openplc:502` on zone-ops. No NAT, no
`ip_forward`, no routes on any other container. Real Zeek and Suricata
then sniff the zone-enterprise-facing interface of that same container —
genuine libpcap capture of the wire, not tap.py's own log-writing path,
which is left running in parallel purely for local debugging
(`/zeek-logs/tap-relay.log`).

This has a real, honest side effect worth naming: because `router`
terminates two separate TCP connections rather than forwarding IP
packets, OpenPLC only ever sees connections originating from `router`'s
own zone-ops address, never the attacker's real IP. That's not a
limitation to apologize for — it's exactly how a real DMZ jump-host or
protocol-break device behaves, and it's arguably a *more* realistic
teaching point than transparent NAT would have been: tracing the pivot
means correlating the zone-enterprise-side session (real attacker IP,
what Zeek/Suricata actually observe and alert on) with the zone-ops-side
session (only shows `router`), not reading one flat conversation.

**Two real bugs found only by running this against genuine traffic, not
by reasoning about it:**

- **Docker's veth pairs don't compute real TCP checksums** (offloaded to
  hardware on a real NIC; skipped outright for virtual/intra-host
  links). Zeek's default behavior is to silently discard analysis on any
  packet with an invalid checksum — correct for a real deployment, but
  it meant the Modbus analyzer never fired at all against this traffic
  until `zeek -C` (ignore checksums) was set. `weird.log`'s
  `bad_TCP_checksum` entries were the trail; see `router/entrypoint.sh`.
- **The Modbus app-layer parser is disabled by default in stock
  `suricata.yaml`** (a performance/false-positive caution for a protocol
  most deployments don't run). `router/entrypoint.sh` sets
  `--set app-layer.protocols.modbus.enabled=yes` explicitly — without
  it, every `modbus:`-keyword rule fails to load with "protocol modbus
  cannot be used in a signature."

**Extending Zeek's log, not fighting its event order.** Zeek's built-in
`base/protocols/modbus/main.zeek` gives a real `modbus.log` (ts, uid,
id, tid, unit, func, pdu_type, exception) for free via `@load
base/protocols/modbus` — no changes needed. It does not carry
address/quantity/values, and there's no reliable way to add those to the
*same* log line: the analyzer raises the generic `modbus_message` event
(whose handler does the actual `Log::write`, at priority -5) before the
function-specific events like `modbus_read_holding_registers_request`,
so anything set from a specific-event handler lands one message too
late. `router/local.zeek` sidesteps this by writing a genuinely separate
`modbus_detailed.log` instead — which `sensor/tap.py`'s docstring had
already named as the expected two-log shape, back when it was only a
synthetic stand-in. `sensor/zeek_reader.py` joins the two logs by `(uid,
tid, pdu_type)` and normalizes the handful of real shape differences
(Zeek's `"REQ"/"RESP"` vs. tap.py's `"request"/"response"`; recovering
`func_code` from `func`'s name via
`sensor.modbus_frames.FUNCTION_NAMES`; `is_write` isn't a Zeek field at
all, derived the same way tap.py derives it) into exactly what
`sensor/detect.py` already expected. `Detector.analyze()` itself needed
**zero changes** — the payoff of choosing Zeek-compatible field names
all the way back at M1.

**Suricata** runs on the same interface, `-c /etc/suricata/suricata.yaml`
with the Modbus app-layer enabled. `router/suricata.rules` adds five
rules on top of the ones already shipped in the `suricata` Debian
package (`modbus-events.rules` — protocol-anomaly alerts like invalid
function codes, unsolicited responses, request floods): any Modbus write
function (05/06/0F/10) on the zone-enterprise boundary is `critical`
(S03/S05-adjacent), and any Modbus traffic at all on that boundary is
flagged as reconnaissance (S01-adjacent) — both trivially low-false-positive
because *no legitimate service ever originates from zone-enterprise* in
this topology, so there's no baseline to tune against, unlike the
Python-side rules.

**S05 does not have a docker-networked path, and that's a real finding,
not an oversight.** It writes an undocumented spoof register directly on
process-sim's own field-only slave — a device that sits *below* OpenPLC
and is reachable only from zone-ops, never from zone-enterprise even
through `router`. An attacker confined to zone-enterprise structurally
cannot pull off S05's specific lie against this topology. See
[`limitations.md`](limitations.md) for the fuller version — it's worth
treating as a teaching point in its own right (S05 models a more
privileged attacker position than S01/S03), not quietly working around.

**S03, on the other hand, needed a real fix, not just plumbing.**
Pointed at OpenPLC instead of process-sim directly, S03's reads/writes
landed on the wrong registers — OpenPLC mirrors field I/O at a fixed
offset (input registers +100, coils/discrete inputs +800; see
`docs/openplc-integration.md`) that `plc/modbus-map.yml`'s direct
addressing doesn't account for. `plc/modbus-map-openplc.yml` gives the
correct addresses for the points S03 (and S01, which never needed a
point-map lookup to begin with) actually touch, and `attacker/
s03_unauthorized_command.py` takes an optional `--map` to select it —
`make scenario-S03-docker` passes it automatically. Confirmed with real
before/after runs: without the fix, `IT_101`/`LT_101` reads stayed
pinned at whatever OpenPLC's own unused local registers happened to
hold; with it, the pump genuinely draws current and the tank level rises
in real time, watched live through the real network path.

## Tool stack

| Layer | Tool | License | Notes |
|---|---|---|---|
| PLC runtime | OpenPLC | GPLv3 | Real IEC 61131-3, from M1.5. Enables genuine logic-modification scenarios. |
| Process simulation | Custom Python | Apache-2.0 | Owned code. Talks to the PLC over Modbus. |
| Device sims / attacker tooling | pymodbus, scapy | BSD, GPLv2 | |
| HMI | Custom (Flask + HTML/CSS) | Apache-2.0 | `hmi/`, from M2 — see Open Questions below for why this replaced FUXA. |
| Historian | PostgreSQL | PostgreSQL | Avoids time-series-DB licensing ambiguity. |
| Dashboards | Grafana | AGPLv3 | |
| Network sensor | Zeek | BSD | `modbus.log` is the backbone of detection. |
| Signature IDS | Suricata | GPLv2 | |
| Log pipeline | Loki (default), OpenSearch (opt-in profile) | AGPLv3, Apache-2.0 | |
| Rule format | Sigma | DRL | |
| Technique mapping | MITRE ATT&CK for ICS | Free | |
| Runtime | Docker Engine **or** Podman | Apache-2.0 | Must not require Docker Desktop. |

**Excluded on purpose:** Factory I/O, Ignition, Studio 5000, TIA Portal,
EcoStruxure, any vendor engineering software. Not distributable, and
requiring them would gate the audience that most needs this.

**Licensing note:** OpenPLC (GPLv3) will run in its own container and be
invoked over a network protocol — aggregation, not derivation. Original
code stays Apache-2.0.

## The simulated process

A small pump station and storage tank with chlorine disinfection —
Cedar Hollow Pump Station. One process, modeled well.

- Storage tank, 500 m³, level 0–100%
- Fill pump P-101 (variable speed) drawing from an external source
- Discharge valve V-201 to distribution, diurnal demand curve on outflow
- Chlorine dosing pump CL-301, residual decays with time and flow
- High-high and low-low level float switches, hardwired and independent
  of the analog level transmitter — this independent path is what makes
  the manipulation-of-view scenario (S05) teachable later

**Failure states the process can actually reach**

- Tank overflow (level sustained at 100%)
- Pump damage from deadheading (running against an already-full tank)
- Chlorine residual below regulatory minimum → unsafe water leaves the
  plant
- Chlorine residual far above range → taste/corrosion event

The Modbus point map is `plc/modbus-map.yml` — the single source of
truth every component reads addressing from. See that file directly
rather than duplicating it here; it changes as scenarios add points.

## Scenario library (planned)

Each scenario ships as: attack script + briefing + expected process
impact + detection rule + analyst walkthrough + answer key. Only S01,
S03, and S05 are required to ship at M6; the rest are post-release.

### Tier 1 — Access and reconnaissance

**S01 — Exposed device discovery and point enumeration.** Scan for port
502, walk unit IDs, sweep register ranges. No process impact — pure
recon. *ATT&CK:* T0846, T0861, T0885.

**S02 — Default credentials and project file exfiltration.** No impact
visible to operators — that's the lesson. *ATT&CK:* T0812, T0822, T0859,
T0845.

### Tier 2 — Process manipulation

**S03 — Unauthorized command, tank overflow.** Write directly to the
pump coil (and mode coil), overriding auto control. Tank overflows,
pump deadheads and faults. *ATT&CK:* T0855, T0831, T0826.

**S04 — Setpoint drift.** Chlorine dose setpoint walked out of range in
small increments over hours. Not signature-based — needs historian trend
analysis. *ATT&CK:* T0836, T0831.

**S05 — Manipulation of view (flagship). ✅ Built.** Process runs unsafe
while the HMI and reported values show normal. *ATT&CK:* T0832, T0856,
T0815. As built: `LT_101` is a Modbus input register (FC04), which the
protocol makes read-only over the wire — there's no write function code
that targets it, so the lie can't be a simple unauthorized write the way
S03 attacks a coil. It's injected at the field device itself, via an
undocumented holding register not in `plc/modbus-map.yml` or on any
operator screen (`LT101_SPOOF_HR_INDEX`, `process_sim/server.py`).
Detection is two-layered: the arming write is still caught by the same
baseline-allowlist mechanism S03 uses (defense in depth), and a new
cross-consistency rule (`MODBUS_VIEW_MANIPULATION`) compares the
hardwired float (`LSHH_101`) against the reported transmitter value
(`LT_101`) independent of source — see `docs/coverage-matrix.md` and
`scenarios/S05-manipulation-of-view/`. Historian/HMI-rendered legs of the
cross-check are future work once M3 lands; the network-vs-hardwired-float
leg alone is enough to teach the lesson. This is the single highest-value
teaching moment in the range — an analyst who trusts the screen fails,
one who correlates across independent sources catches it.

### Tier 3 — Control logic and denial

**S06 — Logic modification with safety disabled. ✅ Built, detection
deliberately incomplete.** Modified logic (`plc/logic/
cedar_hollow_s06_no_interlock.st`) leaves normal pumping intact but
deletes the protective interlock rung. Latent — nothing happens until
the process hits a condition the interlock should have caught.
`attacker/s06_logic_modification.py` logs into OpenPLC's web UI with its
default credentials, uploads and compiles the modified program, restarts
the runtime, then raises `SP_LVL_HI` over Modbus to reach the trigger
condition without waiting on a real fill cycle. The compromise itself
happens over HTTP, which nothing in this range inspects — see
`scenarios/S06-logic-modification/detection.md` for why that's the
scenario's actual teaching point, not a gap to quietly work around, and
`docs/coverage-matrix.md`'s "Built, detection deliberately incomplete"
section. *ATT&CK:* T0889, T0843, T0837, T0880.

**S07 — Denial of control / operator lockout.** Session flood or held
connections force manual operation. *ATT&CK:* T0813, T0814, T0827.

**S08 — Replay.** A legitimate write sequence captured earlier is
replayed off-hours. *ATT&CK:* T0855, T0842.

> Verify all technique IDs against the current ATT&CK for ICS matrix
> before publishing.

## Detection design

The core primitive, worth building first: a **baseline allowlist of
`(source IP, unit ID, function code, register range)` tuples**, learned
from a clean run and stored as config. Anything outside the tuple set
alerts. This is how OT detection actually works — the environment is
deterministic in a way IT never is — and it catches S01, S02, S03, and
S08 with one mechanism.

Layered on top:
- **Timing/rate analytics** — polling interval deviation, connection
  storms (S07)
- **Cross-layer consistency checks** — network vs. HMI vs. historian
  (S05)
- **Trend analytics in Grafana** — slow parameter drift (S04)
- **Baseline integrity** — logic checksums, controller mode changes (S06)

Deliverables: Zeek scripts, Suricata rules, Sigma rules, and Grafana
alert definitions, each tagged with its ATT&CK technique and the
scenario it covers, with a published coverage matrix so gaps are visible.

## Build order

| Milestone | Contents | Done when |
|---|---|---|
| **M0** | Repo skeleton, README, license, CI shell, security tooling wired up before any real code | Lint and secret scanning pass on an empty repo |
| **M1** | Process sim + Modbus map + Modbus slave + CLI | You can watch a tank fill from the terminal |
| **M1.5** | ✅ OpenPLC integration, control logic moves from Python to IEC 61131-3 | Done — S06 viability confirmed end to end, see `docs/openplc-integration.md` |
| **M2** | ✅ HMI driving the process | Done — custom (`hmi/`), not FUXA; demoable to a non-technical person |
| **M3** | ✅ Historian + Grafana dashboards | Done — `historian/` polls OpenPLC on its own cadence and writes to Postgres, verified end-to-end in `tests/test_historian.py`; Grafana auto-provisions the datasource and a 5-panel dashboard (`dashboards/`), verified via `/api/ds/query` — see the M3 note below on how it was checked |
| **M4** | ✅ Zone networks, router container, Zeek + Suricata | Done (v1 scope — one instrumented zone boundary, see below) — real `modbus.log`/`modbus_detailed.log` populating from genuine packet capture, verified end-to-end in `tests/test_router.py` |
| **M5** | ✅ Scenarios S01, S03, S05 + detections + CI assertions | Done — each attack runs, each detection fires, CI proves it |
| **M6** | **Publish.** Docs, walkthroughs, coverage matrix | Someone else clones it and gets to S05 unaided |

S06 is also built (`attacker/s06_logic_modification.py` + full scenario
docs) even though it was scoped as post-release — its mechanism was
already proven end to end by M1.5's own OpenPLC integration tests, so
turning it into a full scenario was cheap once M4 was done. Its
detection deliberately isn't built; see `coverage-matrix.md`.

Everything else after M6 — S02, S04, S07, S08, second protocol, second
process — is post-release. Ship at M6.

## Quality-of-life features, ranked by value per hour spent

1. CI that runs every attack and asserts its detection fires.
2. One-command setup / reset / per-scenario run, clean state every time.
3. Auto-export a pcap per scenario run — instant teaching artifact, also
   seeds the detection test corpus.
4. Scenario briefings + scoring/flag mode.
5. Preloaded Grafana dashboards, not "import this JSON."
6. Blue-team mode — run an attack silently on a timer, hide the answer
   key.
7. Difficulty toggles — noisy vs. stealthy variants of the same attack.
8. Health check on startup, in plain language.

## Security and sanitization practices

See [`SECURITY.md`](../SECURITY.md) for the responsible-use statement.
Applies to every commit, not just before release:

- Pre-commit: gitleaks, semgrep, bandit, ruff, hadolint — same gates in
  CI, plus SBOM generation and pinned/digest-pinned dependencies.
- No real IPs, hostnames, ASNs, or topology from any real environment —
  documentation-range addresses only (RFC 1918 / RFC 5737).
- No packet captures from a real network — every capture in the repo is
  generated by the range itself.
- No credentials, ever — `.env.example` holds placeholders only.
- Git history scrubbed before first public push.
- Nothing sourced from an employer, coursework, or competition.

## Open questions

1. **FUXA vs. Node-RED — evaluated, not yet built; shipping a minimal
   custom HMI for M2 instead, for now.** FUXA is still the better
   long-term choice on paper: official image (`frangoteam/fuxa`, 166k
   Docker pulls, MIT, 4.7k GitHub stars, actively maintained), a
   purpose-built SCADA product, native Modbus TCP, web-based editor,
   even a built-in historian useful for M3. But building its dashboard
   requires either driving its SVG canvas editor visually or
   hand-authoring its project format — a two-part system of binding
   metadata (`hmi.views[].items`) plus raw SVG markup
   (`hmi.views[].svgcontent`) that the frontend hooks into by
   structural convention I hadn't reverse-engineered. Doing either
   blind, without the ability to see rendered output, risks shipping
   something silently broken. Also found along the way: this FUXA
   version doesn't bundle Modbus support — it requires installing a
   driver via its Plugins system first, another unverified step.
   **Decision:** build a small purpose-written HMI now (`hmi/`, plain
   Flask + HTML/CSS, polls OpenPLC's slave interface directly) so M2
   has a real, verified, working dashboard, and revisit FUXA in a
   session with actual visual/screenshot access to the editor — the
   research above is still the starting point when that happens.
2. ~~Does OpenPLC's Modbus server expose everything needed for S06~~ —
   **resolved.** No — program download is HTTP, not Modbus, and that
   turned out to be the more realistic attack surface anyway. See
   `docs/openplc-integration.md`.
3. ~~Baseline learning mechanism~~ — **resolved.** Automatic: `sensor/detect.py
   --learn` derives a baseline from a clean traffic capture.
4. **How to represent "internet-exposed"** convincingly inside a closed
   range so S01/S02 feel real.
5. **Second process later** — wastewater lift station, or stay with one
   process and add protocol depth instead?
6. **M3 Grafana dashboard — verified at the data/query level, not by eye.**
   `dashboards/json/cedar-hollow.json` provisions cleanly (Grafana accepts
   and stores it without error on both 11.3.0 and 10.4.5) and every panel's
   `rawSql` was confirmed live against `/api/ds/query`, returning real rows
   from `process_history` with the expected field names. What wasn't
   confirmed is the rendered chart pixels: this session's browser tool
   reported `document.visibilityState: "hidden"` for its own tab against
   this app (the same class of limitation noted under the FUXA entry
   above), so Grafana's own visibility-gated panel loader never fired and
   nothing painted, independent of whether the dashboard is correct.
   Revisit with a session that has real screenshot/compositing access —
   should be a two-minute confirmation, not new work.
