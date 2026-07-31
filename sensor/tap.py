"""Inline Modbus TCP tap.

Sits between clients and the Modbus slave, forwards traffic unchanged, and
writes one JSON record per parsed frame to a log whose field names match
Zeek's `modbus.log` / `modbus_detailed.log`. Detection rules written
against this log are portable to a real Zeek deployment.

**Why a proxy rather than a passive sniffer.** Passive capture needs
either root (raw sockets) or a real span/tap. A Docker bridge network also
does MAC learning, so a sniffer attached to one does not see unicast
traffic between other containers at all. The M4 architecture solves this
properly by routing cross-zone traffic through a router container running
Zeek. Until that exists, an inline proxy is the honest way to observe
traffic without pretending a bridge-attached sniffer would work — and
inline protocol gateways are themselves a real OT deployment pattern.

Not a security control: it does not block anything, it only observes.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

from sensor.modbus_frames import ModbusFrame, extract_frames

LOG = logging.getLogger("ot_range.tap")

DEFAULT_LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 5020
DEFAULT_UPSTREAM_HOST = os.environ.get("MODBUS_BIND_HOST", "127.0.0.1")
DEFAULT_UPSTREAM_PORT = int(os.environ.get("MODBUS_BIND_PORT", "5502"))
DEFAULT_LOG_PATH = Path("logs/modbus.log")


class TransactionLog:
    """Writes Zeek-modbus.log-compatible JSON records, one per line."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, record: dict) -> None:
        self._fh.write(json.dumps(record) + "\n")
        self._fh.flush()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._fh.close()


def frame_to_record(
    frame: ModbusFrame,
    uid: str,
    orig_h: str,
    orig_p: int,
    resp_h: str,
    resp_p: int,
    pending: dict[int, ModbusFrame],
) -> dict:
    """Build a log record, correlating read responses back to their request.

    A read response carries only a byte count, so the address it refers to
    comes from the request with the same transaction id.
    """
    address = frame.address
    quantity = frame.quantity

    if frame.is_request:
        pending[frame.tid] = frame
    else:
        request = pending.pop(frame.tid, None)
        if request is not None and address is None:
            address = request.address
            quantity = request.quantity

    record = {
        "ts": round(time.time(), 6),
        "uid": uid,
        "id.orig_h": orig_h,
        "id.orig_p": orig_p,
        "id.resp_h": resp_h,
        "id.resp_p": resp_p,
        "tid": frame.tid,
        "unit": frame.unit,
        "func": frame.func_name,
        "func_code": frame.func,
        "pdu_type": "request" if frame.is_request else "response",
        "is_write": frame.is_write,
    }
    if address is not None:
        record["address"] = address
    if quantity is not None:
        record["quantity"] = quantity
    if frame.exception is not None:
        record["exception"] = frame.exception_name
    return record


class ModbusTap:
    def __init__(
        self,
        listen_host: str,
        listen_port: int,
        upstream_host: str,
        upstream_port: int,
        log_path: Path,
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.txlog = TransactionLog(log_path)
        self._server: asyncio.AbstractServer | None = None

    async def _pump(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        is_request: bool,
        uid: str,
        orig_h: str,
        orig_p: int,
        resp_h: str,
        resp_p: int,
        pending: dict[int, ModbusFrame],
    ) -> None:
        """Forward one direction, parsing and logging as bytes go past."""
        buffer = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()

                buffer.extend(chunk)
                for frame in extract_frames(buffer, is_request):
                    self.txlog.write(
                        frame_to_record(frame, uid, orig_h, orig_p, resp_h, resp_p, pending)
                    )
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def handle_client(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        peer = client_writer.get_extra_info("peername") or ("unknown", 0)
        orig_h, orig_p = str(peer[0]), int(peer[1])
        uid = uuid.uuid4().hex[:17]

        try:
            up_reader, up_writer = await asyncio.open_connection(
                self.upstream_host, self.upstream_port
            )
        except OSError as exc:
            LOG.warning("upstream connect failed: %s", exc)
            client_writer.close()
            return

        pending: dict[int, ModbusFrame] = {}
        LOG.info("session %s from %s:%d", uid, orig_h, orig_p)

        await asyncio.gather(
            self._pump(
                client_reader,
                up_writer,
                is_request=True,
                uid=uid,
                orig_h=orig_h,
                orig_p=orig_p,
                resp_h=self.upstream_host,
                resp_p=self.upstream_port,
                pending=pending,
            ),
            self._pump(
                up_reader,
                client_writer,
                is_request=False,
                uid=uid,
                orig_h=orig_h,
                orig_p=orig_p,
                resp_h=self.upstream_host,
                resp_p=self.upstream_port,
                pending=pending,
            ),
        )

    async def start(self) -> asyncio.AbstractServer:
        self._server = await asyncio.start_server(
            self.handle_client, self.listen_host, self.listen_port
        )
        LOG.info(
            "tap listening on %s:%d -> %s:%d (log: %s)",
            self.listen_host,
            self.listen_port,
            self.upstream_host,
            self.upstream_port,
            self.txlog.path,
        )
        return self._server

    async def serve_forever(self) -> None:
        server = self._server or await self.start()
        async with server:
            await server.serve_forever()

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
        self.txlog.close()


async def main_async(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    tap = ModbusTap(
        args.listen_host,
        args.listen_port,
        args.upstream_host,
        args.upstream_port,
        Path(args.log),
    )
    try:
        await tap.serve_forever()
    finally:
        tap.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", default=DEFAULT_LISTEN_HOST)
    parser.add_argument("--listen-port", type=int, default=DEFAULT_LISTEN_PORT)
    parser.add_argument("--upstream-host", default=DEFAULT_UPSTREAM_HOST)
    parser.add_argument("--upstream-port", type=int, default=DEFAULT_UPSTREAM_PORT)
    parser.add_argument("--log", default=str(DEFAULT_LOG_PATH))
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
