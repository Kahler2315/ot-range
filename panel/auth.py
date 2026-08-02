"""Instructor password hashing, throttling, and opaque server sessions.

Scrypt is intentionally from Python's standard library: it provides a
memory-hard password hash without adding a packaging dependency during
this local-application phase.  Raw passwords never leave this module and
are never logged or persisted.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from panel.storage import Storage, StorageConflict

MINIMUM_PASSWORD_LENGTH = 12
DEFAULT_SCRYPT_N = 2**14
DEFAULT_SCRYPT_R = 8
DEFAULT_SCRYPT_P = 1
DEFAULT_IDLE_TIMEOUT = timedelta(minutes=30)
DEFAULT_ABSOLUTE_TIMEOUT = timedelta(hours=8)
MAX_THROTTLE_SECONDS = 60


class AuthenticationError(RuntimeError):
    """Generic credential failure safe to translate to a public error."""


class AuthenticationNotConfigured(AuthenticationError):
    pass


class AuthenticationThrottled(AuthenticationError):
    def __init__(self, retry_after: int):
        super().__init__("authentication temporarily unavailable")
        self.retry_after = max(1, retry_after)


class PasswordValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SessionValidation:
    authenticated: bool
    reason: str | None = None


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class InstructorAuth:
    def __init__(
        self,
        storage: Storage,
        *,
        scrypt_n: int = DEFAULT_SCRYPT_N,
        scrypt_r: int = DEFAULT_SCRYPT_R,
        scrypt_p: int = DEFAULT_SCRYPT_P,
        idle_timeout: timedelta = DEFAULT_IDLE_TIMEOUT,
        absolute_timeout: timedelta = DEFAULT_ABSOLUTE_TIMEOUT,
        throttle_base_seconds: int = 1,
        now: Callable[[], datetime] | None = None,
    ):
        self.storage = storage
        self.scrypt_n = scrypt_n
        self.scrypt_r = scrypt_r
        self.scrypt_p = scrypt_p
        self.idle_timeout = idle_timeout
        self.absolute_timeout = absolute_timeout
        self.throttle_base_seconds = throttle_base_seconds
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def configured(self) -> bool:
        return self.storage.get_instructor_credentials() is not None

    @staticmethod
    def validate_new_password(password: str) -> None:
        if not isinstance(password, str) or len(password) < MINIMUM_PASSWORD_LENGTH:
            raise PasswordValidationError(
                f"Instructor password must be at least {MINIMUM_PASSWORD_LENGTH} characters."
            )

    @staticmethod
    def _derive(password: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=32,
        )

    def _password_record(self, password: str) -> tuple[bytes, bytes]:
        salt = secrets.token_bytes(32)
        derived = self._derive(
            password,
            salt,
            n=self.scrypt_n,
            r=self.scrypt_r,
            p=self.scrypt_p,
        )
        return salt, derived

    def setup(self, password: str) -> str:
        self.validate_new_password(password)
        salt, derived = self._password_record(password)
        try:
            version = self.storage.set_instructor_credentials(
                salt=salt,
                password_hash=derived,
                scrypt_n=self.scrypt_n,
                scrypt_r=self.scrypt_r,
                scrypt_p=self.scrypt_p,
                only_if_unconfigured=True,
            )
        except StorageConflict as exc:
            raise AuthenticationError("instructor setup is unavailable") from exc
        self.storage.set_throttle(0, None, None)
        self.storage.record_security_event("instructor_password_configured")
        return self._create_session(version)

    def verify_password(self, password: str) -> bool:
        credentials = self.storage.get_instructor_credentials()
        if credentials is None or not isinstance(password, str):
            return False
        candidate = self._derive(
            password,
            credentials["salt"],
            n=credentials["scrypt_n"],
            r=credentials["scrypt_r"],
            p=credentials["scrypt_p"],
        )
        return secrets.compare_digest(candidate, credentials["password_hash"])

    def login(self, password: str) -> str:
        credentials = self.storage.get_instructor_credentials()
        now = self._now()
        throttle = self.storage.get_throttle()
        blocked_until = (
            _parse_timestamp(throttle["blocked_until"]) if throttle["blocked_until"] else None
        )
        if blocked_until and blocked_until > now:
            retry = int((blocked_until - now).total_seconds()) + 1
            raise AuthenticationThrottled(retry)
        if credentials is None:
            self._record_failure(now, int(throttle["failure_count"]))
            raise AuthenticationNotConfigured("incorrect credentials")
        if not self.verify_password(password):
            self._record_failure(now, int(throttle["failure_count"]))
            raise AuthenticationError("incorrect credentials")

        self.storage.set_throttle(0, None, None)
        self.storage.record_security_event("instructor_login_succeeded")
        return self._create_session(int(credentials["password_version"]))

    def _record_failure(self, now: datetime, previous_count: int) -> None:
        count = previous_count + 1
        delay = min(
            MAX_THROTTLE_SECONDS,
            self.throttle_base_seconds * (2 ** min(count - 1, 10)),
        )
        blocked_until = now + timedelta(seconds=delay)
        self.storage.set_throttle(count, blocked_until.isoformat(), now.isoformat())
        self.storage.record_security_event(
            "instructor_login_failed", {"failure_count": count, "delay_seconds": delay}
        )

    def _create_session(self, password_version: int) -> str:
        token = secrets.token_urlsafe(32)
        now = self._now()
        self.storage.create_instructor_session(
            token,
            password_version,
            created_at=now.isoformat(),
            expires_at=(now + self.absolute_timeout).isoformat(),
        )
        return token

    def validate_session(self, token: str | None) -> SessionValidation:
        if not token:
            return SessionValidation(False, "missing")
        session = self.storage.get_instructor_session(token)
        credentials = self.storage.get_instructor_credentials()
        if session is None or credentials is None:
            return SessionValidation(False, "unknown")
        now = self._now()
        expired = now >= _parse_timestamp(session["expires_at"])
        inactive = now - _parse_timestamp(session["last_seen_at"]) >= self.idle_timeout
        stale_password = session["password_version"] != credentials["password_version"]
        if expired or inactive or stale_password:
            self.storage.delete_instructor_session(token)
            return SessionValidation(False, "expired")
        self.storage.touch_instructor_session(token, now.isoformat())
        return SessionValidation(True)

    def logout(self, token: str | None) -> None:
        if token:
            self.storage.delete_instructor_session(token)
        self.storage.record_security_event("instructor_logout")

    def change_password(self, token: str | None, current_password: str, new_password: str) -> None:
        if not self.validate_session(token).authenticated or not self.verify_password(
            current_password
        ):
            raise AuthenticationError("incorrect credentials")
        self.validate_new_password(new_password)
        salt, derived = self._password_record(new_password)
        self.storage.set_instructor_credentials(
            salt=salt,
            password_hash=derived,
            scrypt_n=self.scrypt_n,
            scrypt_r=self.scrypt_r,
            scrypt_p=self.scrypt_p,
        )
        self.storage.record_security_event("instructor_password_changed")

    def invalidate_all(self, token: str | None, password: str) -> int:
        if not self.validate_session(token).authenticated or not self.verify_password(password):
            raise AuthenticationError("incorrect credentials")
        count = self.storage.delete_all_instructor_sessions()
        self.storage.record_security_event("instructor_sessions_invalidated", {"count": count})
        return count
