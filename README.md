# OT Range

A small, open-source OT/ICS cyber range simulating a municipal water pump
station over Modbus TCP — built for teaching attack detection, not just
attacks.

Scenarios in this range are modeled on real, public 2026 water-sector
incidents (coordinated attacks on US water utilities, CISA advisories on
internet-exposed PLCs) rather than invented threats. Every attack scenario
ships with a working detection rule that is regression-tested in CI.

See [`SECURITY.md`](SECURITY.md) before running anything here — this is a
simulated environment only.

## Design goals

1. Runs on a 16 GB laptop. Default stack, not a workstation-only setup.
2. `git clone` then a single setup command to a running plant.
3. The physics is legible — a non-expert can look at a dashboard, see a
   tank overflowing, and understand something bad happened.
4. Every attack ships with its detection. An attack without a detection is
   an incomplete scenario.
5. Detections are regression-tested in CI.
6. Fully reproducible reset — clean state in seconds, infinitely.
7. 100% open source. No proprietary tooling, no vendor engineering
   software, no license that blocks classroom or commercial use.

## Status

Working today: the simulated plant, a real OpenPLC controller running
compiled IEC 61131-3 logic, an HMI, a historian + Grafana dashboards, a
real Zeek + Suricata sensor behind a zone-segmented Docker network, and
four attack scenarios with full teaching material — including the
flagship manipulation-of-view scenario — with detections for three of
them asserted to fire in CI (S06's undetected-by-design gap is documented,
not hidden).

v1 is complete — every milestone below is done, verified by a fresh
`git clone` reaching the flagship scenario unaided.

| Milestone | State |
|---|---|
| M0 repo scaffold + security gates | done |
| M1 process sim, Modbus slave, CLI | done |
| M1.5 real OpenPLC running compiled control logic, S06 attack path proven | done — see [`docs/openplc-integration.md`](docs/openplc-integration.md) |
| M2 HMI (custom, not FUXA — see [`docs/architecture.md`](docs/architecture.md) open question 1) | done |
| M3 historian + Grafana dashboards | done — see [`docs/architecture.md`](docs/architecture.md) open question 6 for the one unverified piece (rendered chart pixels, not the data) |
| M4 zone networks, router, real Zeek + Suricata | done (v1 scope: one instrumented zone boundary) — see [`docs/architecture.md`](docs/architecture.md) M4 section |
| M5 S01 + S03 + S05, sensor, detections, CI assertions | done |
| M6 publish — docs, walkthroughs, coverage matrix | done |

See [`docs/architecture.md`](docs/architecture.md) for the full design and
[`docs/coverage-matrix.md`](docs/coverage-matrix.md) for exactly what is
detected and what isn't.

## GUI (recommended if you're new to this)

```bash
./start-panel.sh
```

Sets up the venv on first run, starts a local web control panel, and
opens it in your browser automatically. Status lights for every
service, one-click bring-up/reset/teardown, a scenario picker with
plain-language briefings, and a live console that streams each attack
and detection as it runs — no terminal commands to memorize. Everything
it does is one of the `make` targets below, run for you; nothing
hidden. See [`panel/app.py`](panel/app.py).

Each scenario also has 5 short-answer flags (20 total) pulled straight
from its own answer key — a concrete "did I actually find the thing"
check, not just a pass/fail on running the attack script. Answers are
checked server-side, so they're never sitting in the page source; the
header tracks your running total. See [`scenarios/flags.py`](scenarios/flags.py).

