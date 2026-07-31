"""Spin up a throwaway range: simulator behind a tap, on free ports.

Used by the attack→detection tests. Every instance gets its own ports and
its own log file so tests can run without colliding.
"""

from __future__ import annotations

import contextlib
import dataclasses
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

HMI_SOURCE_IP = "127.0.0.1"
ATTACKER_SOURCE_IP = "127.0.0.2"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for_port(host: str, port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"nothing listening on {host}:{port} after {timeout}s")


@dataclasses.dataclass
class Range:
    sim_port: int
    tap_port: int
    log_path: Path
    _procs: list[subprocess.Popen] = dataclasses.field(default_factory=list)

    def run_module(self, module: str, *args: str, timeout: float = 180.0):
        """Run a repo module to completion against the tap."""
        return subprocess.run(
            [sys.executable, "-m", module, *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def hmi_poll(self, cycles: int = 10, interval: float = 0.05):
        return self.run_module(
            "tools.hmi_poll",
            "--port",
            str(self.tap_port),
            "--source-ip",
            HMI_SOURCE_IP,
            "--interval",
            str(interval),
            "--cycles",
            str(cycles),
        )

    def records(self) -> list[dict]:
        from sensor.detect import load_records

        # The tap flushes per record, but the writing process is separate;
        # give the last few in-flight frames a moment to land.
        time.sleep(0.5)
        return load_records(self.log_path)


@contextlib.contextmanager
def running_range(tmp_path: Path, level: float = 55.0, speed: float = 600.0):
    sim_port = free_port()
    tap_port = free_port()
    log_path = tmp_path / "modbus.log"

    sim = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "process_sim.server",
            "--port",
            str(sim_port),
            "--speed",
            str(speed),
            "--tick",
            "1",
            "--level",
            str(level),
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_port("127.0.0.1", sim_port)

        tap = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "sensor.tap",
                "--listen-port",
                str(tap_port),
                "--upstream-port",
                str(sim_port),
                "--log",
                str(log_path),
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_for_port("127.0.0.1", tap_port)
            yield Range(sim_port=sim_port, tap_port=tap_port, log_path=log_path)
        finally:
            tap.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                tap.wait(timeout=5)
    finally:
        sim.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            sim.wait(timeout=5)
