"""Real network topology data for the panel's network map.

Sourced directly from `docker-compose.yml` (services, ports, networks)
and `docs/architecture.md`'s Purdue-zone diagram — not an invented or
decorative diagram. `tests/test_topology.py` parses `docker-compose.yml`
itself and cross-checks every port/service declared here against it, so
this can't silently drift from what the stack actually runs.

Node/edge "monitored" flags reflect `docs/limitations.md` #8 exactly:
only the zone-enterprise/zone-ops boundary (the `router` container) is
instrumented. Traffic within zone-ops — process-sim<->openplc,
openplc<->hmi, openplc<->historian — is real but genuinely unobserved,
which is a documented limitation, not an oversight here.
"""

from __future__ import annotations

ZONES = {
    "zone-enterprise": {
        "label": "Enterprise / Attacker Zone",
        "subnet": "10.20.0.0/24",
        "trust": "untrusted",
    },
    "zone-ops": {
        "label": "OT Operations Zone",
        "subnet": "10.30.0.0/24",
        "trust": "trusted",
    },
    "boundary": {
        "label": "Monitored Boundary",
        "subnet": None,
        "trust": "boundary",
    },
}

NODES = [
    {
        "id": "attacker",
        "zone": "zone-enterprise",
        "label": "Attacker",
        "kind": "attacker",
        "ports": [],
        "detail": "On-demand container (docker-compose.yml's `attacker` "
        "service, profile-gated — not started by `make up`). Runs the "
        "scenario's attack script against `router`.",
    },
    {
        "id": "router",
        "zone": "boundary",
        "label": "Router / Sensor",
        "kind": "sensor",
        "ports": [],
        "detail": "The only container on both networks. Relays Modbus TCP "
        "to openplc (an application-layer relay, not IP routing — see "
        "docs/architecture.md M4). Real Zeek + Suricata sniff the "
        "zone-enterprise-facing interface. Does not inspect HTTP.",
    },
    {
        "id": "openplc",
        "zone": "zone-ops",
        "label": "OpenPLC Controller",
        "kind": "controller",
        "ports": [
            {"port": 502, "protocol": "Modbus TCP", "monitored": True},
            {"port": 8080, "protocol": "HTTP (web UI)", "monitored": False},
        ],
        "detail": "Real OpenPLC running compiled IEC 61131-3 logic "
        "(plc/logic/cedar_hollow.st). Modbus (502) is monitored at the "
        "router boundary; the HTTP web UI (8080) is not inspected by "
        "anything in this range — see S06's overlay.",
    },
    {
        "id": "process-sim",
        "zone": "zone-ops",
        "label": "Process Simulator",
        "kind": "process",
        "ports": [{"port": 5502, "protocol": "Modbus TCP", "monitored": False}],
        "detail": "The physical plant model — tank, pump, chlorine dosing. "
        "Polled by OpenPLC as a Modbus master. Ground truth for every "
        "physical value in the range.",
    },
    {
        "id": "hmi",
        "zone": "zone-ops",
        "label": "HMI",
        "kind": "hmi",
        "ports": [{"port": 8090, "protocol": "HTTP", "monitored": False}],
        "detail": "Operator display. Reads OpenPLC's Modbus slave "
        "interface, not process-sim directly.",
    },
    {
        "id": "historian",
        "zone": "zone-ops",
        "label": "Historian",
        "kind": "historian",
        "ports": [],
        "detail": "Polls OpenPLC on its own cadence, writes to Postgres. "
        "No host-published port — internal to zone-ops.",
    },
    {
        "id": "postgres",
        "zone": "zone-ops",
        "label": "PostgreSQL",
        "kind": "database",
        "ports": [],
        "detail": "Backing store for the historian and Grafana's "
        "datasource. No host-published port.",
    },
    {
        "id": "grafana",
        "zone": "zone-ops",
        "label": "Grafana",
        "kind": "dashboard",
        "ports": [{"port": 3000, "protocol": "HTTP", "monitored": False}],
        "detail": "Historian dashboards, auto-provisioned datasource.",
    },
]

EDGES = [
    {
        "id": "sim-plc",
        "from": "process-sim",
        "to": "openplc",
        "protocol": "Modbus TCP",
        "monitored": False,
    },
    {"id": "plc-hmi", "from": "openplc", "to": "hmi", "protocol": "Modbus TCP", "monitored": False},
    {
        "id": "plc-historian",
        "from": "openplc",
        "to": "historian",
        "protocol": "Modbus TCP",
        "monitored": False,
    },
    {
        "id": "historian-pg",
        "from": "historian",
        "to": "postgres",
        "protocol": "SQL",
        "monitored": False,
    },
    {
        "id": "grafana-pg",
        "from": "grafana",
        "to": "postgres",
        "protocol": "SQL",
        "monitored": False,
    },
    {
        "id": "attacker-router",
        "from": "attacker",
        "to": "router",
        "protocol": "Modbus TCP",
        "monitored": True,
    },
    {
        "id": "router-plc",
        "from": "router",
        "to": "openplc",
        "protocol": "Modbus TCP",
        "monitored": True,
    },
]

# Per-scenario overlays: which edges/nodes light up when a scenario is
# selected on the map. Grounded in each scenario's own detection.md /
# expected-impact.md (read in full while planning this), not guessed.
SCENARIO_OVERLAYS = {
    "S01": {
        "path_edges": ["attacker-router", "router-plc"],
        "affected_nodes": [],
        "detection_nodes": ["router"],
        "note": "Recon only — reads outside the baseline. No process "
        "impact; caught at the router/sensor boundary.",
    },
    "S03": {
        "path_edges": ["attacker-router", "router-plc"],
        "affected_nodes": ["openplc", "process-sim"],
        "detection_nodes": ["router"],
        "note": "Unauthorized writes to MODE_AUTO and P101_RUN — tank "
        "overflows, pump destroys itself.",
    },
    "S05": {
        "path_edges": ["attacker-router", "router-plc"],
        "affected_nodes": ["process-sim", "hmi"],
        "detection_nodes": ["router"],
        "truth_node": "process-sim",
        "spoofed_node": "hmi",
        "note": "process-sim holds the physical truth — its hardwired "
        "float trips for real. hmi displays a frozen, spoofed LT_101. "
        "The two nodes disagreeing is the entire scenario.",
    },
    "S06": {
        "path_edges": [],
        "compromise_port": {"node": "openplc", "port": 8080},
        "affected_nodes": ["openplc", "process-sim"],
        "detection_nodes": [],
        "note": "Compromise is over OpenPLC's HTTP web UI (port 8080), "
        "which nothing in this range inspects — a genuine monitoring "
        "gap, not a missed detection. The interlock is deleted from "
        "the running program itself; the wire looks normal throughout.",
    },
}


def get_topology() -> dict:
    """Return neutral infrastructure only; scenario overlays are gated separately."""
    return {
        "zones": ZONES,
        "nodes": NODES,
        "edges": EDGES,
    }


def get_scenario_overlay(scenario_id: str) -> dict | None:
    return SCENARIO_OVERLAYS.get(scenario_id)
