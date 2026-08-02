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
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_POLICIES = {
    "scored_mode_enabled": True,
    "independent_mode_enabled": True,
    "guided_mode_enabled": True,
    "hints_enabled": True,
    "answer_key_enabled": True,
    "walkthrough_enabled": True,
}


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
            if version not in (0, SCHEMA_VERSION):
                raise UnsupportedSchemaVersion(
                    f"database schema version {version} is unsupported; expected {SCHEMA_VERSION}"
                )
            if version == 0:
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
                        profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                        scenario_id TEXT NOT NULL,
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
                        notes TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (profile_id, scenario_id)
                    );

                    CREATE TABLE flag_progress (
                        profile_id TEXT NOT NULL,
                        scenario_id TEXT NOT NULL,
                        flag_id TEXT NOT NULL,
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        solved INTEGER NOT NULL DEFAULT 0 CHECK (solved IN (0, 1)),
                        points_earned INTEGER,
                        solved_at TEXT,
                        PRIMARY KEY (profile_id, scenario_id, flag_id),
                        FOREIGN KEY (profile_id, scenario_id)
                            REFERENCES scenario_attempts(profile_id, scenario_id) ON DELETE CASCADE
                    );

                    CREATE TABLE hint_reveals (
                        profile_id TEXT NOT NULL,
                        scenario_id TEXT NOT NULL,
                        flag_id TEXT NOT NULL,
                        level INTEGER NOT NULL CHECK (level > 0),
                        point_cost INTEGER NOT NULL CHECK (point_cost >= 0),
                        revealed_at TEXT NOT NULL,
                        PRIMARY KEY (profile_id, scenario_id, flag_id, level),
                        FOREIGN KEY (profile_id, scenario_id)
                            REFERENCES scenario_attempts(profile_id, scenario_id) ON DELETE CASCADE
                    );

                    CREATE TABLE opened_documents (
                        profile_id TEXT NOT NULL,
                        scenario_id TEXT NOT NULL,
                        document_key TEXT NOT NULL,
                        opened_at TEXT NOT NULL,
                        PRIMARY KEY (profile_id, scenario_id, document_key),
                        FOREIGN KEY (profile_id, scenario_id)
                            REFERENCES scenario_attempts(profile_id, scenario_id) ON DELETE CASCADE
                    );

                    CREATE INDEX idx_attempts_profile ON scenario_attempts(profile_id);
                    CREATE INDEX idx_flag_progress_flag ON flag_progress(scenario_id, flag_id);
                    CREATE INDEX idx_hint_reveals_flag ON hint_reveals(scenario_id, flag_id, level);
                    """
                )
                now = utc_now()
                conn.execute(
                    "INSERT INTO application_metadata(key, value) VALUES (?, ?)",
                    ("schema_version", str(SCHEMA_VERSION)),
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
            now = utc_now()
            with conn:
                conn.executemany(
                    """INSERT INTO scenario_availability(scenario_id, enabled, updated_at)
                       VALUES (?, 1, ?) ON CONFLICT(scenario_id) DO NOTHING""",
                    [(scenario_id, now) for scenario_id in scenario_ids],
                )

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

    # -- profile-scoped attempt persistence ------------------------------------

    def get_attempt(self, profile_id: str, scenario_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            attempt = conn.execute(
                """SELECT * FROM scenario_attempts
                   WHERE profile_id = ? AND scenario_id = ?""",
                (profile_id, scenario_id),
            ).fetchone()
            if not attempt:
                return None
            flags = conn.execute(
                """SELECT flag_id, attempt_count, solved, points_earned, solved_at
                   FROM flag_progress WHERE profile_id = ? AND scenario_id = ?""",
                (profile_id, scenario_id),
            ).fetchall()
            hints = conn.execute(
                """SELECT flag_id, level, point_cost, revealed_at
                   FROM hint_reveals WHERE profile_id = ? AND scenario_id = ?
                   ORDER BY flag_id, level""",
                (profile_id, scenario_id),
            ).fetchall()
            docs = conn.execute(
                """SELECT document_key, opened_at FROM opened_documents
                   WHERE profile_id = ? AND scenario_id = ? ORDER BY document_key""",
                (profile_id, scenario_id),
            ).fetchall()
        result = dict(attempt)
        result["flags"] = [dict(row) for row in flags]
        result["hints"] = [dict(row) for row in hints]
        result["documents"] = [dict(row) for row in docs]
        return result

    def list_attempts(self, profile_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            ids = [
                row["scenario_id"]
                for row in conn.execute(
                    """SELECT scenario_id FROM scenario_attempts
                       WHERE profile_id = ? ORDER BY scenario_id""",
                    (profile_id,),
                ).fetchall()
            ]
        return [
            attempt for scenario_id in ids if (attempt := self.get_attempt(profile_id, scenario_id))
        ]

    def ensure_attempt(
        self,
        profile_id: str,
        scenario_id: str,
        *,
        mode: str,
        scored: bool,
        start: bool = False,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as conn, conn:
            row = conn.execute(
                "SELECT * FROM scenario_attempts WHERE profile_id = ? AND scenario_id = ?",
                (profile_id, scenario_id),
            ).fetchone()
            if row is None:
                status = "in_progress" if start else "not_started"
                started_at = now if start else None
                conn.execute(
                    """INSERT INTO scenario_attempts(
                           profile_id, scenario_id, status, mode, scored,
                           started_at, completed_at, solution_locked, notes,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, NULL, 0, '', ?, ?)""",
                    (profile_id, scenario_id, status, mode, int(scored), started_at, now, now),
                )
            else:
                if row["status"] != "not_started" and row["mode"] != mode:
                    raise StorageConflict("training mode is locked once an attempt starts")
                if start and row["status"] == "not_started":
                    conn.execute(
                        """UPDATE scenario_attempts SET status = 'in_progress',
                                  started_at = ?, updated_at = ?
                           WHERE profile_id = ? AND scenario_id = ?""",
                        (now, now, profile_id, scenario_id),
                    )
                elif row["status"] == "not_started":
                    conn.execute(
                        """UPDATE scenario_attempts SET mode = ?, scored = ?, updated_at = ?
                           WHERE profile_id = ? AND scenario_id = ?""",
                        (mode, int(scored), now, profile_id, scenario_id),
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
                   WHERE profile_id = ? AND scenario_id = ?""",
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
        """Record one hint level. Returns True only for a new reveal."""
        now = utc_now()
        with self.connection() as conn, conn:
            attempt = conn.execute(
                """SELECT solution_locked FROM scenario_attempts
                   WHERE profile_id = ? AND scenario_id = ?""",
                (profile_id, scenario_id),
            ).fetchone()
            if not attempt:
                raise StorageConflict("attempt does not exist")
            if attempt["solution_locked"]:
                raise StorageConflict("attempt is locked")
            if level > 1:
                previous = conn.execute(
                    """SELECT 1 FROM hint_reveals
                       WHERE profile_id = ? AND scenario_id = ? AND flag_id = ? AND level = ?""",
                    (profile_id, scenario_id, flag_id, level - 1),
                ).fetchone()
                if not previous:
                    raise StorageConflict("hint levels must be revealed sequentially")
            result = conn.execute(
                """INSERT OR IGNORE INTO hint_reveals(
                       profile_id, scenario_id, flag_id, level, point_cost, revealed_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (profile_id, scenario_id, flag_id, level, point_cost, now),
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
                """SELECT status, mode, solution_locked FROM scenario_attempts
                   WHERE profile_id = ? AND scenario_id = ?""",
                (profile_id, scenario_id),
            ).fetchone()
            if not attempt:
                raise StorageConflict("attempt does not exist")
            if attempt["solution_locked"]:
                raise StorageConflict("attempt is locked")
            conn.execute(
                """INSERT INTO flag_progress(
                       profile_id, scenario_id, flag_id, attempt_count, solved,
                       points_earned, solved_at
                   ) VALUES (?, ?, ?, 1, ?, ?, ?)
                   ON CONFLICT(profile_id, scenario_id, flag_id) DO UPDATE SET
                       attempt_count = flag_progress.attempt_count + 1,
                       solved = CASE WHEN flag_progress.solved = 1 THEN 1 ELSE excluded.solved END,
                       points_earned = CASE
                           WHEN flag_progress.solved = 1 THEN flag_progress.points_earned
                           ELSE excluded.points_earned END,
                       solved_at = CASE
                           WHEN flag_progress.solved = 1 THEN flag_progress.solved_at
                           ELSE excluded.solved_at END""",
                (
                    profile_id,
                    scenario_id,
                    flag_id,
                    int(correct),
                    points_earned if correct else None,
                    now if correct else None,
                ),
            )
            solved_count = conn.execute(
                """SELECT COUNT(*) FROM flag_progress
                   WHERE profile_id = ? AND scenario_id = ? AND solved = 1""",
                (profile_id, scenario_id),
            ).fetchone()[0]
            status = "in_progress"
            completed_at = None
            if all_flag_ids and solved_count == len(set(all_flag_ids)):
                hints_used = conn.execute(
                    """SELECT COUNT(*) FROM hint_reveals
                       WHERE profile_id = ? AND scenario_id = ?""",
                    (profile_id, scenario_id),
                ).fetchone()[0]
                status = (
                    "completed_with_assistance"
                    if hints_used and attempt["mode"] != "guided"
                    else "completed"
                )
                completed_at = now
            conn.execute(
                """UPDATE scenario_attempts SET status = ?,
                          completed_at = COALESCE(completed_at, ?), updated_at = ?
                   WHERE profile_id = ? AND scenario_id = ?""",
                (status, completed_at, now, profile_id, scenario_id),
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
                """SELECT status FROM scenario_attempts
                   WHERE profile_id = ? AND scenario_id = ?""",
                (profile_id, scenario_id),
            ).fetchone()
            if not attempt:
                raise StorageConflict("attempt does not exist")
            conn.execute(
                """INSERT OR IGNORE INTO opened_documents(
                       profile_id, scenario_id, document_key, opened_at
                   ) VALUES (?, ?, ?, ?)""",
                (profile_id, scenario_id, document_key, now),
            )
            if solution and lock_solution:
                conn.execute(
                    """UPDATE scenario_attempts SET status = 'solution_revealed',
                              solution_locked = 1,
                              completed_at = COALESCE(completed_at, ?), updated_at = ?
                       WHERE profile_id = ? AND scenario_id = ?""",
                    (now, now, profile_id, scenario_id),
                )
            else:
                conn.execute(
                    """UPDATE scenario_attempts SET updated_at = ?
                       WHERE profile_id = ? AND scenario_id = ?""",
                    (now, profile_id, scenario_id),
                )
            conn.execute("UPDATE profiles SET last_activity_at = ? WHERE id = ?", (now, profile_id))
        result = self.get_attempt(profile_id, scenario_id)
        if result is None:  # pragma: no cover
            raise RuntimeError("attempt disappeared during document open")
        return result

    def reset_attempt(self, profile_id: str, scenario_id: str) -> bool:
        with self.connection() as conn, conn:
            result = conn.execute(
                """DELETE FROM scenario_attempts
                   WHERE profile_id = ? AND scenario_id = ?""",
                (profile_id, scenario_id),
            )
            conn.execute(
                "UPDATE profiles SET last_activity_at = ? WHERE id = ?",
                (utc_now(), profile_id),
            )
        return bool(result.rowcount)

    def reset_profile_progress(self, profile_id: str) -> int:
        with self.connection() as conn, conn:
            result = conn.execute(
                "DELETE FROM scenario_attempts WHERE profile_id = ?", (profile_id,)
            )
            conn.execute(
                "UPDATE profiles SET last_activity_at = ? WHERE id = ?",
                (utc_now(), profile_id),
            )
        return result.rowcount

    # -- instructor analytics --------------------------------------------------

    def analytics(self) -> dict[str, Any]:
        with self.connection() as conn:
            profile_count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
            attempt_rows = conn.execute(
                "SELECT status, mode FROM scenario_attempts WHERE status != 'not_started'"
            ).fetchall()
            average = conn.execute(
                "SELECT AVG(points_earned) FROM flag_progress WHERE solved = 1"
            ).fetchone()[0]
            hints = conn.execute(
                """SELECT scenario_id, flag_id, COUNT(*) AS uses
                   FROM hint_reveals GROUP BY scenario_id, flag_id
                   ORDER BY uses DESC, scenario_id, flag_id LIMIT 10"""
            ).fetchall()
            missed = conn.execute(
                """SELECT scenario_id, flag_id,
                          SUM(attempt_count - CASE WHEN solved = 1 THEN 1 ELSE 0 END) AS misses
                   FROM flag_progress GROUP BY scenario_id, flag_id
                   HAVING misses > 0 ORDER BY misses DESC, scenario_id, flag_id LIMIT 10"""
            ).fetchall()
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
        }
