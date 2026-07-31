#!/usr/bin/env python3
"""Engineering CLI for the Cedar Hollow Pump Station Modbus slave.

Read or write any point by tag name, list the point map, or watch live
values. Talks to whatever Modbus TCP slave is listening at --host:--port —
by default the one `process_sim.server` starts on loopback.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from pymodbus.client import ModbusTcpClient

from common.pointmap import Point, PointMap
from common.pointmap import load as load_pointmap

DEFAULT_HOST = os.environ.get("MODBUS_BIND_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("MODBUS_BIND_PORT", "5502"))

FC_COIL = 1
FC_DISCRETE_INPUT = 2
FC_HOLDING_REGISTER = 3
FC_INPUT_REGISTER = 4


def read_point(client: ModbusTcpClient, p: Point) -> bool | float:
    if p.table == "coils":
        return bool(client.read_coils(p.index, count=1).bits[0])
    if p.table == "discrete_inputs":
        return bool(client.read_discrete_inputs(p.index, count=1).bits[0])
    if p.table == "input_registers":
        raw = client.read_input_registers(p.index, count=1).registers[0]
        return p.decode(raw) if p.scale else raw
    if p.table == "holding_registers":
        raw = client.read_holding_registers(p.index, count=1).registers[0]
        return p.decode(raw) if p.scale else raw
    raise ValueError(f"unknown table for tag {p.tag!r}: {p.table}")


def write_point(client: ModbusTcpClient, p: Point, value: str) -> None:
    if p.table == "coils":
        client.write_coil(p.index, value.strip().lower() in ("1", "true", "on", "yes"))
    elif p.table == "holding_registers":
        raw = p.encode(float(value)) if p.scale else int(value)
        client.write_register(p.index, raw)
    else:
        raise ValueError(f"{p.tag} ({p.table}) is read-only, cannot write")


def format_value(p: Point, value: bool | float) -> str:
    if isinstance(value, bool):
        return str(value)
    unit = f" {p.unit}" if p.unit else ""
    return f"{value:.2f}{unit}"


def cmd_points(pm: PointMap, _args: argparse.Namespace) -> None:
    for table in ("coils", "discrete_inputs", "input_registers", "holding_registers"):
        print(f"# {table}")
        for p in pm.table(table):
            unit = f" [{p.unit}]" if p.unit else ""
            print(f"  {p.tag:<14} addr={p.addr:<6} {p.description}{unit}")


def cmd_read(pm: PointMap, args: argparse.Namespace) -> None:
    p = pm[args.tag]
    with ModbusTcpClient(args.host, port=args.port) as client:
        value = read_point(client, p)
    print(format_value(p, value))


def cmd_write(pm: PointMap, args: argparse.Namespace) -> None:
    p = pm[args.tag]
    with ModbusTcpClient(args.host, port=args.port) as client:
        write_point(client, p, args.value)
        value = read_point(client, p)
    print(f"{p.tag} <- {format_value(p, value)}")


def cmd_dump(pm: PointMap, args: argparse.Namespace) -> None:
    with ModbusTcpClient(args.host, port=args.port) as client:
        for p in pm:
            value = read_point(client, p)
            print(f"{p.tag:<14} {format_value(p, value)}")


def cmd_watch(pm: PointMap, args: argparse.Namespace) -> None:
    tags = args.tags or [
        "LT_101",
        "FT_201",
        "AIT_301",
        "IT_101",
        "P101_RUN",
        "P101_FB",
        "P101_FAULT",
        "LSHH_101",
        "ALARM_HORN",
    ]
    points = [pm[t] for t in tags]
    with ModbusTcpClient(args.host, port=args.port) as client:
        try:
            while True:
                row = " | ".join(
                    f"{p.tag}={format_value(p, read_point(client, p))}" for p in points
                )
                print(row, flush=True)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("points", help="List every point in the point map")

    p_read = sub.add_parser("read", help="Read one point by tag")
    p_read.add_argument("tag")

    p_write = sub.add_parser("write", help="Write one point by tag")
    p_write.add_argument("tag")
    p_write.add_argument("value")

    sub.add_parser("dump", help="Read every point once")

    p_watch = sub.add_parser("watch", help="Live values of key points")
    p_watch.add_argument("tags", nargs="*", help="Tags to watch (default: key process points)")
    p_watch.add_argument("--interval", type=float, default=1.0)

    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    pm = load_pointmap()
    handlers = {
        "points": cmd_points,
        "read": cmd_read,
        "write": cmd_write,
        "dump": cmd_dump,
        "watch": cmd_watch,
    }
    try:
        handlers[args.command](pm, args)
    except (KeyError, ValueError, ConnectionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
