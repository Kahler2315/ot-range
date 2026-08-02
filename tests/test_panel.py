"""panel/app.py had zero test coverage before this. Uses Flask's
test_client(), no Docker/subprocess needed for these — the concurrent
-job test uses a cheap real `sleep` command instead of an actual make
target, keeping this hermetic and fast."""

from __future__ import annotations

import re
import time

import pytest

import panel.app as panel_app
from scenarios.flags import FLAGS_BY_SCENARIO


@pytest.fixture
def client(student_client):
    return student_client


def test_index_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"OT Range" in resp.data or b"OT_RANGE" in resp.data


def test_workspace_chooser_and_student_lab_are_clearly_separated(client):
    chooser = client.get("/").get_data(as_text=True)
    assert "Choose your workspace" in chooser
    assert "Student Lab" in chooser
    assert "Instructor Console" in chooser
    assert "Student profiles identify local training records" in chooser

    student = client.get("/student").get_data(as_text=True)
    assert "Student Lab" in student
    assert "Training Policies" not in student
    assert "Instructor Settings" not in student


def test_student_source_has_metadata_and_no_accepted_answers(client):
    html = client.get("/student").get_data(as_text=True)
    assert "Difficulty" in html
    assert "Estimated time" in html
    assert "Process impact:" in html
    assert '"accepted"' not in html


def test_frontend_clean_cutover_has_no_authoritative_localstorage_key():
    for path in (
        panel_app.REPO_ROOT / "panel/static/app.js",
        panel_app.REPO_ROOT / "panel/static/training.js",
        panel_app.REPO_ROOT / "panel/static/student.js",
        panel_app.REPO_ROOT / "panel/static/instructor.js",
        panel_app.REPO_ROOT / "panel/static/auth.js",
    ):
        source = path.read_text()
        assert "ot-range-training-v1" not in source
        assert "instructor-authenticated" not in source


