"""Historian integration test — the M3 verification.

Brings up the real stack from docker-compose.yml (process-sim, OpenPLC,
the configure job, postgres, and the historian) on an isolated compose
project, then confirms rows actually landed in Postgres via
historian/ingest.py's real poll loop against the real running PLC — not
a mock insert, not a reimplementation of the pipeline.

Requires Docker and psql inside the postgres container (both already
true of the images this test builds); marked `docker`, auto-skipped
when Docker isn't reachable (see conftest.py). Run explicitly with
`pytest -m docker` or `make test-docker`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid

import pytest

from tests.docker_harness import REPO_ROOT

pytestmark = pytest.mark.docker

COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

# Detect docker-compose binary — use `docker compose` (plugin) if available,
# fall back to standalone `docker-compose`. GitHub Actions runners often have
# only the plugin, not the standalone binary.
_COMPOSE_CMD = "docker-compose" if shutil.which("docker-compose") else "docker"
_COMPOSE_ARGS = [] if shutil.which("docker-compose") else ["compose"]

IMAGE_BUILDS = [
    ("ot-range-process-sim", "process_sim/Dockerfile", "."),
    ("ot-range-openplc", "plc/openplc/Dockerfile", "plc/openplc"),
    ("ot-range-openplc-configure", "process_sim/Dockerfile", "."),
    ("ot-range-hmi", "hmi/Dockerfile", "."),
    ("ot-range-historian", "historian/Dockerfile", "."),
    # router is a normal always-up service in docker-compose.yml since
    # M4, so it's part of this stack too even though this test doesn't
    # exercise it directly.
    ("ot-range-router", "router/Dockerfile", "."),
]


def _ensure_images_built() -> None:
    for tag, dockerfile, context in IMAGE_BUILDS:
        if (
            subprocess.run(
                ["docker", "image", "inspect", tag], capture_output=True, timeout=10
            ).returncode
            == 0
        ):
            continue
        result = subprocess.run(
            ["docker", "build", "-t", tag, "-f", dockerfile, context],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert result.returncode == 0, f"{tag} build failed:\n{result.stdout}\n{result.stderr}"


def _compose(project: str, *args: str, timeout: float = 300.0) -> subprocess.CompletedProcess:
    # Distinct host ports *and* zone subnets from docker-compose.yml's
    # defaults (and from tests/test_router.py's own overrides) so this
    # isolated stack can run alongside a normal `make up` or another
    # concurrent test run without fighting over 8080/8090/3000 or a
    # pinned Docker network CIDR — Docker refuses two networks with
    # overlapping subnets even across different compose projects.
    env = {
        **os.environ,
        "OPENPLC_HTTP_PORT": "18080",
        "OPENPLC_MODBUS_PORT": "15020",
        "HMI_PORT": "18090",
        "GRAFANA_PORT": "13000",
        "ZONE_ENTERPRISE_SUBNET": "10.22.0.0/24",
        "ZONE_OPS_SUBNET": "10.32.0.0/24",
    }
    return subprocess.run(
        [_COMPOSE_CMD, *_COMPOSE_ARGS, "-p", project, "-f", str(COMPOSE_FILE), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def test_historian_ingests_readings_from_the_real_stack():
    _ensure_images_built()
    project = f"ot-range-hist-test-{uuid.uuid4().hex[:8]}"
    try:
        up = _compose(project, "up", "--wait", "--wait-timeout", "180", timeout=300)
        assert up.returncode == 0, up.stdout + up.stderr

        # openplc-configure must finish (PLC compiled, running, registered
        # as a slave device) before the historian has anything real to
        # poll; --wait above already blocks on that since hmi/historian
        # depend on it completing successfully. Give the historian a
        # couple of its own 5s poll cycles on top of that.
        time.sleep(15)

        query = _compose(
            project,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "historian",
            "-d",
            "historian",
            "-t",
            "-c",
            "SELECT count(*), max(level_pct) FROM process_history",
        )
        assert query.returncode == 0, query.stdout + query.stderr

        count_str, level_str = (part.strip() for part in query.stdout.strip().split("|"))
        count = int(count_str)
        assert count > 0, f"no rows landed in process_history after startup:\n{query.stdout}"
        # A real level reading, not a stale zero from a broken poll.
        assert float(level_str) > 0, f"level_pct never above zero:\n{query.stdout}"
    finally:
        _compose(project, "down", "-v", "--remove-orphans", timeout=120)
