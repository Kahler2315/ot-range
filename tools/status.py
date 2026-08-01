#!/usr/bin/env python3
"""Health checklist for whichever stack is currently running.

Checks both the loopback stack (`make sim`/`tap`/`hmi`) and the docker
stack (`make up`) and reports what's actually reachable, in plain
language, instead of leaving you to guess from silent connection
failures. Safe to run any time — read-only, no side effects.

Usage: tools/status.py
"""

from __future__ import annotations

import socket
import subprocess
import sys

import requests

CHECK = "\N{CHECK MARK}"
CROSS = "\N{BALLOT X}"


def tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        resp = requests.get(url, timeout=timeout)
        return resp.status_code < 500
    except requests.RequestException:
        return False


def docker_container_status(name: str) -> str | None:
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}",
            name,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def line(ok: bool, label: str, detail: str = "") -> None:
    mark = CHECK if ok else CROSS
    suffix = f" — {detail}" if detail else ""
    print(f"  {mark} {label}{suffix}")


def check_loopback() -> bool:
    print("Loopback stack (make sim / tap / hmi):")
    sim_up = tcp_open("127.0.0.1", 5502)
    line(sim_up, "process-sim", "127.0.0.1:5502" if sim_up else "not listening — try `make sim`")
    tap_up = tcp_open("127.0.0.1", 5020)
    line(tap_up, "sensor tap", "127.0.0.1:5020" if tap_up else "not listening — try `make tap`")
    return sim_up


def check_docker() -> bool:
    print("\nDocker stack (make up):")
    containers = [
        ("ot-range-process-sim-1", None),
        ("ot-range-openplc-1", 8080),
        ("ot-range-hmi-1", 8090),
        ("ot-range-historian-1", None),
        ("ot-range-postgres-1", None),
        ("ot-range-grafana-1", 3000),
        ("ot-range-router-1", None),
    ]
    any_present = False
    all_healthy = True
    for name, _port in containers:
        status = docker_container_status(name)
        if status is None:
            line(False, name, "not created — try `make up`")
            all_healthy = False
            continue
        any_present = True
        state, _, health = status.partition("|")
        ok = state == "running" and health in ("", "healthy")
        detail = state if not health else f"{state}, {health}"
        line(ok, name, detail)
        all_healthy = all_healthy and ok

    if not any_present:
        print("  (nothing found — run `make up` first)")
        return False

    print("\n  Ports:")
    modbus_openplc = tcp_open("127.0.0.1", 502)
    line(modbus_openplc, "Modbus (OpenPLC)", "127.0.0.1:502")
    modbus_sim = tcp_open("127.0.0.1", 5502)
    line(modbus_sim, "Modbus (process-sim)", "127.0.0.1:5502")
    web_openplc = http_ok("http://127.0.0.1:8080")
    line(web_openplc, "OpenPLC web UI", "http://localhost:8080 (openplc/openplc)")
    web_hmi = http_ok("http://127.0.0.1:8090")
    line(web_hmi, "HMI", "http://localhost:8090")
    web_grafana = http_ok("http://127.0.0.1:3000")
    line(web_grafana, "Grafana", "http://localhost:3000 (admin/admin)")

    return all_healthy and modbus_openplc and modbus_sim


def main() -> int:
    print("=" * 68)
    print(" OT RANGE — status")
    print("=" * 68)
    loopback_ok = check_loopback()
    docker_ok = check_docker()

    print()
    if loopback_ok or docker_ok:
        print("Something's up and reachable — see above for anything marked with an X.")
        return 0
    print("Nothing is running. Start with `make sim` (loopback) or `make up` (docker).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
