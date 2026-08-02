"""HTTP authorization boundaries for the Instructor Console."""

from __future__ import annotations

import panel.app as panel_app

TEST_PASSWORD = "a sufficiently long instructor password"  # noqa: S105
NEW_PASSWORD = "a different sufficiently long password"  # noqa: S105


def setup_instructor(client, password=TEST_PASSWORD):
    response = client.post("/api/instructor/setup", json={"password": password})
    assert response.status_code == 201
    return response


def test_fresh_status_and_first_time_setup(panel_client):
    assert panel_client.get("/api/instructor/status").get_json() == {
        "configured": False,
        "authenticated": False,
    }
    response = setup_instructor(panel_client)
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert TEST_PASSWORD not in cookie
    assert panel_client.get("/api/instructor/status").get_json() == {
        "configured": True,
        "authenticated": True,
    }
    assert (
        panel_client.post("/api/instructor/setup", json={"password": NEW_PASSWORD}).status_code
        == 409
    )


def test_unauthenticated_instructor_page_redirects_and_all_apis_deny(panel_client):
    page = panel_client.get("/instructor")
    assert page.status_code == 302
    assert "/instructor/login" in page.headers["Location"]
    protected = [
        ("get", "/api/instructor/overview"),
        ("get", "/api/instructor/profiles"),
        ("get", "/api/instructor/profiles/export-all"),
        ("get", "/api/instructor/policies"),
        ("put", "/api/instructor/policies"),
        ("post", "/api/instructor/change-password"),
        ("post", "/api/instructor/sessions/invalidate"),
        ("get", "/api/instructor/docs/S01/answer-key"),
        ("delete", "/api/instructor/profiles/not-real"),
        ("post", "/api/instructor/profiles/not-real/reset"),
    ]
    for method, path in protected:
        assert getattr(panel_client, method)(path, json={}).status_code == 401, path


def test_login_failure_is_generic_throttled_and_leaks_no_hash(panel_client):
    setup_instructor(panel_client)
    panel_client.post("/api/instructor/logout")
    attempted = "this incorrect password must never persist"
    first = panel_client.post("/api/instructor/login", json={"password": attempted})
    assert first.status_code == 401
    assert "Incorrect credentials" in first.get_json()["error"]
    assert attempted not in first.get_data(as_text=True)
    second = panel_client.post("/api/instructor/login", json={"password": attempted})
    assert second.status_code == 429
    assert "Retry-After" in second.headers

    with panel_app.app.app_context():
        credentials = panel_app.get_storage().get_instructor_credentials()
        secret_fragments = [credentials["salt"].hex(), credentials["password_hash"].hex()]
    bodies = [
        panel_client.get("/api/instructor/status").get_data(as_text=True),
        first.get_data(as_text=True),
        second.get_data(as_text=True),
    ]
    assert all(fragment not in "".join(bodies) for fragment in secret_fragments)


def test_login_logout_and_password_change_invalidate_sessions(panel_client):
    setup_instructor(panel_client)
    logout = panel_client.post("/api/instructor/logout")
    assert logout.status_code == 200
    assert panel_client.get("/api/instructor/overview").status_code == 401

    login = panel_client.post("/api/instructor/login", json={"password": TEST_PASSWORD})
    assert login.status_code == 200
    changed = panel_client.post(
        "/api/instructor/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 200
    assert panel_client.get("/api/instructor/overview").status_code == 401
    assert (
        panel_client.post("/api/instructor/login", json={"password": TEST_PASSWORD}).status_code
        == 401
    )


def test_all_session_invalidation_requires_reauthentication(panel_client):
    setup_instructor(panel_client)
    denied = panel_client.post(
        "/api/instructor/sessions/invalidate", json={"password": "incorrect password"}
    )
    assert denied.status_code == 401
    accepted = panel_client.post(
        "/api/instructor/sessions/invalidate", json={"password": TEST_PASSWORD}
    )
    assert accepted.status_code == 200
    assert panel_client.get("/api/instructor/overview").status_code == 401
