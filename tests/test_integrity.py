"""Durable learner history and backend spoiler-gating regressions."""

from __future__ import annotations

import json

import panel.app as panel_app
from panel.storage import Storage
from scenarios.catalog import SCENARIOS_BY_ID

PASSWORD = "a sufficiently long instructor password"  # noqa: S105


def reveal(student_client, scenario: str, document: str):
    response = student_client.post(f"/api/docs/{scenario}/{document}/reveal")
    assert response.status_code == 200
    return response.get_json()["state"]


def test_solution_reveal_reset_and_export_preserve_both_attempts(student_client):
    profile_id = student_client.profile["id"]
    student_client.post(
        "/api/flags/check",
        json={
            "scenario": "S01",
            "flag_id": "s01-hosts",
            "answer": "2",
            "training_mode": "independent",
        },
    )
    locked = reveal(student_client, "S01", "answer-key")
    assert locked["solutionLocked"] is True

    reset = student_client.post(f"/api/profiles/{profile_id}/progress/S01/reset")
    assert reset.status_code == 200
    assert reset.get_json()["state"]["attemptNumber"] == 2
    assert reset.get_json()["state"]["priorSolutionExposure"] is True

    report = student_client.get(f"/api/profiles/{profile_id}/export").get_json()
    scenario = next(item for item in report["scenarios"] if item["scenario"] == "S01")
    assert scenario["totalAttempts"] == 2
    assert len(scenario["attemptHistory"]) == 2
    assert scenario["attemptHistory"][0]["solutionRevealed"] is True
    assert scenario["attemptHistory"][0]["resetActor"] == "student"
    assert scenario["attemptHistory"][1]["practiceAfterSolutionReview"] is True
    assert report["integritySummary"]["priorSolutionReveals"] == 1
    assert report["integritySummary"]["totalResets"] == 1


def test_solution_document_direct_get_is_denied_until_explicit_transition(student_client):
    student_client.patch("/api/training/S03", json={"mode": "independent", "start": True})
    assert student_client.get("/api/docs/S03/detection").status_code == 403
    assert student_client.get("/api/docs/S03/expected-impact").status_code == 403
    reveal(student_client, "S03", "detection")
    assert student_client.get("/api/docs/S03/detection").status_code == 200
    assert student_client.get("/api/training/S03").get_json()["state"]["solutionLocked"]


def test_guided_solution_document_access_is_recorded_without_lock(student_client):
    student_client.patch("/api/training/S03", json={"mode": "guided", "start": True})
    assert student_client.get("/api/docs/S03/detection").status_code == 200
    state = student_client.get("/api/training/S03").get_json()["state"]
    assert state["solutionLocked"] is False
    assert state["walkthroughOpened"] is True
    with panel_app.app.app_context():
        events = panel_app.get_storage().list_training_events(
            profile_id=student_client.profile["id"]
        )
    assert any(event["event_type"] == "solution_document_opened" for event in events)


def test_base_topology_is_neutral_and_overlay_is_backend_gated(student_client):
    base = student_client.get("/api/topology").get_json()
    serialized = json.dumps(base).lower()
    assert set(base) == {"zones", "nodes", "edges"}
    for spoiler in ("path_edges", "affected_nodes", "truth_node", "spoofed_node"):
        assert spoiler not in serialized

    student_client.patch("/api/training/S05", json={"mode": "independent", "start": True})
    assert student_client.get("/api/topology/overlays/S05").status_code == 403
    profile_id = student_client.profile["id"]
    student_client.post(f"/api/profiles/{profile_id}/progress/S05/reset")
    student_client.patch("/api/training/S05", json={"mode": "guided", "start": True})
    allowed = student_client.get("/api/topology/overlays/S05")
    assert allowed.status_code == 200
    assert allowed.get_json()["overlay"]["truth_node"] == "process-sim"


def test_student_scenario_payload_omits_outcome_fields(student_client):
    payload = student_client.get("/api/scenarios").get_json()["scenarios"]
    serialized = json.dumps(payload)
    assert "caught_by" not in serialized
    assert "detection_coverage_state" not in serialized
    assert "MODBUS_VIEW_MANIPULATION" not in serialized
    assert "interlock is deleted" not in serialized.lower()
    flags = student_client.get("/api/flags").get_json()
    assert all(flag["id"] != "s06-creds" for flag in flags["S06"])


