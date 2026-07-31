"""Loader for plc/modbus-map.yml — the single source of truth for addressing.

Every component that touches Modbus addressing (the simulator, the CLI,
tests, future HMI/detection code) should go through this loader instead of
hardcoding register numbers.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

DEFAULT_MAP_PATH = Path(__file__).resolve().parent.parent / "plc" / "modbus-map.yml"

TABLES = ("coils", "discrete_inputs", "input_registers", "holding_registers")


@dataclasses.dataclass(frozen=True)
class Point:
    table: str
    tag: str
    addr: int  # traditional 1-indexed Modbus address, within its table
    description: str
    unit: str | None = None
    scale: int | None = None
    default: Any = None

    @property
    def index(self) -> int:
        """Zero-indexed offset into the pymodbus datastore for this table."""
        return self.addr - 1

    def encode(self, value: float) -> int:
        """Convert an engineering-unit value to a raw register value."""
        if self.scale is None:
            raise ValueError(f"{self.tag} has no scale; not a scaled register")
        return round(value * self.scale)

    def decode(self, raw: int) -> float:
        """Convert a raw register value to engineering units."""
        if self.scale is None:
            raise ValueError(f"{self.tag} has no scale; not a scaled register")
        return raw / self.scale


class PointMap:
    """Parsed view of plc/modbus-map.yml, indexed by tag name."""

    def __init__(self, path: Path | str = DEFAULT_MAP_PATH) -> None:
        self.path = Path(path)
        data = yaml.safe_load(self.path.read_text())
        self.meta: dict[str, Any] = data.get("meta", {})
        self._points: dict[str, Point] = {}
        self._by_table: dict[str, list[Point]] = {t: [] for t in TABLES}

        for table in TABLES:
            for entry in data.get(table, []) or []:
                point = Point(
                    table=table,
                    tag=entry["tag"],
                    addr=entry["addr"],
                    description=entry.get("description", ""),
                    unit=entry.get("unit"),
                    scale=entry.get("scale"),
                    default=entry.get("default"),
                )
                if point.tag in self._points:
                    raise ValueError(f"duplicate tag in point map: {point.tag}")
                self._points[point.tag] = point
                self._by_table[table].append(point)

    def __getitem__(self, tag: str) -> Point:
        try:
            return self._points[tag]
        except KeyError:
            raise KeyError(f"unknown point tag: {tag!r}") from None

    def __contains__(self, tag: str) -> bool:
        return tag in self._points

    def __iter__(self):
        return iter(self._points.values())

    def table(self, table: str) -> list[Point]:
        return list(self._by_table[table])

    def tags(self) -> list[str]:
        return list(self._points.keys())


def load(path: Path | str = DEFAULT_MAP_PATH) -> PointMap:
    return PointMap(path)