def test_api_status_shape(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "docker" in data
    assert "busy" in data


def test_api_flags_includes_points_and_hint_costs_never_hint_text(client):
    resp = client.get("/api/flags")
    assert resp.status_code == 200
    data = resp.get_json()
    payload_str = resp.get_data(as_text=True)

    assert "S01" in data
    flag = data["S01"][0]
    assert "points" in flag
    assert "hintCosts" in flag
    assert isinstance(flag["hintCosts"], list)
    assert "category" in flag
    assert "evidenceSource" in flag

    for flags in FLAGS_BY_SCENARIO.values():
        for f in flags:
            for hint in f.hints:
                assert hint.text not in payload_str, f"hint text for {f.id} leaked into /api/flags"


def test_api_flags_hint_returns_text_for_valid_level(client):
    resp = client.get("/api/flags/S01/s01-hosts/hint/1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "text" in data
    assert "cost" in data
    assert data["text"]  # non-empty


def test_api_flags_hint_404s_for_invalid_level(client):
    assert client.get("/api/flags/S01/s01-hosts/hint/99").status_code == 404
    assert client.get("/api/flags/S01/s01-hosts/hint/0").status_code == 404


def test_api_flags_hint_404s_for_unknown_flag_or_scenario(client):
    assert client.get("/api/flags/S99/x/hint/1").status_code == 404
    assert client.get("/api/flags/S01/not-a-real-flag/hint/1").status_code == 404


def test_api_flags_check_unchanged_contract(client):
    resp = client.post(
        "/api/flags/check",
        json={"scenario": "S01", "flag_id": "s01-hosts", "answer": "2"},
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"correct": True}

    resp2 = client.post(
        "/api/flags/check",
        json={"scenario": "S01", "flag_id": "s01-hosts", "answer": "wrong"},
    )
    assert resp2.get_json() == {"correct": False}


def test_api_topology_shape(client):
    resp = client.get("/api/topology")
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data) == {"zones", "nodes", "edges", "overlays"}
    assert len(data["nodes"]) > 0


def test_api_docs_unchanged_behavior(client):
    resp = client.get("/api/docs/S01/briefing")
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"

    assert client.get("/api/docs/S01/not-a-real-doc").status_code == 404
    assert client.get("/api/docs/S99/briefing").status_code == 404


def test_concurrent_job_guard_rejects_second_job_while_busy(client):
    # A slow-ish real command so the busy window is observable without
    # making the test flaky or slow.
    job_id_1 = panel_app.start_job(["sleep", "0.3"])
    assert job_id_1 is not None

    job_id_2 = panel_app.start_job(["sleep", "0.1"])
    assert job_id_2 is None, "a second job must not start while one is running"

    time.sleep(0.5)
    job_id_3 = panel_app.start_job(["sleep", "0.05"])
    assert job_id_3 is not None, "a new job should be startable once the first finishes"
    time.sleep(0.2)


def test_api_stack_rejects_unknown_action(client):
    resp = client.post("/api/stack/not-a-real-action")
    assert resp.status_code == 404


def test_api_run_rejects_unknown_scenario(client):
    resp = client.post("/api/run", json={"scenario": "S99", "mode_index": 0})
    assert resp.status_code == 400


def test_api_run_rejects_unknown_mode_index(client):
    resp = client.post("/api/run", json={"scenario": "S01", "mode_index": 99})
    assert resp.status_code == 400


@pytest.mark.parametrize("payload", [[], ["not", "an", "object"]])
def test_mutating_apis_reject_non_object_json(client, payload):
    assert client.post("/api/run", json=payload).status_code == 400
    assert client.post("/api/flags/check", json=payload).status_code == 400


def test_api_run_rejects_negative_or_wrong_type_mode(client):
    assert client.post("/api/run", json={"scenario": "S01", "mode_index": -1}).status_code == 400
    assert client.post("/api/run", json={"scenario": ["S01"], "mode_index": 0}).status_code == 400


def test_sidebar_nav_targets_all_resolve(client):
    # Every sidebar nav-link's data-nav must be either a real element
    # id on the page (so the scroll-position highlight can track it) or
    # "console" (the one nav item that opens the fixed console drawer
    # directly instead of scrolling, wired
    # explicitly in app.js's wireSidebarNav). A link pointing at
    # neither can never highlight or navigate anywhere — this caught a
    # real bug where "Console" pointed at a nonexistent
    # #console-section anchor.
    html = client.get("/student").get_data(as_text=True)
    nav_targets = re.findall(r'class="nav-link"[^>]*data-nav="([^"]+)"', html)
    assert nav_targets, "no sidebar nav links found"
    for target in nav_targets:
        if target == "console":
            continue
        assert re.search(rf'id="{re.escape(target)}"', html), (
            f"no element with id={target!r} for nav target"
        )


def test_sidebar_scroll_links_follow_workspace_section_order(client):
    """The visible navigation order should match the page's reading order."""
    html = client.get("/student").get_data(as_text=True)
    nav_targets = re.findall(r'class="nav-link"[^>]*data-nav="([^"]+)"', html)
    scroll_targets = [target for target in nav_targets if target != "console"]
    section_targets = re.findall(r'<section id="([^"]+)" class="workspace-section"', html)
    assert scroll_targets == section_targets


def test_map_overlay_warning_is_dismissible(client):
    html = client.get("/student").get_data(as_text=True)
    assert 'id="map-overlay-locked-note"' in html
    assert 'id="map-overlay-locked-message"' in html
    assert 'id="map-overlay-locked-close"' in html
    assert 'aria-label="Dismiss scenario overlay warning"' in html


def test_answer_key_warning_uses_explicit_lock_and_back_actions():
    source = (panel_app.REPO_ROOT / "panel/static/training.js").read_text()
    assert "Opening the answer key will lock this attempt" in source
    assert 'confirmLabel: "Continue and lock attempt"' in source
    assert 'cancelLabel: "Go back"' in source


def test_network_map_exposes_local_service_links(client):
    html = client.get("/student").get_data(as_text=True)
    assert 'href="http://localhost:8080"' in html
    assert 'href="http://localhost:8090"' in html
    assert 'href="http://localhost:3000"' in html
    assert 'class="header-service-links"' in html
    assert "OpenPLC" in html
    assert "Grafana" in html


def test_student_header_exposes_copyable_range_tool_logins(client):
    html = client.get("/student").get_data(as_text=True)
    assert "Tool logins" in html
    assert 'data-copy-value="openplc"' in html
    assert 'data-copy-value="admin"' in html
    assert "not your instructor password" in html


def test_dark_theme_metadata_and_svg_paint_are_explicit(client):
    html = client.get("/student").get_data(as_text=True)
    styles = (panel_app.REPO_ROOT / "panel/static/styles.css").read_text()
    networkmap = (panel_app.REPO_ROOT / "panel/static/networkmap.js").read_text()

    assert '<meta name="color-scheme" content="dark only">' in html
    assert '<meta name="theme-color" content="#0b0c0e">' in html
    for token in (
        "--map-node-surface",
        "--map-node-border",
        "--map-node-primary",
        "--map-node-secondary",
        "--map-badge-surface",
        "--map-protocol-surface",
        "--map-zone-trusted",
        "--map-health-ok",
        "--map-health-bad",
        "--map-traffic-monitored",
        "--map-traffic-attack",
        "--map-selected-surface",
        "--map-detection-surface",
    ):
        assert token in styles
    assert "filter: none" in styles
    assert "feDropShadow" not in networkmap
