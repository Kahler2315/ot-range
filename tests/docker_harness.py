"""Docker-based test harness for the OpenPLC integration.

Builds (once, cached) and runs process-sim + OpenPLC on an isolated
docker network, using the same images and configuration flow as real
bring-up (tools/openplc_configure.py) — no shortcuts that would let a
test pass while the real bring-up path is broken.

Requires Docker. Tests using this harness are marked `docker` and
skipped automatically when Docker isn't reachable — see conftest.py.
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PROCESS_SIM_IMAGE = "ot-range/process-sim:test"
OPENPLC_IMAGE = "ot-range/openplc:test"


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


def _run(*args: str, timeout: float = 600.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )


def ensure_images_built() -> None:
    """Build once; subsequent test runs reuse the cached image."""
    if _run("image", "inspect", PROCESS_SIM_IMAGE).returncode != 0:
        result = _run(
            "build",
            "-t",
            PROCESS_SIM_IMAGE,
            "-f",
            "process_sim/Dockerfile",
            ".",
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"process-sim image build failed:\n{result.stdout}\n{result.stderr}")

    if _run("image", "inspect", OPENPLC_IMAGE).returncode != 0:
        result = _run(
            "build",
            "-t",
            OPENPLC_IMAGE,
            "-f",
            "plc/openplc/Dockerfile",
            "plc/openplc",
            timeout=900,  # first build compiles OpenPLC from source, several minutes
        )
        if result.returncode != 0:
            raise RuntimeError(f"OpenPLC image build failed:\n{result.stdout}\n{result.stderr}")


def _wait_for_http(url: str, timeout_s: float = 30.0) -> None:
    import urllib.request

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            time.sleep(0.5)
    raise TimeoutError(f"{url} did not respond within {timeout_s}s")


@dataclasses.dataclass
class OpenPLCStack:
    network: str
    process_sim_container: str
    openplc_container: str

    def run_in_openplc_netns(
        self, script: str, timeout: float = 90.0
    ) -> subprocess.CompletedProcess:
        """Run a Python script sharing the openplc container's network
        namespace — needed because OpenPLC's own Modbus master requires a
        literal IP (see tools/openplc_configure.py) and its own slave
        port is only reachable from inside that namespace in these tests
        (not published to the host).

        Writes the script to a file and mounts it in rather than
        embedding it in a shell -c string: the repo path contains a
        space, and script content containing quotes previously broke
        shell-string interpolation in ways that failed silently.
        """
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", dir=REPO_ROOT / "tests", delete=False
        ) as fh:
            fh.write(script)
            script_path = Path(fh.name)

        try:
            return subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    f"container:{self.openplc_container}",
                    "-v",
                    f"{REPO_ROOT}:/app",
                    "-w",
                    "/app",
                    "python:3.11-slim",
                    "bash",
                    "-c",
                    f"pip install -q requests pymodbus >/dev/null 2>&1 "
                    f"&& python tests/{script_path.name}",
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        finally:
            script_path.unlink(missing_ok=True)

    def configure(self, st_file: str) -> subprocess.CompletedProcess:
        # st_file is a host path (REPO_ROOT/...); translate to where the
        # repo is mounted inside the container.
        container_path = "/app/" + str(Path(st_file).resolve().relative_to(REPO_ROOT))

        # Resolve process-sim's IP from the host rather than relying on
        # DNS inside the ephemeral --network container:openplc joiner —
        # that resolution is unreliable (works for 127.0.0.1, which needs
        # no DNS, but not reliably for other names in that mode), whereas
        # `docker inspect` from the host is always reliable.
        field_ip = _run(
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            self.process_sim_container,
        ).stdout.strip()
        if not field_ip:
            raise RuntimeError(f"could not resolve IP for {self.process_sim_container}")

        return self.run_in_openplc_netns(
            "import sys\n"
            "sys.path.insert(0, '/app')\n"
            "from tools.openplc_configure import bring_up_cedar_hollow\n"
            f"bring_up_cedar_hollow('http://127.0.0.1:8080', {container_path!r}, "
            f"{field_ip!r}, 5502)\n",
            timeout=120,
        )

    def swap_program(self, st_file: str) -> subprocess.CompletedProcess:
        """Upload, compile, and restart with a different program on an
        already-configured PLC — the S06 attack path: login, replace the
        logic, restart, with the previously-registered slave device
        config untouched (it persists in OpenPLC's own DB and reloads on
        restart, same as a real attacker would find it).
        """
        container_path = "/app/" + str(Path(st_file).resolve().relative_to(REPO_ROOT))
        return self.run_in_openplc_netns(
            "import sys\n"
            "sys.path.insert(0, '/app')\n"
            "from tools.openplc_configure import OpenPLCClient\n"
            "client = OpenPLCClient('http://127.0.0.1:8080')\n"
            "client.login()\n"
            f"client.upload_and_compile({container_path!r}, name='S06 attack payload')\n"
            "client.start_plc()\n",
            timeout=120,
        )


def _free_container_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class stack_context:
    """Context manager standing up process-sim + OpenPLC on an isolated
    network, torn down (containers + network) on exit regardless of
    outcome."""

    def __init__(self, level_pct: float = 55.0, speed: float = 600.0) -> None:
        self.level_pct = level_pct
        self.speed = speed
        self.network = _free_container_name("ot-range-net")
        self.process_sim = _free_container_name("process-sim")
        self.openplc = _free_container_name("openplc")

    def __enter__(self) -> OpenPLCStack:
        ensure_images_built()
        _run("network", "create", self.network)
        result = _run(
            "run",
            "-d",
            "--name",
            self.process_sim,
            "--network",
            self.network,
            PROCESS_SIM_IMAGE,
            "--speed",
            str(self.speed),
            "--tick",
            "1",
            "--level",
            str(self.level_pct),
        )
        if result.returncode != 0:
            raise RuntimeError(f"process-sim failed to start:\n{result.stderr}")

        result = _run("run", "-d", "--name", self.openplc, "--network", self.network, OPENPLC_IMAGE)
        if result.returncode != 0:
            raise RuntimeError(f"openplc failed to start:\n{result.stderr}")

        # Wait for the webserver, using the same network-namespace trick
        # since we don't publish ports to the host in these tests.
        deadline = time.time() + 30
        while time.time() < deadline:
            check = subprocess.run(
                [
                    "docker",
                    "exec",
                    self.openplc,
                    "python3",
                    "-c",
                    "import socket; socket.create_connection(('127.0.0.1', 8080), timeout=1)",
                ],
                capture_output=True,
                timeout=5,
            )
            if check.returncode == 0:
                break
            time.sleep(1)
        else:
            raise TimeoutError("OpenPLC webserver did not become reachable")

        return OpenPLCStack(self.network, self.process_sim, self.openplc)

    def __exit__(self, *exc_info) -> None:
        _run("stop", self.openplc, self.process_sim, timeout=30)
        _run("rm", "-f", self.openplc, self.process_sim, timeout=30)
        _run("network", "rm", self.network, timeout=30)
