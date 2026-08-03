"""SQLite persistence for the local OT Range training application.

The web layer deliberately contains no SQL.  This module owns schema
creation, version checks, transactions, foreign-key enforcement, and
all persistence operations for instructor auth, local learner profiles,
policies, and profile-scoped training records.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
DEFAULT_POLICIES = {
    "scored_mode_enabled": True,
    "independent_mode_enabled": True,
    "guided_mode_enabled": True,
    "hints_enabled": True,
    "answer_key_enabled": True,
    "walkthrough_enabled": True,
}
INTEGRITY_EVENT_TYPES = {
    "answer_key_opened",
    "solution_document_opened",
    "attempt_reset",
    "profile_progress_reset",
    "instructor_override",
}
SOLUTION_RESOURCE_KEYS = {"answer-key", "detection", "expected-impact", "walkthrough"}


class UnsupportedSchemaVersion(RuntimeError):
    """Raised when the database is newer or older than this application."""


class StorageConflict(RuntimeError):
    """Raised for state transitions that cannot be applied safely."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def token_digest(token: str) -> str:
    """Store session-token digests so a database read cannot replay a cookie."""
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self, scenario_ids: Sequence[str] = ()) -> None:
        with self.connection() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, 1, SCHEMA_VERSION):
                raise UnsupportedSchemaVersion(
                    f"database schema version {version} is unsupported; expected 1 or "
                    f"{SCHEMA_VERSION}"
                )
            if version == 0:
                self._create_schema_v2(conn)
            elif version == 1:
                self._migrate_v1_to_v2(conn)
            now = utc_now()
            with conn:
                conn.executemany(
                    """INSERT INTO scenario_availability(scenario_id, enabled, updated_at)
                       VALUES (?, 1, ?) ON CONFLICT(scenario_id) DO NOTHING""",
                    [(scenario_id, now) for scenario_id in scenario_ids],
                )

    @staticmethod
    def _create_schema_v2(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
                    PRAGMA journal_mode = WAL;

                    CREATE TABLE application_metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE instructor_credentials (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        salt BLOB NOT NULL,
                        password_hash BLOB NOT NULL,
                        scrypt_n INTEGER NOT NULL,
                        scrypt_r INTEGER NOT NULL,
                        scrypt_p INTEGER NOT NULL,
                        password_version INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE instructor_sessions (
                        token_hash TEXT PRIMARY KEY,
                        password_version INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    );

                    CREATE TABLE authentication_throttle (
                        scope TEXT PRIMARY KEY,
                        failure_count INTEGER NOT NULL,
                        blocked_until TEXT,
                        last_failure_at TEXT
                    );

                    CREATE TABLE security_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        details TEXT NOT NULL DEFAULT '{}'
                    );

                    CREATE TABLE profiles (
                        id TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        learner_id TEXT,
                        organization TEXT,
                        course TEXT,
                        section TEXT,
                        instructor_name TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_activity_at TEXT NOT NULL
                    );

                    CREATE TABLE student_sessions (
                        token_hash TEXT PRIMARY KEY,
                        profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                        created_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL
                    );

                    CREATE TABLE instructor_policies (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        scored_mode_enabled INTEGER NOT NULL CHECK (scored_mode_enabled IN (0, 1)),
                        independent_mode_enabled INTEGER NOT NULL
                            CHECK (independent_mode_enabled IN (0, 1)),
                        guided_mode_enabled INTEGER NOT NULL CHECK (guided_mode_enabled IN (0, 1)),
                        hints_enabled INTEGER NOT NULL CHECK (hints_enabled IN (0, 1)),
                        answer_key_enabled INTEGER NOT NULL CHECK (answer_key_enabled IN (0, 1)),
                        walkthrough_enabled INTEGER NOT NULL CHECK (walkthrough_enabled IN (0, 1)),
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE scenario_availability (
                        scenario_id TEXT PRIMARY KEY,
                        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE scenario_attempts (
                        id TEXT PRIMARY KEY,
                        profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                        scenario_id TEXT NOT NULL,
                        attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
                        is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
                        status TEXT NOT NULL CHECK (status IN (
                            'not_started', 'in_progress', 'completed',
                            'completed_with_assistance', 'solution_revealed'
                        )),
                        mode TEXT CHECK (mode IN ('independent', 'guided')),
                        scored INTEGER NOT NULL DEFAULT 1 CHECK (scored IN (0, 1)),
                        started_at TEXT,
                        completed_at TEXT,
                        solution_locked INTEGER NOT NULL DEFAULT 0
                            CHECK (solution_locked IN (0, 1)),
                        prior_solution_exposure INTEGER NOT NULL DEFAULT 0
                            CHECK (prior_solution_exposure IN (0, 1)),
                        policy_snapshot TEXT NOT NULL DEFAULT '{}',
                        notes TEXT NOT NULL DEFAULT '',
                        closed_at TEXT,
                        reset_at TEXT,
                        reset_actor TEXT CHECK (reset_actor IN ('student', 'instructor')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE (profile_id, scenario_id, attempt_number)
                    );

                    CREATE TABLE flag_progress (
                        attempt_id TEXT NOT NULL REFERENCES scenario_attempts(id) ON DELETE CASCADE,
                        flag_id TEXT NOT NULL,
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        solved INTEGER NOT NULL DEFAULT 0 CHECK (solved IN (0, 1)),
                        points_earned INTEGER,
                        solved_at TEXT,
                        PRIMARY KEY (attempt_id, flag_id)
                    );

                    CREATE TABLE hint_reveals (
                        attempt_id TEXT NOT NULL REFERENCES scenario_attempts(id) ON DELETE CASCADE,
                        flag_id TEXT NOT NULL,
                        level INTEGER NOT NULL CHECK (level > 0),
                        point_cost INTEGER NOT NULL CHECK (point_cost >= 0),
                        revealed_at TEXT NOT NULL,
                        PRIMARY KEY (attempt_id, flag_id, level)
                    );

                    CREATE TABLE opened_documents (
                        attempt_id TEXT NOT NULL REFERENCES scenario_attempts(id) ON DELETE CASCADE,
                        document_key TEXT NOT NULL,
                        opened_at TEXT NOT NULL,
                        PRIMARY KEY (attempt_id, document_key)
                    );

                    CREATE TABLE training_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                        attempt_id TEXT REFERENCES scenario_attempts(id) ON DELETE CASCADE,
                        scenario_id TEXT,
                        event_type TEXT NOT NULL,
                        actor_type TEXT NOT NULL
                            CHECK (actor_type IN ('student', 'instructor', 'system')),
                        occurred_at TEXT NOT NULL,
                        details TEXT NOT NULL DEFAULT '{}'
                    );

                    CREATE TABLE training_event_acknowledgments (
                        event_id INTEGER PRIMARY KEY
                            REFERENCES training_events(id) ON DELETE CASCADE,
                        acknowledged_at TEXT NOT NULL,
                        acknowledged_by TEXT NOT NULL DEFAULT 'instructor'
                    );

                    CREATE TABLE scenario_executions (
                        job_id TEXT PRIMARY KEY,
                        attempt_id TEXT NOT NULL REFERENCES scenario_attempts(id) ON DELETE CASCADE,
                        execution_mode TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        return_code INTEGER
                    );

                    CREATE INDEX idx_attempts_profile ON scenario_attempts(profile_id, scenario_id);
                    CREATE UNIQUE INDEX idx_attempts_current
                        ON scenario_attempts(profile_id, scenario_id) WHERE is_current = 1;
                    CREATE INDEX idx_flag_progress_flag ON flag_progress(flag_id);
                    CREATE INDEX idx_hint_reveals_flag ON hint_reveals(flag_id, level);
                    CREATE INDEX idx_training_events_profile
                        ON training_events(profile_id, occurred_at);
                    CREATE INDEX idx_training_events_integrity
                        ON training_events(event_type, occurred_at);
                    CREATE INDEX idx_executions_attempt ON scenario_executions(attempt_id);
            """
        )
        now = utc_now()
        conn.execute(
            "INSERT INTO application_metadata(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        conn.execute(
            "INSERT INTO application_metadata(key, value) VALUES (?, ?)",
            ("database_id", uuid.uuid4().hex),
        )
        conn.execute(
            """INSERT INTO instructor_policies(
                   id, scored_mode_enabled, independent_mode_enabled,
                   guided_mode_enabled, hints_enabled, answer_key_enabled,
                   walkthrough_enabled, updated_at
               ) VALUES (1, 1, 1, 1, 1, 1, 1, ?)""",
            (now,),
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    @staticmethod
    def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
        """Preserve every version-1 record while introducing durable attempt IDs."""
        conn.executescript(
            """
            ALTER TABLE flag_progress RENAME TO flag_progress_v1;
            ALTER TABLE hint_reveals RENAME TO hint_reveals_v1;
            ALTER TABLE opened_documents RENAME TO opened_documents_v1;
            ALTER TABLE scenario_attempts RENAME TO scenario_attempts_v1;

            CREATE TABLE scenario_attempts (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                scenario_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
                is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
                status TEXT NOT NULL CHECK (status IN (
                    'not_started', 'in_progress', 'completed',
                    'completed_with_assistance', 'solution_revealed'
                )),
                mode TEXT CHECK (mode IN ('independent', 'guided')),
                scored INTEGER NOT NULL DEFAULT 1 CHECK (scored IN (0, 1)),
                started_at TEXT,
                completed_at TEXT,
                solution_locked INTEGER NOT NULL DEFAULT 0 CHECK (solution_locked IN (0, 1)),
                prior_solution_exposure INTEGER NOT NULL DEFAULT 0
                    CHECK (prior_solution_exposure IN (0, 1)),
                policy_snapshot TEXT NOT NULL DEFAULT '{}',
                notes TEXT NOT NULL DEFAULT '',
                closed_at TEXT,
                reset_at TEXT,
                reset_actor TEXT CHECK (reset_actor IN ('student', 'instructor')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (profile_id, scenario_id, attempt_number)
            );

            INSERT INTO scenario_attempts(
                id, profile_id, scenario_id, attempt_number, is_current, status, mode, scored,
                started_at, completed_at, solution_locked, prior_solution_exposure,
                policy_snapshot, notes, created_at, updated_at
            )
            SELECT profile_id || ':' || scenario_id || ':1', profile_id, scenario_id, 1, 1,
                   status, mode, scored, started_at, completed_at, solution_locked, 0,
                   '{}', notes, created_at, updated_at
            FROM scenario_attempts_v1;

            CREATE TABLE flag_progress (
                attempt_id TEXT NOT NULL REFERENCES scenario_attempts(id) ON DELETE CASCADE,
                flag_id TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                solved INTEGER NOT NULL DEFAULT 0 CHECK (solved IN (0, 1)),
                points_earned INTEGER,
                solved_at TEXT,
                PRIMARY KEY (attempt_id, flag_id)
            );
            INSERT INTO flag_progress
            SELECT profile_id || ':' || scenario_id || ':1', flag_id, attempt_count,
                   solved, points_earned, solved_at
            FROM flag_progress_v1;

            CREATE TABLE hint_reveals (
                attempt_id TEXT NOT NULL REFERENCES scenario_attempts(id) ON DELETE CASCADE,
                flag_id TEXT NOT NULL,
                level INTEGER NOT NULL CHECK (level > 0),
                point_cost INTEGER NOT NULL CHECK (point_cost >= 0),
                revealed_at TEXT NOT NULL,
                PRIMARY KEY (attempt_id, flag_id, level)
            );
            INSERT INTO hint_reveals
            SELECT profile_id || ':' || scenario_id || ':1', flag_id, level,
                   point_cost, revealed_at
            FROM hint_reveals_v1;

            CREATE TABLE opened_documents (
                attempt_id TEXT NOT NULL REFERENCES scenario_attempts(id) ON DELETE CASCADE,
                document_key TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                PRIMARY KEY (attempt_id, document_key)
            );
            INSERT INTO opened_documents
            SELECT profile_id || ':' || scenario_id || ':1', document_key, opened_at
            FROM opened_documents_v1;

            CREATE TABLE training_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                attempt_id TEXT REFERENCES scenario_attempts(id) ON DELETE CASCADE,
                scenario_id TEXT,
                event_type TEXT NOT NULL,
                actor_type TEXT NOT NULL CHECK (actor_type IN ('student', 'instructor', 'system')),
                occurred_at TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE training_event_acknowledgments (
                event_id INTEGER PRIMARY KEY REFERENCES training_events(id) ON DELETE CASCADE,
                acknowledged_at TEXT NOT NULL,
                acknowledged_by TEXT NOT NULL DEFAULT 'instructor'
            );
            CREATE TABLE scenario_executions (
                job_id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL REFERENCES scenario_attempts(id) ON DELETE CASCADE,
                execution_mode TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                return_code INTEGER
            );

            DROP TABLE flag_progress_v1;
            DROP TABLE hint_reveals_v1;
            DROP TABLE opened_documents_v1;
            DROP TABLE scenario_attempts_v1;

            CREATE INDEX idx_attempts_profile ON scenario_attempts(profile_id, scenario_id);
            CREATE UNIQUE INDEX idx_attempts_current
                ON scenario_attempts(profile_id, scenario_id) WHERE is_current = 1;
            CREATE INDEX idx_flag_progress_flag ON flag_progress(flag_id);
            CREATE INDEX idx_hint_reveals_flag ON hint_reveals(flag_id, level);
            CREATE INDEX idx_training_events_profile
                ON training_events(profile_id, occurred_at);
            CREATE INDEX idx_training_events_integrity
                ON training_events(event_type, occurred_at);
            CREATE INDEX idx_executions_attempt ON scenario_executions(attempt_id);
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO application_metadata(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        conn.execute(
            "INSERT OR IGNORE INTO application_metadata(key, value) VALUES (?, ?)",
            ("database_id", uuid.uuid4().hex),
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    def schema_version(self) -> int:
        with self.connection() as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    # -- local learner profiles -------------------------------------------------

    def list_profiles(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT id, display_name, learner_id, organization, course,
                          section, instructor_name, created_at, updated_at,
                          last_activity_at
                   FROM profiles ORDER BY display_name COLLATE NOCASE, created_at"""
            ).fetchall()
        return [dict(row) for row in rows]

    def create_profile(self, profile_id: str, fields: Mapping[str, str | None]) -> dict[str, Any]:
        now = utc_now()
        values = self._profile_values(fields)
        with self.connection() as conn, conn:
            conn.execute(
                """INSERT INTO profiles(
                       id, display_name, learner_id, organization, course,
                       section, instructor_name, created_at, updated_at, last_activity_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (profile_id, *values, now, now, now),
            )
        profile = self.get_profile(profile_id)
        if profile is None:  # pragma: no cover - insert/read invariant
            raise RuntimeError("profile insert did not persist")
        return profile

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            return _row_dict(
                conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
            )

    def update_profile(
        self, profile_id: str, fields: Mapping[str, str | None]
    ) -> dict[str, Any] | None:
        now = utc_now()
        values = self._profile_values(fields)
        with self.connection() as conn, conn:
            result = conn.execute(
                """UPDATE profiles SET display_name = ?, learner_id = ?,
                          organization = ?, course = ?, section = ?,
                          instructor_name = ?, updated_at = ?, last_activity_at = ?
                   WHERE id = ?""",
                (*values, now, now, profile_id),
            )
        return self.get_profile(profile_id) if result.rowcount else None

    def delete_profile(self, profile_id: str) -> bool:
        with self.connection() as conn, conn:
            result = conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        return bool(result.rowcount)

    @staticmethod
    def _profile_values(fields: Mapping[str, str | None]) -> tuple[str | None, ...]:
        return (
            fields.get("display_name"),
            fields.get("learner_id"),
            fields.get("organization"),
            fields.get("course"),
            fields.get("section"),
            fields.get("instructor_name"),
        )

    def touch_profile(self, profile_id: str, now: str | None = None) -> None:
        with self.connection() as conn, conn:
            conn.execute(
                "UPDATE profiles SET last_activity_at = ? WHERE id = ?",
                (now or utc_now(), profile_id),
            )

    # -- opaque student-session records ----------------------------------------

    def create_student_session(self, token: str, profile_id: str) -> None:
        now = utc_now()
        with self.connection() as conn, conn:
            conn.execute(
                """INSERT INTO student_sessions(token_hash, profile_id, created_at, last_seen_at)
                   VALUES (?, ?, ?, ?)""",
                (token_digest(token), profile_id, now, now),
            )

    def get_student_session(self, token: str) -> dict[str, Any] | None:
        with self.connection() as conn, conn:
            row = conn.execute(
                "SELECT * FROM student_sessions WHERE token_hash = ?",
                (token_digest(token),),
            ).fetchone()
            if row:
                now = utc_now()
                conn.execute(
                    "UPDATE student_sessions SET last_seen_at = ? WHERE token_hash = ?",
                    (now, token_digest(token)),
                )
                conn.execute(
                    "UPDATE profiles SET last_activity_at = ? WHERE id = ?",
                    (now, row["profile_id"]),
                )
        return _row_dict(row)

    def delete_student_session(self, token: str) -> None:
        with self.connection() as conn, conn:
            conn.execute(
                "DELETE FROM student_sessions WHERE token_hash = ?", (token_digest(token),)
            )

    # -- policies ---------------------------------------------------------------

    def get_policies(self) -> dict[str, Any]:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM instructor_policies WHERE id = 1").fetchone()
            scenarios = conn.execute(
                "SELECT scenario_id, enabled FROM scenario_availability ORDER BY scenario_id"
            ).fetchall()
        if row is None:  # pragma: no cover - schema invariant
            raise RuntimeError("policy row missing")
        result = {key: bool(row[key]) for key in DEFAULT_POLICIES}
        result["updated_at"] = row["updated_at"]
        result["scenario_availability"] = {
            item["scenario_id"]: bool(item["enabled"]) for item in scenarios
        }
        return result

    def update_policies(
        self,
        policies: Mapping[str, bool],
        scenario_availability: Mapping[str, bool] | None = None,
    ) -> dict[str, Any]:
        current = self.get_policies()
        merged = {key: bool(policies.get(key, current[key])) for key in DEFAULT_POLICIES}
        if not merged["independent_mode_enabled"] and not merged["guided_mode_enabled"]:
            raise StorageConflict("at least one training mode must remain enabled")
        now = utc_now()
        with self.connection() as conn, conn:
            conn.execute(
                """UPDATE instructor_policies SET scored_mode_enabled = ?,
                          independent_mode_enabled = ?, guided_mode_enabled = ?,
                          hints_enabled = ?, answer_key_enabled = ?,
                          walkthrough_enabled = ?, updated_at = ? WHERE id = 1""",
                (*(int(merged[key]) for key in DEFAULT_POLICIES), now),
            )
            if scenario_availability is not None:
                conn.executemany(
                    """INSERT INTO scenario_availability(scenario_id, enabled, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(scenario_id) DO UPDATE SET
                           enabled = excluded.enabled, updated_at = excluded.updated_at""",
                    [
                        (scenario_id, int(enabled), now)
                        for scenario_id, enabled in scenario_availability.items()
                    ],
                )
        return self.get_policies()

    # -- instructor credentials and sessions ----------------------------------

    def get_instructor_credentials(self) -> dict[str, Any] | None:
        with self.connection() as conn:
            return _row_dict(
                conn.execute("SELECT * FROM instructor_credentials WHERE id = 1").fetchone()
            )

    def set_instructor_credentials(
        self,
        *,
        salt: bytes,
        password_hash: bytes,
        scrypt_n: int,
        scrypt_r: int,
        scrypt_p: int,
        only_if_unconfigured: bool = False,
    ) -> int:
        now = utc_now()
        with self.connection() as conn, conn:
            if only_if_unconfigured:
                try:
                    conn.execute(
                        """INSERT INTO instructor_credentials(
                               id, salt, password_hash, scrypt_n, scrypt_r, scrypt_p,
                               password_version, created_at, updated_at
                           ) VALUES (1, ?, ?, ?, ?, ?, 1, ?, ?)""",
                        (salt, password_hash, scrypt_n, scrypt_r, scrypt_p, now, now),
                    )
                except sqlite3.IntegrityError as exc:
                    raise StorageConflict("instructor credentials are already configured") from exc
                conn.execute("DELETE FROM instructor_sessions")
                return 1
            existing = conn.execute(
                "SELECT password_version FROM instructor_credentials WHERE id = 1"
            ).fetchone()
            version = int(existing[0]) + 1 if existing else 1
            conn.execute(
                """INSERT INTO instructor_credentials(
                       id, salt, password_hash, scrypt_n, scrypt_r, scrypt_p,
                       password_version, created_at, updated_at
                   ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       salt = excluded.salt,
                       password_hash = excluded.password_hash,
                       scrypt_n = excluded.scrypt_n,
                       scrypt_r = excluded.scrypt_r,
                       scrypt_p = excluded.scrypt_p,
                       password_version = excluded.password_version,
                       updated_at = excluded.updated_at""",
                (salt, password_hash, scrypt_n, scrypt_r, scrypt_p, version, now, now),
            )
            conn.execute("DELETE FROM instructor_sessions")
        return version

    def create_instructor_session(
        self,
        token: str,
        password_version: int,
        *,
        created_at: str,
        expires_at: str,
    ) -> None:
        with self.connection() as conn, conn:
            conn.execute(
                """INSERT INTO instructor_sessions(
                       token_hash, password_version, created_at, last_seen_at, expires_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (token_digest(token), password_version, created_at, created_at, expires_at),
            )

    def get_instructor_session(self, token: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            return _row_dict(
                conn.execute(
                    "SELECT * FROM instructor_sessions WHERE token_hash = ?",
                    (token_digest(token),),
                ).fetchone()
            )

    def touch_instructor_session(self, token: str, last_seen_at: str) -> None:
        with self.connection() as conn, conn:
            conn.execute(
                "UPDATE instructor_sessions SET last_seen_at = ? WHERE token_hash = ?",
                (last_seen_at, token_digest(token)),
            )

    def delete_instructor_session(self, token: str) -> None:
        with self.connection() as conn, conn:
            conn.execute(
                "DELETE FROM instructor_sessions WHERE token_hash = ?", (token_digest(token),)
            )

    def delete_all_instructor_sessions(self) -> int:
        with self.connection() as conn, conn:
            result = conn.execute("DELETE FROM instructor_sessions")
        return result.rowcount

    # -- persisted authentication throttling and safe audit events -------------

    def get_throttle(self, scope: str = "instructor") -> dict[str, Any]:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM authentication_throttle WHERE scope = ?", (scope,)
            ).fetchone()
        return (
            dict(row)
            if row
            else {
                "scope": scope,
                "failure_count": 0,
                "blocked_until": None,
                "last_failure_at": None,
            }
        )

    def set_throttle(
        self,
        failure_count: int,
        blocked_until: str | None,
        last_failure_at: str | None,
        scope: str = "instructor",
    ) -> None:
        with self.connection() as conn, conn:
            conn.execute(
                """INSERT INTO authentication_throttle(
                       scope, failure_count, blocked_until, last_failure_at
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(scope) DO UPDATE SET
                       failure_count = excluded.failure_count,
                       blocked_until = excluded.blocked_until,
                       last_failure_at = excluded.last_failure_at""",
                (scope, failure_count, blocked_until, last_failure_at),
            )

    def record_security_event(
        self, event_type: str, details: Mapping[str, Any] | None = None
    ) -> None:
        with self.connection() as conn, conn:
            conn.execute(
                "INSERT INTO security_events(event_type, occurred_at, details) VALUES (?, ?, ?)",
                (event_type, utc_now(), json.dumps(details or {}, sort_keys=True)),
            )

    def list_security_events(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT id, event_type, occurred_at, details FROM security_events ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    # -- profile-scoped attempt persistence and immutable training history -----

    @staticmethod
    def _insert_training_event(
        conn: sqlite3.Connection,
        *,
        profile_id: str,
        event_type: str,
        actor_type: str,
        occurred_at: str,
        attempt_id: str | None = None,
        scenario_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> int:
        result = conn.execute(
            """INSERT INTO training_events(
                   profile_id, attempt_id, scenario_id, event_type,
                   actor_type, occurred_at, details
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_id,
                attempt_id,
                scenario_id,
                event_type,
                actor_type,
                occurred_at,
                json.dumps(details or {}, sort_keys=True),
            ),
        )
        return int(result.lastrowid)

    @staticmethod
    def _hydrate_attempt(conn: sqlite3.Connection, attempt: sqlite3.Row) -> dict[str, Any]:
        attempt_id = attempt["id"]
        flags = conn.execute(
            """SELECT flag_id, attempt_count, solved, points_earned, solved_at
               FROM flag_progress WHERE attempt_id = ? ORDER BY flag_id""",
            (attempt_id,),
        ).fetchall()
        hints = conn.execute(
            """SELECT flag_id, level, point_cost, revealed_at
               FROM hint_reveals WHERE attempt_id = ? ORDER BY flag_id, level""",
            (attempt_id,),
        ).fetchall()
        documents = conn.execute(
            """SELECT document_key, opened_at FROM opened_documents
               WHERE attempt_id = ? ORDER BY opened_at, document_key""",
            (attempt_id,),
        ).fetchall()
        executions = conn.execute(
            """SELECT job_id, execution_mode, started_at, finished_at, return_code
               FROM scenario_executions WHERE attempt_id = ? ORDER BY started_at, job_id""",
            (attempt_id,),
        ).fetchall()
        result = dict(attempt)
        try:
            result["policy_snapshot"] = json.loads(result["policy_snapshot"] or "{}")
        except (TypeError, json.JSONDecodeError):
            result["policy_snapshot"] = {}
        result["flags"] = [dict(row) for row in flags]
        result["hints"] = [dict(row) for row in hints]
        result["documents"] = [dict(row) for row in documents]
        result["executions"] = [dict(row) for row in executions]
        return result

    def get_attempt(self, profile_id: str, scenario_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            attempt = conn.execute(
                """SELECT * FROM scenario_attempts
                   WHERE profile_id = ? AND scenario_id = ? AND is_current = 1""",
                (profile_id, scenario_id),
            ).fetchone()
            return self._hydrate_attempt(conn, attempt) if attempt else None

    def get_attempt_by_id(self, attempt_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            attempt = conn.execute(
                "SELECT * FROM scenario_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            return self._hydrate_attempt(conn, attempt) if attempt else None

    def list_attempts(self, profile_id: str) -> list[dict[str, Any]]:
        """Return only each scenario's current attempt for the live student state."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM scenario_attempts
                   WHERE profile_id = ? AND is_current = 1 ORDER BY scenario_id""",
                (profile_id,),
            ).fetchall()
            return [self._hydrate_attempt(conn, row) for row in rows]

    def list_attempt_history(
        self, profile_id: str, scenario_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self.connection() as conn:
            if scenario_id is None:
                rows = conn.execute(
                    """SELECT * FROM scenario_attempts WHERE profile_id = ?
                       ORDER BY scenario_id, attempt_number""",
                    (profile_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM scenario_attempts
                       WHERE profile_id = ? AND scenario_id = ? ORDER BY attempt_number""",
                    (profile_id, scenario_id),
                ).fetchall()
            return [self._hydrate_attempt(conn, row) for row in rows]

    @staticmethod
    def _new_attempt_id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def _next_attempt_number(conn: sqlite3.Connection, profile_id: str, scenario_id: str) -> int:
        row = conn.execute(
            """SELECT COALESCE(MAX(attempt_number), 0) + 1
               FROM scenario_attempts WHERE profile_id = ? AND scenario_id = ?""",
            (profile_id, scenario_id),
        ).fetchone()
        return int(row[0])

    def ensure_attempt(
        self,
        profile_id: str,
        scenario_id: str,
        *,
        mode: str,
        scored: bool,
        start: bool = False,
        policy_snapshot: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as conn, conn:
            row = conn.execute(
                """SELECT * FROM scenario_attempts
                   WHERE profile_id = ? AND scenario_id = ? AND is_current = 1""",
                (profile_id, scenario_id),
            ).fetchone()
            if row is None:
                attempt_id = self._new_attempt_id()
                number = self._next_attempt_number(conn, profile_id, scenario_id)
                status = "in_progress" if start else "not_started"
                started_at = now if start else None
                conn.execute(
                    """INSERT INTO scenario_attempts(
                           id, profile_id, scenario_id, attempt_number, is_current,
                           status, mode, scored, started_at, completed_at,
                           solution_locked, prior_solution_exposure, policy_snapshot,
                           notes, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, NULL, 0, 0, ?, '', ?, ?)""",
                    (
                        attempt_id,
                        profile_id,
                        scenario_id,
                        number,
                        status,
                        mode,
                        int(scored),
                        started_at,
                        json.dumps(policy_snapshot or {}, sort_keys=True),
                        now,
                        now,
                    ),
                )
                if start:
                    self._insert_training_event(
                        conn,
                        profile_id=profile_id,
                        attempt_id=attempt_id,
                        scenario_id=scenario_id,
                        event_type="attempt_started",
                        actor_type="student",
                        occurred_at=now,
                        details={"attempt_number": number, "mode": mode},
                    )
            else:
                attempt_id = row["id"]
                if row["status"] != "not_started" and row["mode"] != mode:
                    raise StorageConflict("training mode is locked once an attempt starts")
                if start and row["status"] == "not_started":
                    conn.execute(
                        """UPDATE scenario_attempts SET status = 'in_progress', mode = ?,
                                  scored = ?, started_at = ?, policy_snapshot = ?, updated_at = ?
                           WHERE id = ?""",
                        (
                            mode,
                            int(scored),
                            now,
                            json.dumps(policy_snapshot or {}, sort_keys=True),
                            now,
                            attempt_id,
                        ),
                    )
                    self._insert_training_event(
                        conn,
                        profile_id=profile_id,
                        attempt_id=attempt_id,
                        scenario_id=scenario_id,
                        event_type="attempt_started",
                        actor_type="student",
                        occurred_at=now,
                        details={"attempt_number": row["attempt_number"], "mode": mode},
                    )
                elif row["status"] == "not_started":
                    conn.execute(
                        """UPDATE scenario_attempts SET mode = ?, scored = ?,
                                  policy_snapshot = ?, updated_at = ? WHERE id = ?""",
                        (
                            mode,
                            int(scored),
                            json.dumps(policy_snapshot or {}, sort_keys=True),
                            now,
                            attempt_id,
                        ),
                    )
            conn.execute("UPDATE profiles SET last_activity_at = ? WHERE id = ?", (now, profile_id))
        result = self.get_attempt(profile_id, scenario_id)
        if result is None:  # pragma: no cover - transaction invariant
            raise RuntimeError("attempt insert did not persist")
        return result

    def set_notes(self, profile_id: str, scenario_id: str, notes: str) -> bool:
        now = utc_now()
        with self.connection() as conn, conn:
            result = conn.execute(
                """UPDATE scenario_attempts SET notes = ?, updated_at = ?
                   WHERE profile_id = ? AND scenario_id = ? AND is_current = 1""",
                (notes, now, profile_id, scenario_id),
            )
            if result.rowcount:
                conn.execute(
                    "UPDATE profiles SET last_activity_at = ? WHERE id = ?", (now, profile_id)
                )
        return bool(result.rowcount)

    def reveal_hint(
        self,
        profile_id: str,
        scenario_id: str,
        flag_id: str,
        level: int,
        point_cost: int,
    ) -> bool:
        """Record one hint level and its event. Returns True only for a new reveal."""
        now = utc_now()
        with self.connection() as conn, conn:
            attempt = conn.execute(
                """SELECT id, solution_locked FROM scenario_attempts
                   WHERE profile_id = ? AND scenario_id = ? AND is_current = 1""",
                (profile_id, scenario_id),
            ).fetchone()
            if not attempt:
                raise StorageConflict("attempt does not exist")
            if attempt["solution_locked"]:
                raise StorageConflict("attempt is locked")
            attempt_id = attempt["id"]
            if level > 1:
                previous = conn.execute(
                    """SELECT 1 FROM hint_reveals
                       WHERE attempt_id = ? AND flag_id = ? AND level = ?""",
                    (attempt_id, flag_id, level - 1),
                ).fetchone()
                if not previous:
                    raise StorageConflict("hint levels must be revealed sequentially")
            result = conn.execute(
                """INSERT OR IGNORE INTO hint_reveals(
                       attempt_id, flag_id, level, point_cost, revealed_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (attempt_id, flag_id, level, point_cost, now),
            )
            if result.rowcount:
                self._insert_training_event(
                    conn,
                    profile_id=profile_id,
                    attempt_id=attempt_id,
                    scenario_id=scenario_id,
                    event_type="hint_revealed",
                    actor_type="student",
                    occurred_at=now,
                    details={"flag_id": flag_id, "level": level, "point_cost": point_cost},
                )
            conn.execute("UPDATE profiles SET last_activity_at = ? WHERE id = ?", (now, profile_id))
        return bool(result.rowcount)

    def record_flag_submission(
        self,
        profile_id: str,
        scenario_id: str,
        flag_id: str,
        *,
        correct: bool,
        points_earned: int | None,
        all_flag_ids: Sequence[str],
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as conn, conn:
            attempt = conn.execute(
                """SELECT id, status, mode, solution_locked, prior_solution_exposure
                   FROM scenario_attempts
                   WHERE profile_id = ? AND scenario_id = ? AND is_current = 1""",
                (profile_id, scenario_id),
            ).fetchone()
            if not attempt:
                raise StorageConflict("attempt does not exist")
            if attempt["solution_locked"]:
                raise StorageConflict("attempt is locked")
            attempt_id = attempt["id"]
            previous = conn.execute(
                "SELECT solved FROM flag_progress WHERE attempt_id = ? AND flag_id = ?",
                (attempt_id, flag_id),
            ).fetchone()
            conn.execute(
                """INSERT INTO flag_progress(
                       attempt_id, flag_id, attempt_count, solved, points_earned, solved_at
                   ) VALUES (?, ?, 1, ?, ?, ?)
                   ON CONFLICT(attempt_id, flag_id) DO UPDATE SET
                       attempt_count = flag_progress.attempt_count + 1,
                       solved = CASE WHEN flag_progress.solved = 1 THEN 1 ELSE excluded.solved END,
                       points_earned = CASE
                           WHEN flag_progress.solved = 1 THEN flag_progress.points_earned
                           ELSE excluded.points_earned END,
                       solved_at = CASE
                           WHEN flag_progress.solved = 1 THEN flag_progress.solved_at
                           ELSE excluded.solved_at END""",
                (
                    attempt_id,
                    flag_id,
                    int(correct),
                    points_earned if correct else None,
                    now if correct else None,
                ),
            )
            self._insert_training_event(
                conn,
                profile_id=profile_id,
                attempt_id=attempt_id,
                scenario_id=scenario_id,
                event_type="flag_submitted",
                actor_type="student",
                occurred_at=now,
                details={"flag_id": flag_id, "correct": bool(correct)},
            )
            if correct and not (previous and previous["solved"]):
                self._insert_training_event(
                    conn,
                    profile_id=profile_id,
                    attempt_id=attempt_id,
                    scenario_id=scenario_id,
                    event_type="flag_solved",
                    actor_type="student",
                    occurred_at=now,
                    details={"flag_id": flag_id, "points_earned": points_earned},
                )
            solved_count = conn.execute(
                "SELECT COUNT(*) FROM flag_progress WHERE attempt_id = ? AND solved = 1",
                (attempt_id,),
            ).fetchone()[0]
            status = "in_progress"
            completed_at = None
            newly_completed = False
            if all_flag_ids and solved_count == len(set(all_flag_ids)):
                assistance = conn.execute(
                    """SELECT
                           (SELECT COUNT(*) FROM hint_reveals WHERE attempt_id = ?) +
                           (SELECT COUNT(*) FROM opened_documents
                            WHERE attempt_id = ? AND document_key != 'briefing')""",
                    (attempt_id, attempt_id),
                ).fetchone()[0]
                status = (
                    "completed_with_assistance"
                    if assistance or attempt["prior_solution_exposure"]
                    else "completed"
                )
                completed_at = now
                newly_completed = attempt["status"] not in (
                    "completed",
                    "completed_with_assistance",
                )
            conn.execute(
                """UPDATE scenario_attempts SET status = ?,
                          completed_at = COALESCE(completed_at, ?), updated_at = ? WHERE id = ?""",
                (status, completed_at, now, attempt_id),
            )
            if newly_completed:
                self._insert_training_event(
                    conn,
                    profile_id=profile_id,
                    attempt_id=attempt_id,
                    scenario_id=scenario_id,
                    event_type="attempt_completed",
                    actor_type="system",
                    occurred_at=now,
                    details={"status": status},
                )
            conn.execute("UPDATE profiles SET last_activity_at = ? WHERE id = ?", (now, profile_id))
        result = self.get_attempt(profile_id, scenario_id)
        if result is None:  # pragma: no cover
            raise RuntimeError("attempt disappeared during submission")
        return result

    def record_document_open(
        self,
        profile_id: str,
        scenario_id: str,
        document_key: str,
        *,
        solution: bool,
        lock_solution: bool,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as conn, conn:
            attempt = conn.execute(
                """SELECT id, status FROM scenario_attempts
                   WHERE profile_id = ? AND scenario_id = ? AND is_current = 1""",
                (profile_id, scenario_id),
            ).fetchone()
            if not attempt:
                raise StorageConflict("attempt does not exist")
            attempt_id = attempt["id"]
            result = conn.execute(
                """INSERT OR IGNORE INTO opened_documents(
                       attempt_id, document_key, opened_at
                   ) VALUES (?, ?, ?)""",
                (attempt_id, document_key, now),
            )
            if result.rowcount:
                if document_key == "briefing":
                    event_type = "briefing_opened"
                elif document_key == "answer-key":
                    event_type = "answer_key_opened"
                else:
                    event_type = "solution_document_opened"
                self._insert_training_event(
                    conn,
                    profile_id=profile_id,
                    attempt_id=attempt_id,
                    scenario_id=scenario_id,
                    event_type=event_type,
                    actor_type="student",
                    occurred_at=now,
                    details={
                        "document_key": document_key,
                        "before_completion": attempt["status"]
                        not in ("completed", "completed_with_assistance"),
                    },
                )
            if solution and lock_solution:
                conn.execute(
                    """UPDATE scenario_attempts SET status = 'solution_revealed',
                              solution_locked = 1,
                              completed_at = COALESCE(completed_at, ?), updated_at = ?
                       WHERE id = ?""",
                    (now, now, attempt_id),
                )
            else:
                conn.execute(
                    "UPDATE scenario_attempts SET updated_at = ? WHERE id = ?",
                    (now, attempt_id),
                )
            conn.execute("UPDATE profiles SET last_activity_at = ? WHERE id = ?", (now, profile_id))
        refreshed = self.get_attempt(profile_id, scenario_id)
        if refreshed is None:  # pragma: no cover
            raise RuntimeError("attempt disappeared during document open")
        return refreshed

    def _reset_attempt_in_connection(
        self,
        conn: sqlite3.Connection,
        attempt: sqlite3.Row,
        *,
        actor_type: str,
        now: str,
    ) -> str:
        profile_id = attempt["profile_id"]
        scenario_id = attempt["scenario_id"]
        attempt_id = attempt["id"]
        score = conn.execute(
            "SELECT COALESCE(SUM(points_earned), 0) FROM flag_progress WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()[0]
        solution_exposure = bool(attempt["solution_locked"]) or bool(
            conn.execute(
                """SELECT 1 FROM opened_documents
                   WHERE attempt_id = ? AND document_key IN ('answer-key', 'detection',
                                                             'expected-impact', 'walkthrough')
                   LIMIT 1""",
                (attempt_id,),
            ).fetchone()
        )
        details = {
            "prior_attempt_number": attempt["attempt_number"],
            "prior_status": attempt["status"],
            "prior_score": score,
            "prior_solution_exposure": solution_exposure,
        }
        self._insert_training_event(
            conn,
            profile_id=profile_id,
            attempt_id=attempt_id,
            scenario_id=scenario_id,
            event_type="reset_requested",
            actor_type=actor_type,
            occurred_at=now,
            details=details,
        )
        conn.execute(
            """UPDATE scenario_attempts SET is_current = 0, closed_at = ?, reset_at = ?,
                      reset_actor = ?, updated_at = ? WHERE id = ?""",
            (now, now, actor_type, now, attempt_id),
        )
        new_id = self._new_attempt_id()
        next_number = int(attempt["attempt_number"]) + 1
        conn.execute(
            """INSERT INTO scenario_attempts(
                   id, profile_id, scenario_id, attempt_number, is_current, status,
                   mode, scored, solution_locked, prior_solution_exposure,
                   policy_snapshot, notes, created_at, updated_at
               ) VALUES (?, ?, ?, ?, 1, 'not_started', NULL, ?, 0, ?, '{}', '', ?, ?)""",
            (
                new_id,
                profile_id,
                scenario_id,
                next_number,
                attempt["scored"],
                int(solution_exposure or attempt["prior_solution_exposure"]),
                now,
                now,
            ),
        )
        self._insert_training_event(
            conn,
            profile_id=profile_id,
            attempt_id=attempt_id,
            scenario_id=scenario_id,
            event_type="attempt_reset",
            actor_type=actor_type,
            occurred_at=now,
            details={**details, "new_attempt_id": new_id, "new_attempt_number": next_number},
        )
        return new_id

    def reset_attempt(
        self, profile_id: str, scenario_id: str, *, actor_type: str = "student"
    ) -> bool:
        now = utc_now()
        with self.connection() as conn, conn:
            attempt = conn.execute(
                """SELECT * FROM scenario_attempts
                   WHERE profile_id = ? AND scenario_id = ? AND is_current = 1""",
                (profile_id, scenario_id),
            ).fetchone()
            if attempt is None:
                return False
            self._reset_attempt_in_connection(conn, attempt, actor_type=actor_type, now=now)
            conn.execute("UPDATE profiles SET last_activity_at = ? WHERE id = ?", (now, profile_id))
        return True

    def reset_profile_progress(self, profile_id: str, *, actor_type: str = "student") -> int:
        now = utc_now()
        with self.connection() as conn, conn:
            attempts = conn.execute(
                """SELECT * FROM scenario_attempts
                   WHERE profile_id = ? AND is_current = 1 ORDER BY scenario_id""",
                (profile_id,),
            ).fetchall()
            for attempt in attempts:
                self._reset_attempt_in_connection(conn, attempt, actor_type=actor_type, now=now)
            self._insert_training_event(
                conn,
                profile_id=profile_id,
                event_type="profile_progress_reset",
                actor_type=actor_type,
                occurred_at=now,
                details={"scenario_count": len(attempts)},
            )
            conn.execute("UPDATE profiles SET last_activity_at = ? WHERE id = ?", (now, profile_id))
        return len(attempts)

    def record_training_event(
        self,
        profile_id: str,
        event_type: str,
        *,
        actor_type: str = "student",
        attempt_id: str | None = None,
        scenario_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> int:
        with self.connection() as conn, conn:
            return self._insert_training_event(
                conn,
                profile_id=profile_id,
                attempt_id=attempt_id,
                scenario_id=scenario_id,
                event_type=event_type,
                actor_type=actor_type,
                occurred_at=utc_now(),
                details=details,
            )

    def list_training_events(
        self,
        *,
        profile_id: str | None = None,
        integrity_only: bool = False,
        unread_only: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if profile_id is not None:
            clauses.append("e.profile_id = ?")
            params.append(profile_id)
        if integrity_only:
            placeholders = ",".join("?" for _ in INTEGRITY_EVENT_TYPES)
            clauses.append(f"e.event_type IN ({placeholders})")
            params.extend(sorted(INTEGRITY_EVENT_TYPES))
        if unread_only:
            clauses.append("a.event_id IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 1000)))
        # `where`'s clauses are fixed literal fragments ("e.profile_id =
        # ?", a static IN(...) of a fixed-size placeholder list,
        # "a.event_id IS NULL") built above — every actual value flows
        # through `params` as a `?` placeholder, never string-interpolated.
        query = (
            "SELECT e.id, e.profile_id, p.display_name, e.attempt_id, "  # noqa: S608 # nosec B608
            "e.scenario_id, e.event_type, e.actor_type, e.occurred_at, "
            "e.details, a.acknowledged_at "
            "FROM training_events e "
            "JOIN profiles p ON p.id = e.profile_id "
            "LEFT JOIN training_event_acknowledgments a ON a.event_id = e.id "
            f"{where} "
            "ORDER BY e.id DESC LIMIT ?"
        )
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item["details"] or "{}")
            except json.JSONDecodeError:
                item["details"] = {}
            item["acknowledged"] = item.pop("acknowledged_at") is not None
            events.append(item)
        return events

    def acknowledge_training_event(self, event_id: int) -> bool:
        now = utc_now()
        with self.connection() as conn, conn:
            exists = conn.execute(
                "SELECT 1 FROM training_events WHERE id = ?", (event_id,)
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                """INSERT OR IGNORE INTO training_event_acknowledgments(
                       event_id, acknowledged_at, acknowledged_by
                   ) VALUES (?, ?, 'instructor')""",
                (event_id, now),
            )
        return True

    def database_id(self) -> str:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT value FROM application_metadata WHERE key = 'database_id'"
            ).fetchone()
            return str(row[0]) if row else "unknown"

    def record_execution_started(
        self,
        profile_id: str,
        scenario_id: str,
        job_id: str,
        execution_mode: str,
    ) -> str:
        now = utc_now()
        with self.connection() as conn, conn:
            attempt = conn.execute(
                """SELECT id FROM scenario_attempts
                   WHERE profile_id = ? AND scenario_id = ? AND is_current = 1""",
                (profile_id, scenario_id),
            ).fetchone()
            if not attempt:
                raise StorageConflict("attempt does not exist")
            attempt_id = str(attempt["id"])
            conn.execute(
                """INSERT INTO scenario_executions(
                       job_id, attempt_id, execution_mode, started_at
                   ) VALUES (?, ?, ?, ?)""",
                (job_id, attempt_id, execution_mode, now),
            )
            self._insert_training_event(
                conn,
                profile_id=profile_id,
                attempt_id=attempt_id,
                scenario_id=scenario_id,
                event_type="scenario_execution_started",
                actor_type="student",
                occurred_at=now,
                details={"job_id": job_id, "execution_mode": execution_mode},
            )
        return attempt_id

    def record_execution_finished(self, job_id: str, return_code: int) -> bool:
        now = utc_now()
        with self.connection() as conn, conn:
            execution = conn.execute(
                """SELECT x.attempt_id, x.finished_at, a.profile_id, a.scenario_id
                   FROM scenario_executions x
                   JOIN scenario_attempts a ON a.id = x.attempt_id
                   WHERE x.job_id = ?""",
                (job_id,),
            ).fetchone()
            if not execution:
                return False
            if execution["finished_at"] is not None:
                return False
            conn.execute(
                """UPDATE scenario_executions SET finished_at = ?, return_code = ?
                   WHERE job_id = ?""",
                (now, int(return_code), job_id),
            )
            self._insert_training_event(
                conn,
                profile_id=execution["profile_id"],
                attempt_id=execution["attempt_id"],
                scenario_id=execution["scenario_id"],
                event_type="scenario_execution_finished",
                actor_type="system",
                occurred_at=now,
                details={"job_id": job_id, "return_code": int(return_code)},
            )
        return True

    # -- instructor analytics --------------------------------------------------

    def analytics(self) -> dict[str, Any]:
        with self.connection() as conn:
            profile_count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
            attempt_rows = conn.execute(
                """SELECT status, mode FROM scenario_attempts
                   WHERE status != 'not_started' OR is_current = 0"""
            ).fetchall()
            average = conn.execute(
                "SELECT AVG(points_earned) FROM flag_progress WHERE solved = 1"
            ).fetchone()[0]
            hints = conn.execute(
                """SELECT a.scenario_id, h.flag_id, COUNT(*) AS uses
                   FROM hint_reveals h JOIN scenario_attempts a ON a.id = h.attempt_id
                   GROUP BY a.scenario_id, h.flag_id
                   ORDER BY uses DESC, a.scenario_id, h.flag_id LIMIT 10"""
            ).fetchall()
            missed = conn.execute(
                """SELECT a.scenario_id, f.flag_id,
                          SUM(f.attempt_count - CASE WHEN f.solved = 1 THEN 1 ELSE 0 END) AS misses
                   FROM flag_progress f JOIN scenario_attempts a ON a.id = f.attempt_id
                   GROUP BY a.scenario_id, f.flag_id
                   HAVING misses > 0
                   ORDER BY misses DESC, a.scenario_id, f.flag_id LIMIT 10"""
            ).fetchall()
            unread_integrity = conn.execute(
                """SELECT COUNT(*) FROM training_events e
                   LEFT JOIN training_event_acknowledgments a ON a.event_id = e.id
                   WHERE e.event_type IN ('answer_key_opened', 'solution_document_opened',
                                          'attempt_reset', 'profile_progress_reset',
                                          'instructor_override')
                     AND a.event_id IS NULL"""
            ).fetchone()[0]
        return {
            "profiles": profile_count,
            "attempts": len(attempt_rows),
            "independent_attempts": sum(row["mode"] == "independent" for row in attempt_rows),
            "guided_attempts": sum(row["mode"] == "guided" for row in attempt_rows),
            "completed_attempts": sum(
                row["status"] in ("completed", "completed_with_assistance") for row in attempt_rows
            ),
            "solution_revealed_attempts": sum(
                row["status"] == "solution_revealed" for row in attempt_rows
            ),
            "average_solved_flag_points": round(float(average), 2) if average is not None else None,
            "most_used_hints": [dict(row) for row in hints],
            "frequently_missed_flags": [dict(row) for row in missed],
            "unread_integrity_events": unread_integrity,
        }
