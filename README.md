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

M0 (repo scaffold + security gates) and M1 (process simulation, Modbus TCP
slave, engineering CLI, unit + smoke tests) are done. Next: M1.5, moving
control logic into OpenPLC (IEC 61131-3) so the simulator and the
controller are separate, reprogrammable components. See
[`docs/architecture.md`](docs/architecture.md) for the full design and
milestone plan.

## Quickstart

```bash
make setup    # venv + deps + pre-commit hooks
make sim      # start the process simulator on loopback:5502
make watch    # in another terminal: live values of key process points
make test     # 15 unit tests, physics only
make smoke    # end-to-end: auto control cycle, then an S03 attack preview
```

Read or write any point by tag name:

```bash
.venv/bin/python -m tools.modctl points          # list the full point map
.venv/bin/python -m tools.modctl read LT_101      # tank level
.venv/bin/python -m tools.modctl write SP_CL_DOSE 1.5
```

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
