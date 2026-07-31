#!/usr/bin/env python3
"""Configure a running OpenPLC instance over its own HTTP interface.

Drives OpenPLC's web routes (login, upload + compile a program, register
a Modbus slave device, start the runtime) the same way a browser would —
scripted, so bring-up is reproducible instead of manual clicking. This is
also literally the S06 attack surface: default creds, unauthenticated
program upload, no CSRF token. See docs/openplc-integration.md.

Verified gotcha, not documented anywhere upstream: OpenPLC's compiled
Modbus master resolves device addresses with libmodbus's `inet_addr()`,
which parses dotted-decimal IPv4 only — it does not do DNS resolution.
Handing it a hostname silently becomes 255.255.255.255 (INADDR_NONE),
which then fails to connect with "Network is unreachable". This tool
resolves the hostname itself before submitting the device config, so
docker-compose service names work as input even though OpenPLC itself
can't use them directly.
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
import time
from pathlib import Path

import requests

DEFAULT_USERNAME = "openplc"
DEFAULT_PASSWORD = "openplc"  # nosec B105 -- OpenPLC's actual shipped default, not a secret

_UPLOADED_FILENAME_RE = re.compile(r"value='([0-9]+\.st)' id='prog_file'")


class OpenPLCConfigError(Exception):
    pass


class OpenPLCClient:
    """Thin wrapper over OpenPLC's web routes. Not a general API client —
    just the handful of routes bring-up needs."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    # OpenPLC's actual shipped default credential, not a secret; see docs/openplc-integration.md
    def login(  # nosemgrep: python.lang.security.audit.hardcoded-password-default-argument.hardcoded-password-default-argument
        self, username: str = DEFAULT_USERNAME, password: str = DEFAULT_PASSWORD
    ) -> None:
        resp = self.session.post(
            f"{self.base_url}/login",
            data={"username": username, "password": password},
            allow_redirects=False,
        )
        if resp.status_code != 302:
            raise OpenPLCConfigError(f"login failed: HTTP {resp.status_code}")

    def upload_and_compile(
        self,
        st_path: str | Path,
        name: str,
        description: str = "",
        timeout_s: float = 90.0,
    ) -> None:
        st_path = Path(st_path)
        with st_path.open("rb") as fh:
            resp = self.session.post(
                f"{self.base_url}/upload-program",
                files={"file": (st_path.name, fh, "text/plain")},
            )
        resp.raise_for_status()

        match = _UPLOADED_FILENAME_RE.search(resp.text)
        if not match:
            raise OpenPLCConfigError("could not find uploaded filename in OpenPLC's response")
        filename = match.group(1)

        resp = self.session.post(
            f"{self.base_url}/upload-program-action",
            data={
                "prog_name": name,
                "prog_descr": description,
                "prog_file": filename,
                "epoch_time": str(int(time.time())),
            },
        )
        resp.raise_for_status()

        self.session.get(f"{self.base_url}/compile-program", params={"file": filename})

        deadline = time.time() + timeout_s
        logs = ""
        while time.time() < deadline:
            logs = self.session.get(f"{self.base_url}/compilation-logs").text
            if "Compilation finished successfully" in logs:
                return
            if "compilation failed" in logs.lower():
                raise OpenPLCConfigError(f"compilation failed:\n{logs}")
            time.sleep(1)
        raise TimeoutError(f"compilation did not finish within {timeout_s}s; last log:\n{logs}")

    def add_modbus_slave_device(
        self,
        name: str,
        host: str,
        port: int,
        *,
        slave_id: int = 0,
        di_start: int = 0,
        di_size: int = 0,
        coil_start: int = 0,
        coil_size: int = 0,
        ir_start: int = 0,
        ir_size: int = 0,
        hr_start: int = 0,
        hr_size: int = 0,
        pause_ms: int = 0,
    ) -> None:
        ip = socket.gethostbyname(host)  # see module docstring — OpenPLC needs a literal IP
        resp = self.session.post(
            f"{self.base_url}/add-modbus-device",
            data={
                "device_name": name,
                "device_protocol": "TCP",
                "device_id": str(slave_id),
                "device_ip": ip,
                "device_port": str(port),
                "di_start": str(di_start),
                "di_size": str(di_size),
                "do_start": str(coil_start),
                "do_size": str(coil_size),
                "ai_start": str(ir_start),
                "ai_size": str(ir_size),
                "aor_start": str(hr_start),
                "aor_size": str(hr_size),
                "aow_start": str(hr_start),
                "aow_size": str(hr_size),
                "device_pause": str(pause_ms),
            },
            allow_redirects=False,
        )
        if resp.status_code != 302:
            raise OpenPLCConfigError(
                f"add-modbus-device failed: HTTP {resp.status_code}\n{resp.text[:500]}"
            )

    def start_plc(self) -> None:
        self.session.get(f"{self.base_url}/start_plc")

    def stop_plc(self) -> None:
        self.session.get(f"{self.base_url}/stop_plc")


# OpenPLC's actual shipped default credential, not a secret; see docs/openplc-integration.md
def bring_up_cedar_hollow(  # nosemgrep: python.lang.security.audit.hardcoded-password-default-argument.hardcoded-password-default-argument
    base_url: str,
    st_path: str,
    field_host: str,
    field_port: int,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
) -> None:
    """The Cedar Hollow-specific bring-up: upload the program, register
    process-sim as a slave device with the field map's sizes, start."""
    client = OpenPLCClient(base_url)
    client.login(username, password)
    client.upload_and_compile(st_path, name="Cedar Hollow", description="OT Range control logic")
    client.add_modbus_slave_device(
        "process-sim",
        field_host,
        field_port,
        di_size=4,  # LSHH_101, LSLL_101, P101_FB, P101_FAULT
        coil_size=4,  # P101_RUN, V201_OPEN, CL301_RUN, ALARM_HORN
        ir_size=4,  # LT_101, FT_201, AIT_301, IT_101
        hr_size=2,  # SP_P101_SPD, SP_CL_DOSE
    )
    client.start_plc()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--program", default="plc/logic/cedar_hollow.st", help="ST file to upload and run"
    )
    parser.add_argument("--field-host", default="process-sim")
    parser.add_argument("--field-port", type=int, default=5502)
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument(
        "--wait-for-server",
        type=float,
        default=60.0,
        help="Seconds to wait for the webserver to become reachable before giving up "
        "(bring-up right after `docker compose up` races the container's own startup)",
    )
    return parser.parse_args(argv)


def _wait_for_server(base_url: str, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            requests.get(f"{base_url}/login", timeout=2)
            return
        except requests.exceptions.RequestException as exc:
            last_error = exc
            time.sleep(1)
    raise TimeoutError(f"{base_url} not reachable within {timeout_s}s (last error: {last_error})")


def main() -> int:
    args = parse_args()
    try:
        _wait_for_server(args.base_url, args.wait_for_server)
        bring_up_cedar_hollow(
            args.base_url,
            args.program,
            args.field_host,
            args.field_port,
            args.username,
            args.password,
        )
    except (OpenPLCConfigError, TimeoutError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"OpenPLC at {args.base_url} configured and started with {args.program}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
