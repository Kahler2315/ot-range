"""Instructor policies must be enforced by direct backend calls."""

from __future__ import annotations

import panel.app as panel_app

PASSWORD = "a sufficiently long instructor password"  # noqa: S105


def configure(client, **overrides):
    if client.get("/api/instructor/status").get_json()["configured"] is False:
        assert client.post("/api/instructor/setup", json={"password": PASSWORD}).status_code == 201
    response = client.put("/api/instructor/policies", json=overrides)
    assert response.status_code == 200
    return response.get_json()


def test_public_policy_response_is_student_safe(student_client):
    data = student_client.get("/api/policies").get_json()
    assert set(data) == {
        "scoredModeEnabled",
        "independentModeEnabled",
        "guidedModeEnabled",
        "hintsEnabled",
        "answerKeyEnabled",
        "walkthroughEnabled",
        "scenarioAvailability",
    }
    assert "password" not in str(data).lower()


def test_disabled_scenario_blocks_run_flags_and_docs(student_client, monkeypatch):
    configure(student_client, scenarioAvailability={"S01": False})
    monkeypatch.setattr(panel_app, "start_job", lambda command: "job-id")
    assert (
        student_client.post("/api/run", json={"scenario": "S01", "mode_index": 0}).status_code
        == 403
    )
    assert "S01" not in student_client.get("/api/flags").get_json()
    assert student_client.get("/api/docs/S01/briefing").status_code == 403


def test_hint_and_answer_key_policies_block_direct_requests(student_client):
    configure(student_client, hintsEnabled=False, answerKeyEnabled=False)
    assert student_client.get("/api/flags/S01/s01-hosts/hint/1").status_code == 403
    assert student_client.get("/api/docs/S01/answer-key").status_code == 403


def test_walkthrough_policy_blocks_detection_and_impact_docs(student_client):
    configure(student_client, walkthroughEnabled=False)
    assert student_client.get("/api/docs/S01/detection").status_code == 403
    assert student_client.get("/api/docs/S01/expected-impact").status_code == 403
    assert student_client.get("/api/docs/S01/briefing").status_code == 200


def test_independent_and_guided_mode_policies_are_backend_enforced(student_client):
    configure(student_client, guidedModeEnabled=False)
    guided = student_client.patch("/api/training/S01", json={"mode": "guided", "start": True})
    assert guided.status_code == 403

    configure(student_client, guidedModeEnabled=True, independentModeEnabled=False)
    independent = student_client.patch(
        "/api/training/S01", json={"mode": "independent", "start": True}
    )
    assert independent.status_code == 403
    guided_ok = student_client.patch("/api/training/S01", json={"mode": "guided", "start": True})
    assert guided_ok.status_code == 200


def test_scored_mode_disabled_changes_backend_score_behavior(student_client):
    configure(student_client, scoredModeEnabled=False)
    result = student_client.post(
        "/api/flags/check",
        json={
            "scenario": "S01",
            "flag_id": "s01-hosts",
            "answer": "2",
            "training_mode": "independent",
        },
    ).get_json()
    assert result["correct"] is True
    state = student_client.get("/api/training/S01").get_json()["state"]
    assert state["scored"] is False
    assert state["pointsEarned"] == {}


def test_scored_mode_disabled_is_visible_before_an_attempt_starts(student_client):
    configure(student_client, scoredModeEnabled=False)
    training = student_client.get("/api/training").get_json()
    assert all(not scenario["scored"] for scenario in training["scenarios"].values())
    report = student_client.get(f"/api/profiles/{training['profileId']}/export").get_json()
    assert all(item["score"] is None for item in report["scenarios"])


def test_instructor_policy_route_requires_at_least_one_training_mode(student_client):
    if student_client.get("/api/instructor/status").get_json()["configured"] is False:
        student_client.post("/api/instructor/setup", json={"password": PASSWORD})
    rejected = student_client.put(
        "/api/instructor/policies",
        json={"guidedModeEnabled": False, "independentModeEnabled": False},
    )
    assert rejected.status_code == 400


def test_instructor_policy_route_rejects_malformed_values(student_client):
    if student_client.get("/api/instructor/status").get_json()["configured"] is False:
        student_client.post("/api/instructor/setup", json={"password": PASSWORD})
    assert (
        student_client.put("/api/instructor/policies", json={"hintsEnabled": "false"}).status_code
        == 400
    )
    assert (
        student_client.put(
            "/api/instructor/policies", json={"scenarioAvailability": []}
        ).status_code
        == 400
    )
    assert (
        student_client.put(
            "/api/instructor/policies",
            json={"scenarioAvailability": {"S99": False}},
        ).status_code
        == 400
    )
