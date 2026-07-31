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

Working today: the simulated plant, a Modbus TCP sensor, two attack
scenarios with full teaching material, and detections for both that are
asserted to fire in CI.

| Milestone | State |
|---|---|
| M0 repo scaffold + security gates | done |
| M1 process sim, Modbus slave, CLI | done |
| M5 (partial) S01 + S03, sensor, detections, CI assertions | done |
| M1.5 OpenPLC / IEC 61131-3 control logic | researched, not built — see [`docs/openplc-integration.md`](docs/openplc-integration.md) |
| M2 FUXA HMI · M3 historian + Grafana · M4 zone networks + Zeek | not started |

See [`docs/architecture.md`](docs/architecture.md) for the full design and
[`docs/coverage-matrix.md`](docs/coverage-matrix.md) for exactly what is
detected and what isn't.

## Quickstart

```bash
make setup                        # venv + deps + pre-commit hooks
bash scenarios/run_scenario.sh S03  # watch an attack overflow the tank, and get caught
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

## Scenarios

Each ships with a briefing, expected impact, detection writeup, and an
instructor answer key.

| | Scenario | Process impact | Caught by |
|---|---|---|---|
| **S01** | [Recon & point enumeration](scenarios/S01-recon/) | None — that's the lesson | 4 rules |
| **S03** | [Unauthorised command](scenarios/S03-unauthorized-command/) | Tank overflows, pump destroys itself | `MODBUS_UNAUTHORIZED_WRITE` (critical) |

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
(hydraulics are not engineering-accurate, control logic isn't in a real
PLC yet, pymodbus version pin, etc).

Nothing in this repo should ever be pointed at real equipment. See
[`SECURITY.md`](SECURITY.md).

## License

Apache-2.0 for all original code in this repository — see
[`LICENSE`](LICENSE). Third-party components used at runtime (OpenPLC,
Zeek, Suricata, etc.) retain their own licenses; see
[`docs/licenses.md`](docs/licenses.md) once it lands.