def test_known_briefing_answer_leaks_are_removed():
    briefings = {
        scenario_id: (scenario.dirname / "briefing.md").read_text().lower()
        for scenario_id, scenario in SCENARIOS_BY_ID.items()
    }
    assert "showed the plant in **manual** mode" not in briefings["S03"]
    assert "tripped on overload" not in briefings["S03"]
    assert "steady, unremarkable 50%" not in briefings["S05"]
    assert "function code 4, read-only" not in briefings["S05"]
    assert "`lshh_101` never wavers" not in briefings["S05"]
    assert "cedar_hollow_s06_no_interlock.st" not in briefings["S06"]
    assert "openplc/openplc" not in briefings["S06"]


def test_integrity_events_are_instructor_only_and_acknowledgment_persists(student_client):
    profile_id = student_client.profile["id"]
    reveal(student_client, "S01", "answer-key")
    student_client.post(f"/api/profiles/{profile_id}/progress/S01/reset")

    assert student_client.get("/api/instructor/integrity-events").status_code == 401
    assert (
        student_client.post("/api/instructor/setup", json={"password": PASSWORD}).status_code
        == 201
    )
    events = student_client.get("/api/instructor/integrity-events").get_json()["events"]
    reset_event = next(event for event in events if event["event_type"] == "attempt_reset")
    assert reset_event["details"]["prior_solution_exposure"] is True
    assert reset_event["acknowledged"] is False
    assert (
        student_client.post(
            f"/api/instructor/integrity-events/{reset_event['id']}/acknowledge"
        ).status_code
        == 200
    )

    with panel_app.app.app_context():
        storage = Storage(panel_app.get_storage().path)
        persisted = storage.list_training_events(integrity_only=True)
    assert next(event for event in persisted if event["id"] == reset_event["id"])[
        "acknowledged"
    ] is True


def test_training_event_details_never_store_submitted_answer(student_client):
    secret_answer = "do-not-persist-this-submission"  # noqa: S105
    student_client.post(
        "/api/flags/check",
        json={
            "scenario": "S01",
            "flag_id": "s01-hosts",
            "answer": secret_answer,
            "training_mode": "independent",
        },
    )
    with panel_app.app.app_context():
        events = panel_app.get_storage().list_training_events(
            profile_id=student_client.profile["id"]
        )
    assert secret_answer not in json.dumps(events)


def test_status_degrades_cleanly_when_docker_executable_is_missing(panel_client, monkeypatch):
    def missing(_name):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(panel_app, "docker_container_status", missing)
    response = panel_client.get("/api/status")
    assert response.status_code == 200
    status = response.get_json()
    assert status["docker"]["any_present"] is False
    assert status["docker"]["all_healthy"] is False
    assert all(container["state"] == "absent" for container in status["docker"]["containers"])


def test_scenario_execution_is_linked_to_current_attempt(student_client, monkeypatch):
    monkeypatch.setattr(
        panel_app, "start_job", lambda _command, **_kwargs: "fixed-job-id"
    )
    monkeypatch.setattr(
        panel_app,
        "register_job_completion",
        lambda job_id, callback: callback(job_id, 0),
    )
    response = student_client.post(
        "/api/run",
        json={
            "scenario": "S01",
            "mode_index": 0,
            "training_mode": "independent",
        },
    )
    assert response.status_code == 200
    state = student_client.get("/api/training/S01").get_json()["state"]
    assert state["attemptId"] == response.get_json()["attempt_id"]
    assert state["executions"] == [
        {
            "execution_mode": "Quick Simulation",
            "finished_at": state["executions"][0]["finished_at"],
            "job_id": "fixed-job-id",
            "return_code": 0,
            "started_at": state["executions"][0]["started_at"],
        }
    ]
    report = student_client.get(
        f"/api/profiles/{student_client.profile['id']}/export"
    ).get_json()
    scenario = next(item for item in report["scenarios"] if item["scenario"] == "S01")
    assert scenario["practicalExecutionVerified"] is True
    assert scenario["completionClaim"] == "scenario_run_verified"


def test_student_console_filter_hides_attacker_narration_but_keeps_detection_evidence():
    filter_line = panel_app.student_scenario_output_filter()
    assert filter_line("[*] writing MODE_AUTO = 0") is None
    assert filter_line("[*] freezes LT_101 at 50.0%") is None
    assert filter_line(" WHAT THE SENSOR CAUGHT") == " WHAT THE SENSOR CAUGHT"
    assert (
        filter_line("[CRITICAL] MODBUS_UNAUTHORIZED_WRITE")
        == "[CRITICAL] MODBUS_UNAUTHORIZED_WRITE"
    )
    assert "answer-key" not in filter_line("[i] walkthrough : briefing, then answer-key.md")
