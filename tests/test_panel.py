"""panel/app.py had zero test coverage before this. Uses Flask's
test_client(), no Docker/subprocess needed for these — the concurrent
-job test uses a cheap real `sleep` command instead of an actual make
target, keeping this hermetic and fast."""

from __future__ import annotations

import time

import pytest

import panel.app as panel_app
from scenarios.flags import FLAGS_BY_SCENARIO


@pytest.fixture
def client():
    panel_app.app.testing = True
    with panel_app.app.test_client() as c:
        yield c
    # Reset global job state between tests — panel/app.py's job
    # tracking is module-level, same as the real process.
    with panel_app._jobs_guard:
        panel_app._jobs.clear()
        panel_app._current_job_id = None


def test_index_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"OT Range" in resp.data or b"OT_RANGE" in resp.data


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
