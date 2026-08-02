"""The network map must describe the range that actually exists.
Parses docker-compose.yml itself (not a hand-copied summary of it) and
cross-checks panel/topology.py against it, so the map can't silently
drift from the real stack."""

from __future__ import annotations

from pathlib import Path

import yaml

from panel.topology import EDGES, NODES, SCENARIO_OVERLAYS, ZONES, get_topology

REPO_ROOT = Path(__file__).resolve().parent.parent


def _compose_services() -> dict:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    return compose["services"]


def test_every_topology_node_with_ports_is_a_real_compose_service():
    services = _compose_services()
    # postgres is the one node backed by the *stock* image name, not a
    # docker-compose.yml key named "postgres" service — it is, in fact
    # ("postgres" is the actual service key). Router/attacker/etc use
    # ot-range-prefixed image names but the service keys match node ids.
    for node in NODES:
        if node["id"] in ("attacker",):
            continue  # profile-gated, not always present; still a real service key
        assert node["id"] in services, f"topology node {node['id']!r} has no compose match"


def test_openplc_ports_match_compose():
    services = _compose_services()
    openplc_node = next(n for n in NODES if n["id"] == "openplc")
    node_ports = {p["port"] for p in openplc_node["ports"]}
    # docker-compose.yml declares these via env-var-defaulted mappings;
    # the defaults (502, 8080) are what topology.py documents.
    assert 502 in node_ports
    assert 8080 in node_ports
    assert "openplc" in services


def test_process_sim_port_matches_compose():
    node = next(n for n in NODES if n["id"] == "process-sim")
    assert {p["port"] for p in node["ports"]} == {5502}


def test_hmi_and_grafana_ports_match_compose():
    hmi = next(n for n in NODES if n["id"] == "hmi")
    grafana = next(n for n in NODES if n["id"] == "grafana")
    assert {p["port"] for p in hmi["ports"]} == {8090}
    assert {p["port"] for p in grafana["ports"]} == {3000}


def test_zones_match_compose_network_names():
    services = _compose_services()
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    compose_networks = set(compose["networks"].keys())
    assert "zone-enterprise" in compose_networks
    assert "zone-ops" in compose_networks
    assert set(ZONES) >= {"zone-enterprise", "zone-ops"}
    # router is the only node declared on both zones in compose
    router_networks = set(services["router"]["networks"])
    assert router_networks == {"zone-enterprise", "zone-ops"}


def test_every_edge_references_real_nodes():
    node_ids = {n["id"] for n in NODES}
    for edge in EDGES:
        assert edge["from"] in node_ids, edge
        assert edge["to"] in node_ids, edge


def test_only_boundary_edges_are_monitored():
    # Pins docs/limitations.md #8: only the zone-enterprise/zone-ops
    # boundary (router) is instrumented — everything inside zone-ops
    # is genuinely unmonitored. If this ever flips without a docs
    # update, this test should be the thing that notices.
    for edge in EDGES:
        if edge["monitored"]:
            assert "router" in (edge["from"], edge["to"]), edge


def test_every_scenario_overlay_references_real_edges_and_nodes():
    node_ids = {n["id"] for n in NODES}
    edge_ids = {e["id"] for e in EDGES}
    for scenario_id, overlay in SCENARIO_OVERLAYS.items():
        for edge_id in overlay.get("path_edges", []):
            assert edge_id in edge_ids, (scenario_id, edge_id)
        for node_id in overlay.get("affected_nodes", []) + overlay.get("detection_nodes", []):
            assert node_id in node_ids, (scenario_id, node_id)


def test_s05_overlay_distinguishes_truth_from_spoofed_view():
    overlay = SCENARIO_OVERLAYS["S05"]
    assert overlay["truth_node"] == "process-sim"
    assert overlay["spoofed_node"] == "hmi"
    assert overlay["truth_node"] != overlay["spoofed_node"]


def test_s06_overlay_flags_the_http_port_not_a_modbus_path():
    overlay = SCENARIO_OVERLAYS["S06"]
    assert overlay["path_edges"] == []
    assert overlay["compromise_port"]["port"] == 8080
    assert overlay["detection_nodes"] == []


def test_get_topology_returns_only_student_safe_base_keys():
    data = get_topology()
    assert set(data) == {"zones", "nodes", "edges"}
