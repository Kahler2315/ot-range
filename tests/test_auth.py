"""Instructor credential, throttling, and opaque-session security tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from panel.auth import (
    AuthenticationError,
    AuthenticationThrottled,
    InstructorAuth,
    PasswordValidationError,
)
from panel.storage import Storage


@pytest.fixture
def clock():
    current = [datetime(2026, 8, 2, 12, 0, tzinfo=UTC)]

    def now():
        return current[0]

    now.advance = lambda **kwargs: current.__setitem__(0, current[0] + timedelta(**kwargs))
    return now


@pytest.fixture
def auth(tmp_path, clock):
    storage = Storage(tmp_path / "auth.db")
    storage.initialize()
    return InstructorAuth(
        storage,
        scrypt_n=2**10,
        throttle_base_seconds=1,
        now=clock,
    )


def test_first_time_setup_hashes_password_and_succeeds_only_once(auth):
    password = "a long local instructor passphrase"  # noqa: S105 -- test credential
    token = auth.setup(password)
    credentials = auth.storage.get_instructor_credentials()

    assert token
    assert credentials["password_hash"] != password.encode()
    assert credentials["salt"] != password.encode()
    assert password not in auth.storage.path.read_bytes().decode("utf-8", errors="ignore")
    assert auth.verify_password(password) is True
    with pytest.raises(AuthenticationError):
        auth.setup("another sufficiently long passphrase")


def test_password_setup_uses_unique_random_salts(tmp_path, clock):
    salts = []
    for index in range(2):
        storage = Storage(tmp_path / f"auth-{index}.db")
        storage.initialize()
        instance = InstructorAuth(storage, scrypt_n=2**10, now=clock)
        instance.setup("same sufficiently long password")
        salts.append(storage.get_instructor_credentials()["salt"])
    assert salts[0] != salts[1]


def test_short_password_is_rejected(auth):
    with pytest.raises(PasswordValidationError):
        auth.setup("too short")


def test_correct_login_creates_opaque_server_session(auth, clock):
    password = "correct horse battery staple"  # noqa: S105 -- test credential
    auth.setup(password)
    clock.advance(seconds=2)
    token = auth.login(password)

    assert auth.validate_session(token).authenticated is True
    assert token.encode() not in auth.storage.path.read_bytes()


def test_incorrect_password_is_generic_and_persistently_throttled(auth, clock):
    auth.setup("correct horse battery staple")
    with pytest.raises(AuthenticationError, match="incorrect credentials"):
        auth.login("this is definitely not correct")

    throttle = auth.storage.get_throttle()
    assert throttle["failure_count"] == 1
    assert throttle["blocked_until"] is not None
    with pytest.raises(AuthenticationThrottled) as blocked:
        auth.login("another incorrect password")
    assert blocked.value.retry_after >= 1

    # A new auth object sees the same SQLite throttling state.
    restarted = InstructorAuth(auth.storage, scrypt_n=2**10, now=clock)
    with pytest.raises(AuthenticationThrottled):
        restarted.login("still incorrect password")


def test_throttle_delay_increases_after_each_failure(auth, clock):
    auth.setup("correct horse battery staple")
    with pytest.raises(AuthenticationError):
        auth.login("wrong password number one")
    first_until = auth.storage.get_throttle()["blocked_until"]
    clock.advance(seconds=2)
    with pytest.raises(AuthenticationError):
        auth.login("wrong password number two")
    second_until = auth.storage.get_throttle()["blocked_until"]
    assert datetime.fromisoformat(second_until) - clock() > datetime.fromisoformat(
        first_until
    ) - datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def test_session_inactivity_and_absolute_expiration(auth, clock):
    token = auth.setup("correct horse battery staple")
    clock.advance(minutes=29)
    assert auth.validate_session(token).authenticated is True
    clock.advance(minutes=31)
    assert auth.validate_session(token).authenticated is False

    clock.advance(seconds=2)
    token2 = auth.login("correct horse battery staple")
    for _ in range(8):
        clock.advance(minutes=29)
        auth.validate_session(token2)
    clock.advance(hours=5)
    assert auth.validate_session(token2).authenticated is False


def test_logout_invalidates_session(auth):
    token = auth.setup("correct horse battery staple")
    auth.logout(token)
    assert auth.validate_session(token).authenticated is False


def test_password_change_reauthenticates_and_invalidates_every_session(auth, clock):
    old_password = "correct horse battery staple"  # noqa: S105 -- test credential
    new_password = "an entirely new long passphrase"  # noqa: S105 -- test credential
    token1 = auth.setup(old_password)
    clock.advance(seconds=2)
    token2 = auth.login(old_password)

    with pytest.raises(AuthenticationError):
        auth.change_password(token1, "incorrect current password", new_password)
    auth.change_password(token1, old_password, new_password)
    assert auth.validate_session(token1).authenticated is False
    assert auth.validate_session(token2).authenticated is False
    assert auth.verify_password(old_password) is False
    assert auth.verify_password(new_password) is True


def test_invalidate_all_sessions_requires_password(auth, clock):
    password = "correct horse battery staple"  # noqa: S105 -- test credential
    token1 = auth.setup(password)
    clock.advance(seconds=2)
    token2 = auth.login(password)
    with pytest.raises(AuthenticationError):
        auth.invalidate_all(token1, "incorrect password here")
    assert auth.invalidate_all(token1, password) == 2
    assert auth.validate_session(token1).authenticated is False
    assert auth.validate_session(token2).authenticated is False


def test_security_events_never_store_attempted_passwords(auth):
    auth.setup("correct horse battery staple")
    attempted = "a wrong secret that must not be stored"
    with pytest.raises(AuthenticationError):
        auth.login(attempted)
    events = auth.storage.list_security_events()
    assert attempted not in str(events)