**Windows 10/11 — installer (recommended for schools/companies):**
prerequisite either way is [Docker Desktop](https://www.docker.com/products/docker-desktop/)
with WSL2 enabled — Docker Desktop needs this on Windows regardless,
and this range's `router` container needs raw packet capture, a
Linux-container thing no matter how it's packaged. Given that,
download **[`OT-Range-Setup.exe`](https://github.com/Kahler2315/ot-range/releases/download/latest/OT-Range-Setup.exe)**
(a public link, no GitHub login needed — auto-rebuilt from `master` on
every installer change) and run it. No admin rights needed. It adds a
**Start Menu and Desktop shortcut** that clones/updates the repo inside
WSL and launches the control panel automatically — double-click the
icon, the browser opens, that's it. Source:
[`installer/ot-range.iss`](installer/ot-range.iss) (built with
[Inno Setup](https://jrsoftware.org/isinfo.php)).

Two things to know going in: it's unsigned (no paid code-signing
certificate), so Windows SmartScreen will show "Windows protected your
PC" the first time — click **More info → Run anyway**, which is normal
for a small open-source tool, not a sign anything's wrong. And the
installer itself is compiled and validated on GitHub's real Windows
runners in CI, but hasn't been run end-to-end on an actual Windows
machine with WSL2 + Docker Desktop installed — that combination isn't
available in this project's own development environment. If the
shortcut doesn't behave as documented, please open an issue.

**Windows 10/11 — manual path:** install WSL2 + Docker Desktop as
above, clone the repo *inside* the WSL filesystem (not `/mnt/c/...`),
and run `./start-panel.sh` from the WSL shell — from that point on it
behaves exactly like Linux, and the panel opens in your normal Windows
browser via WSL's interop. This is what the installer's shortcut does
for you automatically; use this path if you'd rather not run an
unsigned .exe, or want more control over where things land.

**Linux:** first run also adds an "OT Range Control Panel" entry to
your desktop's application menu, with this clone's actual path baked
in, so after that you can launch it without a terminal at all.

## Quickstart (terminal)

```bash
make setup                        # venv + deps + pre-commit hooks
bash scenarios/run_scenario.sh S05  # watch the tank overflow while every screen reads normal
```

That one command starts the plant, puts a sensor in front of it,
generates normal HMI traffic, runs the attack, and prints what the
detection caught.

Or drive the pieces yourself, one per terminal:

```bash
make sim      # the plant, on loopback:5502
make tap      # the sensor, on loopback:5020, logging to logs/modbus.log
make hmi      # normal HMI polling
make watch    # live process values
make detect   # analyse the capture
```

Read or write any point by tag name:

```bash
.venv/bin/python -m tools.modctl points           # list the full point map
.venv/bin/python -m tools.modctl read LT_101      # tank level
.venv/bin/python -m tools.modctl write SP_CL_DOSE 1.5
```

## Run it behind a real PLC

`make sim` above runs an interim Python controller. `make up` runs the
real thing — [OpenPLC](https://openplcproject.com/), built from source,
executing compiled IEC 61131-3 logic (`plc/logic/cedar_hollow.st`)
against the physics simulator over Modbus, the way an actual plant is
wired — plus an HMI and a historian feeding Grafana dashboards. Requires
Docker.

```bash
make up      # builds and starts the full stack, waits until ready
make status  # health checklist: containers, ports, web UIs — what's up, what isn't
make reset   # wipe postgres + zeek-log state, restart clean, no rebuild
make panel   # same control panel as ./start-panel.sh, without the first-run setup check
```

| Service | URL | Login |
|---|---|---|
| OpenPLC web UI | http://localhost:8080 | `openplc` / `openplc` |
| OpenPLC Modbus interface | `localhost:502` | — |
| process-sim Modbus interface | `localhost:5502` | — |
| HMI (operator display) | http://localhost:8090 | — |
| Grafana (historian dashboards) | http://localhost:3000 | `admin` / `admin` |

`modctl` works against either interface — OpenPLC mirrors field I/O at
different addresses than process-sim's own direct map, so point at the
right map for whichever port you use:

```bash
.venv/bin/python -m tools.modctl --port 502 --map plc/modbus-map-openplc.yml read LT_101
.venv/bin/python -m tools.modctl --port 5502 read LT_101   # process-sim direct, default map
```

Default credentials are exactly the kind the scenario library's attacks
rely on — see [`SECURITY.md`](SECURITY.md). `make down` tears the stack
down (`make reset` if you just want clean state without tearing down).
See [`docs/openplc-integration.md`](docs/openplc-integration.md)
for how OpenPLC is wired and what was verified.

## Run scenarios over a real, zone-segmented network

`make up` also brings up `router` — a container on its own
`zone-enterprise` Docker network, dual-homed onto `zone-ops` (everything
else), running real Zeek and Suricata against real captured packets, not
a synthetic log. `openplc` and every other zone-ops service are
genuinely unreachable from `zone-enterprise` except through `router` —
no route exists, not a policy that could be misconfigured away (see
`tests/test_router.py`). `attacker` is a matching on-demand container:

```bash
make scenario-S01-docker   # recon, over the real network
make scenario-S03-docker   # unauthorized command, tank overflow, for real
```

(S05 isn't offered this way — its spoof targets a field device that
sits below OpenPLC in this topology and genuinely isn't reachable from
zone-enterprise. See `docs/architecture.md`'s M4 section for why that's
a finding, not a bug.)

Pull the real capture out of the router and run the same detection
rules against it:

```bash
docker cp ot-range-router-1:/zeek-logs/modbus.log /tmp/modbus.log
docker cp ot-range-router-1:/zeek-logs/modbus_detailed.log /tmp/modbus_detailed.log
.venv/bin/python -m sensor.detect /tmp/modbus.log --zeek /tmp/modbus_detailed.log
```

S06 targets a different attack surface entirely — OpenPLC's own web UI
(published to loopback alongside its Modbus interface, both by `make
up`), not the Modbus network path at all:

```bash
make scenario-S06   # default credentials, program upload, safety disabled
```

## Scenarios

Each ships with a briefing, expected impact, detection writeup, and an
instructor answer key.

```bash
make scenario   # terminal picker: browse scenarios, pick one, run it
```

Lists every scenario with its hook, process impact, and what catches
it, then dispatches to the right runner (loopback or docker) for you —
no need to remember which `run_scenario.sh` / `make scenario-*` target
goes with which scenario. `./start-panel.sh` (see the GUI section
above) does the same thing with a browser UI instead of a terminal
prompt, plus live streaming output and doc links per card.

| | Scenario | Process impact | Caught by |
|---|---|---|---|
| **S01** | [Recon & point enumeration](scenarios/S01-recon/) | None — that's the lesson | 4 rules |
| **S03** | [Unauthorised command](scenarios/S03-unauthorized-command/) | Tank overflows, pump destroys itself | `MODBUS_UNAUTHORIZED_WRITE` (critical) |
| **S05** | [Manipulation of view](scenarios/S05-manipulation-of-view/) (flagship) | Tank overflows for real while every screen reads a calm 50% | `MODBUS_VIEW_MANIPULATION` (critical) — hardwired float vs. spoofed transmitter |
| **S06** | [Logic modification, safety disabled](scenarios/S06-logic-modification/) | Interlock deleted from the PLC program itself — latent until the tank reaches high-high with the pump still running | **Not detected** — the compromise is over HTTP, which nothing here inspects. That's the scenario's own point; see its `detection.md` |

## Tests

```bash
make test      # fast: physics, framing, scope guard, detection rules
make test-e2e  # runs every attack for real and asserts its detection fires
make smoke     # end-to-end plant behaviour
make security  # gitleaks, ruff, bandit, semgrep, hadolint
```

`make test-e2e` is the one that matters. It stands up the plant, runs the
real attack scripts over real sockets, and fails the build if a detection
stops firing — or if a clean run starts producing false positives.

## Safety

Attack tooling is hard-bound to the range's own address space. Public
addresses are refused *structurally*, not by policy — editing
`attacker/scope.yml` to point at real equipment does not work, and the
guard fails closed on any resolution or config problem. See
[`attacker/common/scope.py`](attacker/common/scope.py) and its tests.

## Known limitations

See [`docs/limitations.md`](docs/limitations.md) for the full list
(hydraulics are not engineering-accurate, S06 has no detection by
design, pymodbus version pin, etc).

Nothing in this repo should ever be pointed at real equipment. See
[`SECURITY.md`](SECURITY.md).

## License

Apache-2.0 for all original code in this repository — see
[`LICENSE`](LICENSE). Third-party components used at runtime (OpenPLC,
Zeek, Suricata, etc.) retain their own licenses; see
[`docs/licenses.md`](docs/licenses.md).
