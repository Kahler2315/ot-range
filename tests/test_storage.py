"""Database schema and persistence invariants for the local training app."""

from __future__ import annotations

import sqlite3

import pytest

from panel.storage import SCHEMA_VERSION, Storage, StorageConflict, UnsupportedSchemaVersion


@pytest.fixture
def storage(tmp_path):
    db = Storage(tmp_path / "instance" / "ot-range.db")
    db.initialize(["S01", "S03", "S05", "S06"])
    return db


def profile_fields(name="Learner One"):
    return {
        "display_name": name,
        "learner_id": "L-100",
        "organization": "Cedar Hollow College",
        "course": "ICS 201",
        "section": "A",
        "instructor_name": "Instructor",
    }


def test_fresh_database_creates_versioned_schema_and_default_policies(tmp_path):
    path = tmp_path / "nested" / "ot-range.db"
    storage = Storage(path)
    storage.initialize(["S01", "S03"])

    assert path.is_file()
    assert storage.schema_version() == SCHEMA_VERSION
    policies = storage.get_policies()
    assert policies["hints_enabled"] is True
    assert policies["independent_mode_enabled"] is True
    assert policies["guided_mode_enabled"] is True
    assert policies["scenario_availability"] == {"S01": True, "S03": True}


def test_unknown_schema_version_is_rejected(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 99")
    with pytest.raises(UnsupportedSchemaVersion):
        Storage(path).initialize()


def test_every_connection_enables_foreign_keys(storage):
    with storage.connection() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_profile_create_read_update_and_list(storage):
    created = storage.create_profile("p1", profile_fields())
    assert created["id"] == "p1"
    assert created["display_name"] == "Learner One"

    updated_fields = profile_fields("Learner Renamed")
    updated_fields["course"] = "ICS 301"
    updated = storage.update_profile("p1", updated_fields)
    assert updated["display_name"] == "Learner Renamed"
    assert updated["course"] == "ICS 301"
    assert [profile["id"] for profile in storage.list_profiles()] == ["p1"]


def test_profile_delete_cascades_only_its_own_progress(storage):
    storage.create_profile("p1", profile_fields("One"))
    storage.create_profile("p2", profile_fields("Two"))
    storage.ensure_attempt("p1", "S01", mode="independent", scored=True, start=True)
    storage.ensure_attempt("p2", "S01", mode="guided", scored=True, start=True)
    storage.reveal_hint("p1", "S01", "s01-hosts", 1, 1)

    assert storage.delete_profile("p1") is True
    assert storage.get_profile("p1") is None
    assert storage.get_attempt("p1", "S01") is None
    assert storage.get_profile("p2") is not None
    assert storage.get_attempt("p2", "S01")["mode"] == "guided"


def test_student_session_is_opaque_and_cascades_with_profile(storage):
    storage.create_profile("p1", profile_fields())
    token = "opaque-session-token"  # noqa: S105 -- test-only opaque identifier
    storage.create_student_session(token, "p1")
    assert storage.get_student_session(token)["profile_id"] == "p1"
    with storage.connection() as conn:
        stored = conn.execute("SELECT token_hash FROM student_sessions").fetchone()[0]
    assert stored != token

    storage.delete_profile("p1")
    assert storage.get_student_session(token) is None


def test_policy_update_is_transactional_and_requires_one_training_mode(storage):
    updated = storage.update_policies(
        {"hints_enabled": False, "guided_mode_enabled": False},
        {"S03": False},
    )
    assert updated["hints_enabled"] is False
    assert updated["guided_mode_enabled"] is False
    assert updated["scenario_availability"]["S03"] is False

    with pytest.raises(StorageConflict):
        storage.update_policies({"independent_mode_enabled": False, "guided_mode_enabled": False})
    unchanged = storage.get_policies()
    assert unchanged["independent_mode_enabled"] is True


def test_hint_reveals_are_sequential_and_deduct_once(storage):
    storage.create_profile("p1", profile_fields())
    storage.ensure_attempt("p1", "S01", mode="independent", scored=True, start=True)

    with pytest.raises(StorageConflict):
        storage.reveal_hint("p1", "S01", "s01-hosts", 2, 2)
    assert storage.reveal_hint("p1", "S01", "s01-hosts", 1, 1) is True
    assert storage.reveal_hint("p1", "S01", "s01-hosts", 1, 1) is False
    assert storage.reveal_hint("p1", "S01", "s01-hosts", 2, 2) is True

    hints = storage.get_attempt("p1", "S01")["hints"]
    assert [(hint["level"], hint["point_cost"]) for hint in hints] == [(1, 1), (2, 2)]


def test_correct_flag_points_are_preserved_on_repeat_submission(storage):
    storage.create_profile("p1", profile_fields())
    storage.ensure_attempt("p1", "S01", mode="independent", scored=True, start=True)
    storage.record_flag_submission(
        "p1",
        "S01",
        "f1",
        correct=True,
        points_earned=6,
        all_flag_ids=["f1", "f2"],
    )
    state = storage.record_flag_submission(
        "p1",
        "S01",
        "f1",
        correct=True,
        points_earned=1,
        all_flag_ids=["f1", "f2"],
    )
    flag = next(item for item in state["flags"] if item["flag_id"] == "f1")
    assert flag["points_earned"] == 6
    assert flag["attempt_count"] == 2


def test_solution_reveal_locks_submission_and_preserves_points(storage):
    storage.create_profile("p1", profile_fields())
    storage.ensure_attempt("p1", "S01", mode="independent", scored=True, start=True)
    storage.record_flag_submission(
        "p1", "S01", "f1", correct=True, points_earned=6, all_flag_ids=["f1", "f2"]
    )
    state = storage.record_document_open(
        "p1", "S01", "answer-key", solution=True, lock_solution=True
    )
    assert state["status"] == "solution_revealed"
    assert state["solution_locked"] == 1
    assert state["flags"][0]["points_earned"] == 6

    with pytest.raises(StorageConflict):
        storage.record_flag_submission(
            "p1", "S01", "f2", correct=True, points_earned=5, all_flag_ids=["f1", "f2"]
        )


def test_attempt_and_profile_progress_reset(storage):
    storage.create_profile("p1", profile_fields())
    storage.ensure_attempt("p1", "S01", mode="independent", scored=True, start=True)
    storage.ensure_attempt("p1", "S03", mode="guided", scored=True, start=True)

    assert storage.reset_attempt("p1", "S01") is True
    current = storage.get_attempt("p1", "S01")
    assert current["attempt_number"] == 2
    assert current["status"] == "not_started"
    history = storage.list_attempt_history("p1", "S01")
    assert len(history) == 2
    assert history[0]["reset_actor"] == "student"
    assert storage.get_attempt("p1", "S03") is not None
    assert storage.reset_profile_progress("p1") == 2
    assert all(attempt["status"] == "not_started" for attempt in storage.list_attempts("p1"))
    events = storage.list_training_events(profile_id="p1")
    assert any(event["event_type"] == "attempt_reset" for event in events)
    assert any(event["event_type"] == "profile_progress_reset" for event in events)


def test_v1_schema_migrates_attempt_children_without_data_loss(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE application_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE profiles (
                id TEXT PRIMARY KEY, display_name TEXT NOT NULL, learner_id TEXT,
                organization TEXT, course TEXT, section TEXT, instructor_name TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_activity_at TEXT NOT NULL
            );
            CREATE TABLE scenario_availability (
                scenario_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE scenario_attempts (
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                scenario_id TEXT NOT NULL, status TEXT NOT NULL, mode TEXT, scored INTEGER NOT NULL,
                started_at TEXT, completed_at TEXT, solution_locked INTEGER NOT NULL,
                notes TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (profile_id, scenario_id)
            );
            CREATE TABLE flag_progress (
                profile_id TEXT NOT NULL, scenario_id TEXT NOT NULL, flag_id TEXT NOT NULL,
                attempt_count INTEGER NOT NULL, solved INTEGER NOT NULL, points_earned INTEGER,
                solved_at TEXT, PRIMARY KEY (profile_id, scenario_id, flag_id),
                FOREIGN KEY (profile_id, scenario_id)
                    REFERENCES scenario_attempts(profile_id, scenario_id) ON DELETE CASCADE
            );
            CREATE TABLE hint_reveals (
                profile_id TEXT NOT NULL, scenario_id TEXT NOT NULL, flag_id TEXT NOT NULL,
                level INTEGER NOT NULL, point_cost INTEGER NOT NULL, revealed_at TEXT NOT NULL,
                PRIMARY KEY (profile_id, scenario_id, flag_id, level),
                FOREIGN KEY (profile_id, scenario_id)
                    REFERENCES scenario_attempts(profile_id, scenario_id) ON DELETE CASCADE
            );
            CREATE TABLE opened_documents (
                profile_id TEXT NOT NULL, scenario_id TEXT NOT NULL, document_key TEXT NOT NULL,
                opened_at TEXT NOT NULL, PRIMARY KEY (profile_id, scenario_id, document_key),
                FOREIGN KEY (profile_id, scenario_id)
                    REFERENCES scenario_attempts(profile_id, scenario_id) ON DELETE CASCADE
            );
            INSERT INTO profiles VALUES ('p1', 'Legacy', NULL, NULL, NULL, NULL, NULL,
                                         't0', 't0', 't0');
            INSERT INTO scenario_attempts VALUES (
                'p1', 'S01', 'in_progress', 'independent', 1, 't0', NULL, 0,
                'legacy notes', 't0', 't0'
            );
            INSERT INTO flag_progress VALUES ('p1', 'S01', 'f1', 2, 1, 6, 't1');
            INSERT INTO hint_reveals VALUES ('p1', 'S01', 'f2', 1, 1, 't1');
            INSERT INTO opened_documents VALUES ('p1', 'S01', 'briefing', 't1');
            PRAGMA user_version = 1;
            """
        )
    migrated = Storage(path)
    migrated.initialize(["S01"])
    assert migrated.schema_version() == SCHEMA_VERSION
    attempt = migrated.get_attempt("p1", "S01")
    assert attempt["attempt_number"] == 1
    assert attempt["notes"] == "legacy notes"
    assert attempt["flags"][0]["points_earned"] == 6
    assert attempt["hints"][0]["level"] == 1
    assert attempt["documents"][0]["document_key"] == "briefing"
    assert migrated.database_id() != "unknown"
