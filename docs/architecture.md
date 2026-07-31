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

## Tool stack

| Layer | Tool | License | Notes |
|---|---|---|---|
| PLC runtime | OpenPLC | GPLv3 | Real IEC 61131-3, from M1.5. Enables genuine logic-modification scenarios. |
| Process simulation | Custom Python | Apache-2.0 | Owned code. Talks to the PLC over Modbus. |
| Device sims / attacker tooling | pymodbus, scapy | BSD, GPLv2 | |
| HMI | FUXA | MIT | Web SCADA/HMI editor, from M2. |
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

**S05 — Manipulation of view (flagship).** Process runs unsafe while the
HMI and reported values show normal. *ATT&CK:* T0832, T0856, T0815.
Detection is a cross-check across independent sources: sensor-observed
registers vs. what the HMI renders vs. what the historian stores vs. the
hardwired float switch. This is the single highest-value teaching moment
in the range — an analyst who trusts the screen fails, one who correlates
across layers catches it.

### Tier 3 — Control logic and denial

**S06 — Logic modification with safety disabled.** Modified logic leaves
normal pumping intact but deletes the protective interlock rung. Latent —
nothing happens until the process hits a condition the interlock should
have caught. Only meaningful once control logic lives in OpenPLC (M1.5).
*ATT&CK:* T0889, T0843, T0837, T0880.

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
| **M1.5** | OpenPLC integration, control logic moves from Python to IEC 61131-3 | S06 viability confirmed |
| **M2** | FUXA HMI driving the process | Demoable to a non-technical person |
| **M3** | Historian + Grafana dashboards | Trends visible over hours |
| **M4** | Zone networks, router container, Zeek + Suricata | `modbus.log` populating with clean baseline traffic |
| **M5** | Scenarios S01, S03, S05 + detections + CI assertions | Each attack runs, each detection fires, CI proves it |
| **M6** | **Publish.** Docs, walkthroughs, coverage matrix | Someone else clones it and gets to S05 unaided |

Everything after M6 — S02, S04, S06, S07, S08, second protocol, second
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

1. **FUXA vs. Node-RED** — build a throwaway HMI in each at M2 and pick
   on feel. FUXA is more authentic; Node-RED is faster.
2. **Does OpenPLC's Modbus server expose everything needed** for the S06
   logic-download scenario, or does that need a separate engineering
   interface simulation? First thing to check at M1.5.
3. **Baseline learning mechanism** — automatic learn-mode run, or
   hand-written allowlist config?
4. **How to represent "internet-exposed"** convincingly inside a closed
   range so S01/S02 feel real.
5. **Second process later** — wastewater lift station, or stay with one
   process and add protocol depth instead?
