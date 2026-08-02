import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def pytest_collection_modifyitems(config, items):
    """Auto-skip @pytest.mark.docker tests when Docker isn't reachable,
    rather than failing — most contributors won't have it set up."""
    from tests.docker_harness import docker_available

    if docker_available():
        return
    skip_docker = pytest.mark.skip(reason="Docker not available in this environment")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip_docker)


@pytest.fixture
def panel_client(tmp_path):
    """Fresh isolated panel database and cookie jar for each API test."""
    import panel.app as panel_app

    panel_app.app.config.update(
        TESTING=True,
        DATABASE_PATH=str(tmp_path / "instance" / "ot-range.db"),
        SCRYPT_N=2**10,
        AUTH_THROTTLE_BASE_SECONDS=1,
    )
    panel_app.app.extensions.pop("ot_range_storage", None)
    panel_app.app.extensions.pop("ot_range_auth", None)
    with panel_app.app.test_client() as client:
        yield client
    with panel_app._jobs_guard:
        panel_app._jobs.clear()
        panel_app._current_job_id = None
    panel_app.app.extensions.pop("ot_range_storage", None)
    panel_app.app.extensions.pop("ot_range_auth", None)


@pytest.fixture
def student_client(panel_client):
    response = panel_client.post("/api/profiles", json={"display_name": "Test Learner"})
    assert response.status_code == 201
    panel_client.profile = response.get_json()["profile"]
    return panel_client
