"""Profile-scoped lifecycle, scoring, hints, solution locking, and reports."""

from __future__ import annotations


def test_training_state_has_clean_backend_cutover_and_no_localstorage_dependency(student_client):
    data = student_client.get("/api/training").get_json()
    assert data["profileId"] == student_client.profile["id"]
    assert all(state["status"] == "not_started" for state in data["scenarios"].values())
    assert "ot-range-training-v1" not in student_client.get("/student").get_data(as_text=True)


def test_incorrect_and_correct_flag_submissions_are_recorded_and_scored(student_client):
    wrong = student_client.post(
        "/api/flags/check",
        json={
            "scenario": "S01",
            "flag_id": "s01-hosts",
            "answer": "wrong",
            "training_mode": "independent",
        },
    )
    assert wrong.status_code == 200
    assert wrong.get_json()["correct"] is False
    wrong_state = student_client.get("/api/training/S01").get_json()["state"]
    assert wrong_state["flagAttempts"]["s01-hosts"] == 1

    correct = student_client.post(
        "/api/flags/check",
        json={"scenario": "S01", "flag_id": "s01-hosts", "answer": "two"},
    )
    assert correct.get_json()["correct"] is True
    state = student_client.get("/api/training/S01").get_json()["state"]
    assert state["flagAttempts"]["s01-hosts"] == 2
    assert state["pointsEarned"]["s01-hosts"] == 6
    assert state["flagsSolved"] == ["s01-hosts"]


def test_hint_reveal_is_sequential_and_charged_exactly_once(student_client):
    out_of_order = student_client.get("/api/flags/S01/s01-hosts/hint/2")
    assert out_of_order.status_code == 409
    first = student_client.get("/api/flags/S01/s01-hosts/hint/1")
    repeated = student_client.get("/api/flags/S01/s01-hosts/hint/1")
    second = student_client.get("/api/flags/S01/s01-hosts/hint/2")
    assert first.get_json()["newlyRevealed"] is True
    assert repeated.get_json()["newlyRevealed"] is False
    assert second.get_json()["newlyRevealed"] is True
    assert second.get_json()["state"]["hintsRevealed"]["s01-hosts"] == [1, 2]

    assert (
        student_client.post(
            "/api/flags/check",
            json={"scenario": "S01", "flag_id": "s01-hosts", "answer": "2"},
        ).get_json()["correct"]
        is True
    )
    solved = student_client.get("/api/training/S01").get_json()["state"]
    assert solved["pointsEarned"]["s01-hosts"] == 3


def test_guided_hints_are_free(student_client):
    student_client.patch("/api/training/S01", json={"mode": "guided", "start": True})
    hint = student_client.get("/api/flags/S01/s01-hosts/hint/1")
    assert hint.get_json()["cost"] == 0
    assert (
        student_client.post(
            "/api/flags/check",
            json={"scenario": "S01", "flag_id": "s01-hosts", "answer": "2"},
        ).get_json()["correct"]
        is True
    )
    solved = student_client.get("/api/training/S01").get_json()["state"]
    assert solved["pointsEarned"]["s01-hosts"] == 6


def test_answer_key_reveal_locks_independent_attempt_and_preserves_score(student_client):
    student_client.post(
        "/api/flags/check",
        json={
            "scenario": "S01",
            "flag_id": "s01-hosts",
            "answer": "2",
            "training_mode": "independent",
        },
    )
    opened = student_client.get("/api/docs/S01/answer-key")
    assert opened.status_code == 200
    state = student_client.get("/api/training/S01").get_json()["state"]
    assert state["status"] == "solution_revealed"
    assert state["solutionLocked"] is True
    assert state["pointsEarned"]["s01-hosts"] == 6

    rejected = student_client.post(
        "/api/flags/check",
        json={"scenario": "S01", "flag_id": "s01-source", "answer": "127.0.0.2"},
    )
    assert rejected.status_code == 409


def test_answer_key_does_not_lock_guided_attempt(student_client):
    student_client.patch("/api/training/S01", json={"mode": "guided", "start": True})
    assert student_client.get("/api/docs/S01/answer-key").status_code == 200
    state = student_client.get("/api/training/S01").get_json()["state"]
    assert state["solutionLocked"] is False
    assert (
        student_client.post(
            "/api/flags/check",
            json={"scenario": "S01", "flag_id": "s01-hosts", "answer": "2"},
        ).status_code
        == 200
    )


def test_notes_and_opened_documents_are_backend_visible_in_report(student_client):
    profile_id = student_client.profile["id"]
    student_client.patch(
        "/api/training/S01", json={"mode": "independent", "notes": "Investigated FC4."}
    )
    student_client.get("/api/docs/S01/briefing")
    report = student_client.get(f"/api/profiles/{profile_id}/export").get_json()
    scenario = next(item for item in report["scenarios"] if item["scenario"] == "S01")
    assert scenario["notes"] == "Investigated FC4."
    assert scenario["documentationOpened"] == ["briefing"]
