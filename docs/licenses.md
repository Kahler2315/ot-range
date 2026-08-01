# Third-party licenses

All original code in this repository is Apache-2.0 — see
[`LICENSE`](../LICENSE). This range also builds, runs, and depends on
third-party components at runtime, each under its own license. Listed
here are the ones actually built, pulled, or imported by this repo
today — not the aspirational tool stack in
[`architecture.md`](architecture.md)'s "Tool stack" table, which
includes some components (Loki/OpenSearch, Sigma, scapy) that are
planned but not yet integrated.

> Verify every license below against the upstream project before
> relying on this list for a real compliance decision — this is a
> best-effort summary, not legal advice.

## Built from source or pulled as a container image

| Component | License | Where it's used |
|---|---|---|
| [OpenPLC v3](https://github.com/thiagoralves/OpenPLC_v3) | GPLv3 | `plc/openplc/Dockerfile` — built from pinned source |
| [Zeek](https://zeek.org/) | BSD-3-Clause | `router/Dockerfile` |
| [Suricata](https://suricata.io/) | GPLv2 | `router/Dockerfile` |
| [PostgreSQL](https://www.postgresql.org/about/licence/) | PostgreSQL License (permissive) | `docker-compose.yml` — `postgres:16-alpine` |
| [Grafana OSS](https://github.com/grafana/grafana) | AGPLv3 | `docker-compose.yml` — `grafana/grafana-oss` |
| [Debian](https://www.debian.org/) | Various free-software licenses | Base image for `plc/openplc/Dockerfile`, `router/Dockerfile` |
| [Python](https://www.python.org/) | PSF License | Base image (`python:3.11-slim`) for `process_sim/`, `hmi/`, `historian/`, `attacker/` |
| [Docker Engine](https://www.docker.com/) | Apache-2.0 | Required to run any of the docker-compose stack |

**OpenPLC and Suricata are GPL/GPLv2.** They run in their own containers,
invoked over their own network protocols (Modbus, packet capture) — this
is aggregation, not derivation. Original code in this repository stays
Apache-2.0 and is not itself GPL-encumbered. Same reasoning applies to
Grafana's AGPLv3 (network use of an unmodified upstream container, not a
modified derivative distributed by this project).

## Python packages (runtime)

| Package | License | Used by |
|---|---|---|
| [pymodbus](https://github.com/pymodbus-dev/pymodbus) | BSD-3-Clause | `process_sim/`, `sensor/`, `attacker/`, `tools/`, `hmi/`, `historian/` — pinned to 3.6.9, see `docs/limitations.md` |
| [PyYAML](https://pyyaml.org/) | MIT | `common/pointmap.py`, `attacker/common/scope.py`, `sensor/detect.py` |
| [requests](https://requests.readthedocs.io/) | Apache-2.0 | `tools/openplc_configure.py`, `attacker/s06_logic_modification.py` |
| [Flask](https://flask.palletsprojects.com/) | BSD-3-Clause | `hmi/app.py` |
| [psycopg](https://www.psycopg.org/) | LGPLv3 | `historian/ingest.py` |

## Dev/CI-only (never shipped to users)

pytest, ruff, bandit, pre-commit, semgrep, hadolint, gitleaks — all used
only in `requirements-dev.txt` and `.pre-commit-config.yaml`/CI, never
imported by or bundled into anything a student runs. Each is
independently open source (MIT/Apache-2.0/BSD family); not itemized here
since none of them ship in a container or a distributed artifact.

## Excluded on purpose

No proprietary or vendor engineering software (Factory I/O, Ignition,
Studio 5000, TIA Portal, EcoStruxure) is used anywhere in this repo —
see `architecture.md`'s design goals. Nothing here requires a commercial
license to run, study, fork, or teach with.
