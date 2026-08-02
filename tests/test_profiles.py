"""Local learner profile API and profile-isolation tests."""

from __future__ import annotations

import panel.app as panel_app


def create_profile(client, name, **fields):
    response = client.post("/api/profiles", json={"display_name": name, **fields})
    assert response.status_code == 201
    return response.get_json()["profile"]


def test_profile_create_list_read_and_update(panel_client):
    profile = create_profile(
        panel_client,
        "Avery Analyst",
        learner_id="STU-42",
        organization="Cedar Hollow College",
        course="OT Defense",
        section="Evening",
        instructor_name="Morgan",
    )
    listed = panel_client.get("/api/profiles").get_json()
    assert listed["activeProfileId"] == profile["id"]
    assert listed["profiles"][0]["display_name"] == "Avery Analyst"

    read = panel_client.get(f"/api/profiles/{profile['id']}")
    assert read.status_code == 200
    assert read.get_json()["profile"]["learner_id"] == "STU-42"

    updated = panel_client.patch(
        f"/api/profiles/{profile['id']}",
        json={"display_name": "Avery Updated", "course": "OT Defense II"},
    )
    assert updated.status_code == 200
    assert updated.get_json()["profile"]["display_name"] == "Avery Updated"
    assert updated.get_json()["profile"]["organization"] == "Cedar Hollow College"


def test_profile_validation_rejects_missing_or_oversized_names(panel_client):
    assert panel_client.post("/api/profiles", json={}).status_code == 400
    assert panel_client.post("/api/profiles", json={"display_name": "x" * 121}).status_code == 400


def test_active_student_context_isolates_profile_data(panel_client):
    first = create_profile(panel_client, "First Learner")
    panel_client.patch("/api/training/S01", json={"mode": "independent", "start": True})
    second = create_profile(panel_client, "Second Learner")

    denied = panel_client.get(f"/api/profiles/{first['id']}")
    assert denied.status_code == 400
    second_data = panel_client.get(f"/api/profiles/{second['id']}").get_json()
    assert second_data["training"]["scenarios"]["S01"]["status"] == "not_started"

    selected = panel_client.post(f"/api/profiles/{first['id']}/select")
    assert selected.status_code == 200
    first_data = panel_client.get(f"/api/profiles/{first['id']}").get_json()
    assert first_data["training"]["scenarios"]["S01"]["status"] == "in_progress"


def test_profile_delete_requires_confirmation_and_cascades(panel_client):
    profile = create_profile(panel_client, "Delete Me")
    panel_client.patch("/api/training/S01", json={"mode": "guided", "start": True})

    rejected = panel_client.delete(
        f"/api/profiles/{profile['id']}", json={"confirm_display_name": "wrong"}
    )
    assert rejected.status_code == 400
    deleted = panel_client.delete(
        f"/api/profiles/{profile['id']}", json={"confirm_display_name": "Delete Me"}
    )
    assert deleted.status_code == 200
    with panel_app.app.app_context():
        assert panel_app.get_storage().get_profile(profile["id"]) is None
        assert panel_app.get_storage().get_attempt(profile["id"], "S01") is None


def test_profile_attempt_reset_and_reset_all(student_client):
    profile_id = student_client.profile["id"]
    student_client.patch("/api/training/S01", json={"mode": "independent", "start": True})
    student_client.patch("/api/training/S03", json={"mode": "guided", "start": True})

    reset_one = student_client.post(f"/api/profiles/{profile_id}/progress/S01/reset")
    assert reset_one.get_json()["state"]["status"] == "not_started"
    assert student_client.get("/api/training/S03").get_json()["state"]["status"] == "in_progress"

    reset_all = student_client.post(f"/api/profiles/{profile_id}/reset-all")
    scenarios = reset_all.get_json()["training"]["scenarios"]
    assert all(state["status"] == "not_started" for state in scenarios.values())


def test_profile_export_contains_identity_fields(student_client):
    profile_id = student_client.profile["id"]
    exported = student_client.get(f"/api/profiles/{profile_id}/export")
    assert exported.status_code == 200
    data = exported.get_json()
    assert data["profile"]["displayName"] == "Test Learner"
    assert data["profile"]["localProfileId"] == profile_id
    assert set(data["profile"]) == {
        "displayName",
        "learnerId",
        "organization",
        "course",
        "section",
        "instructorName",
        "localProfileId",
    }
    assert len(data["scenarios"]) == 4
    assert "Local training progress" in data["disclaimer"]
