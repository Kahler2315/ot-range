"""Router/zone-network integration test — the M4 verification.

Brings up the real stack from docker-compose.yml (including the M4
zone-enterprise/zone-ops networks, the router, and the on-demand
attacker container) on an isolated compose project, then proves the
three real claims M4 makes:

1. Zone isolation is genuine: the attacker container cannot reach
   openplc directly (no route between zone-enterprise and zone-ops
   except through router) — not a policy that could be misconfigured
   away, an actual Docker network property.
2. The router's Zeek + Suricata are real packet capture, not a
   synthetic log: running S01 recon through the router produces a real
   modbus.log/modbus_detailed.log with the attacker's real container IP
   in id.orig_h.
3. sensor/detect.py's rules — unmodified from the M1 loopback stack —
   still catch the attack when reading that real Zeek output through
   sensor/zeek_reader.py's adapter. This is the payoff of the whole
   "Zeek-compatible field names" design decision made back at M1.

Requires Docker; marked `docker`, auto-skipped when Docker isn't
reachable (see conftest.py). Run explicitly with `pytest -m docker` or
`make test-docker`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid

import pytest

from sensor.detect import Baseline, Detector
from sensor.zeek_reader import load_records
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
    ("ot-range-router", "router/Dockerfile", "."),
    ("ot-range-attacker", "attacker/Dockerfile", "."),
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
    # Distinct host ports so this isolated stack can run alongside a
    # normal `make up` (or another concurrent test run) without fighting
    # over 8080/8090/3000 — same reasoning as tests/test_historian.py.
    env = {
        **os.environ,
        "OPENPLC_HTTP_PORT": "18081",
        "OPENPLC_MODBUS_PORT": "15021",
        "HMI_PORT": "18091",
        "GRAFANA_PORT": "13001",
        # Distinct from docker-compose.yml's defaults: Docker refuses two
        # networks with overlapping subnets even across compose projects,
        # so this can't reuse 10.20.0.0/24 / 10.30.0.0/24 while a normal
        # `make up` (or another test run) might already hold them.
        "ZONE_ENTERPRISE_SUBNET": "10.21.0.0/24",
        "ZONE_OPS_SUBNET": "10.31.0.0/24",
    }
    return subprocess.run(
        [_COMPOSE_CMD, *_COMPOSE_ARGS, "-p", project, "-f", str(COMPOSE_FILE), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _run_attacker(
    project: str, *args: str, timeout: float = 60.0, entrypoint: str | None = None
) -> subprocess.CompletedProcess:
    # --entrypoint is a `docker-compose run` option: it must precede the
    # service name, not follow it — anything after "attacker" becomes
    # the container command, appended to the image's own entrypoint.
    entrypoint_flags = ["--entrypoint", entrypoint] if entrypoint else []
    return _compose(
        project,
        "--profile",
        "attacker",
        "run",
        "--rm",
        "--no-deps",
        *entrypoint_flags,
        "attacker",
        *args,
        timeout=timeout,
    )


def test_router_isolates_zones_and_real_zeek_output_is_still_detected():
    _ensure_images_built()
    project = f"ot-range-router-test-{uuid.uuid4().hex[:8]}"
    try:
        up = _compose(project, "up", "--wait", "--wait-timeout", "180", timeout=300)
        assert up.returncode == 0, up.stdout + up.stderr

        # --- claim 1: zone isolation is real, not just undialed ---
        # A raw connect from zone-enterprise straight to openplc's real
        # zone-ops IP must fail — no route exists except through router.
        # Resolve openplc's zone-ops address from the host (which is on
        # neither zone network) rather than assuming an IP, matching
        # tests/docker_harness.py's own reasoning for the OpenPLC tests.
        inspect = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                '{{(index .NetworkSettings.Networks "' + project + '_zone-ops").IPAddress}}',
                f"{project}-openplc-1",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        openplc_ip = inspect.stdout.strip()
        assert openplc_ip, f"could not resolve openplc's zone-ops IP:\n{inspect.stderr}"

        isolation_check = _run_attacker(
            project,
            "-c",
            "import socket, sys; socket.setdefaulttimeout(3)\n"
            f"try:\n    socket.create_connection(('{openplc_ip}', 502))\n"
            "except OSError:\n    sys.exit(0)\n"
            "sys.exit(1)  # connected — isolation is broken\n",
            timeout=30,
            entrypoint="python3",
        )
        assert isolation_check.returncode == 0, (
            "attacker container reached openplc directly — zone isolation is broken:\n"
            + isolation_check.stdout
            + isolation_check.stderr
        )

        # --- claim 2 + 3: real capture through the router still
        # produces something sensor/detect.py's unmodified rules catch ---
        s01 = _run_attacker(
            project,
            "attacker.s01_recon",
            "--host",
            "router",
            "--port",
            "502",
            "--unit-id-max",
            "3",
            "--sweep-count",
            "5",
            timeout=60,
        )
        assert s01.returncode == 0, s01.stdout + s01.stderr
        assert "port 502 open" in s01.stdout

        time.sleep(1)  # let Zeek/Suricata flush their last writes

        modbus_log = _compose(project, "exec", "-T", "router", "cat", "/zeek-logs/modbus.log")
        assert modbus_log.returncode == 0, modbus_log.stdout + modbus_log.stderr
        modbus_detailed_log = _compose(
            project, "exec", "-T", "router", "cat", "/zeek-logs/modbus_detailed.log"
        )
        assert modbus_detailed_log.returncode == 0, (
            modbus_detailed_log.stdout + modbus_detailed_log.stderr
        )
        assert modbus_log.stdout.strip(), "router produced no modbus.log — Zeek never saw traffic"

        fast_log = _compose(project, "exec", "-T", "router", "cat", "/zeek-logs/fast.log")
        assert fast_log.returncode == 0, fast_log.stdout + fast_log.stderr
        assert "9000005" in fast_log.stdout, (
            "Suricata's custom zone-enterprise-boundary rule never fired on real traffic:\n"
            + fast_log.stdout
        )

        modbus_log_path = REPO_ROOT / f".pytest-zeek-{project}-modbus.log"
        detailed_log_path = REPO_ROOT / f".pytest-zeek-{project}-modbus_detailed.log"
        modbus_log_path.write_text(modbus_log.stdout)
        detailed_log_path.write_text(modbus_detailed_log.stdout)
        try:
            records = load_records(modbus_log_path, detailed_log_path)
            assert records, "adapter produced no records from real Zeek output"

            alerts = Detector(Baseline.load()).analyze(records)
            rule_ids = {a.rule_id for a in alerts}
            assert "MODBUS_UNAUTHORIZED_SOURCE" in rule_ids, (
                f"detect.py did not flag the recon traffic from real Zeek output: {rule_ids}"
            )
        finally:
            modbus_log_path.unlink(missing_ok=True)
            detailed_log_path.unlink(missing_ok=True)
    finally:
        _compose(project, "down", "-v", "--remove-orphans", timeout=120)
