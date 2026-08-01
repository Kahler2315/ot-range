"""Detection engine for Modbus traffic.

The core primitive is a **baseline allowlist**: in a real plant the set of
`(source, unit id, function code, register range)` tuples seen on the wire
is small, stable, and knowable. Industrial networks are deterministic in a
way IT networks never are — the HMI polls the same points on the same
interval forever. So rather than enumerating bad behavior, enumerate the
good and alert on everything else.

That one mechanism covers most of the scenario library. Layered on top are
behavioral rules that fire even when an attacker operates from an
allowlisted source: point enumeration, unit-id sweeps, and exception-rate
spikes.

Consumes the JSON records written by sensor/tap.py (Zeek `modbus.log`
field names), so the same rules port to a real Zeek deployment at M4.
"""

from __future__ import annotations

import dataclasses
import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

DEFAULT_BASELINE_PATH = Path(__file__).resolve().parent / "baseline.yml"

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclasses.dataclass(frozen=True)
class Alert:
    rule_id: str
    severity: str
    scenario: str
    technique: str
    message: str
    source: str
    evidence: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "scenario": self.scenario,
            "technique": self.technique,
            "message": self.message,
            "source": self.source,
            "evidence": self.evidence,
        }


@dataclasses.dataclass(frozen=True)
class AllowedTuple:
    """One permitted (source, unit, functions, address range) combination."""

    source: str
    unit: int
    functions: frozenset[str]
    address_min: int
    address_max: int

    def permits(self, record: dict) -> bool:
        if record.get("id.orig_h") != self.source:
            return False
        if record.get("unit") != self.unit:
            return False
        if record.get("func") not in self.functions:
            return False
        address = record.get("address")
        if address is None:
            return True
        last = address + max(1, record.get("quantity", 1)) - 1
        return self.address_min <= address and last <= self.address_max


@dataclasses.dataclass
class Thresholds:
    point_enumeration_addresses: int = 12
    unit_id_sweep_count: int = 3
    exception_rate: float = 0.2
    exception_min_responses: int = 10
    # S05: LSHH_101 physically trips at 98% ground truth. Any LT_101
    # reading below this while LSHH is tripped is a physical
    # impossibility, not measurement noise — kept well clear of 98 so
    # this doesn't false-positive on legitimate transient readings.
    view_manipulation_max_pct: float = 90.0


@dataclasses.dataclass
class Baseline:
    allowed: list[AllowedTuple]
    thresholds: Thresholds

    @classmethod
    def load(cls, path: Path | str = DEFAULT_BASELINE_PATH) -> Baseline:
        data = yaml.safe_load(Path(path).read_text()) or {}
        allowed = [
            AllowedTuple(
                source=str(entry["source"]),
                unit=int(entry["unit"]),
                functions=frozenset(entry["functions"]),
                address_min=int(entry["address_min"]),
                address_max=int(entry["address_max"]),
            )
            for entry in data.get("allowed", []) or []
        ]
        thresholds = Thresholds(**(data.get("thresholds") or {}))
        return cls(allowed=allowed, thresholds=thresholds)

    def permits(self, record: dict) -> bool:
        return any(rule.permits(record) for rule in self.allowed)

    def known_source(self, source: str) -> bool:
        return any(rule.source == source for rule in self.allowed)


def load_records(path: Path | str) -> list[dict]:
    """Read tap output. Malformed lines are skipped, not fatal."""
    records = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def learn_baseline(records: Iterable[dict], thresholds: Thresholds | None = None) -> Baseline:
    """Derive an allowlist from a known-clean run.

    Collapses observed traffic into one tuple per (source, unit), spanning
    the function codes and address range actually used.
    """
    seen: dict[tuple[str, int], dict[str, Any]] = {}
    for record in records:
        if record.get("pdu_type") != "request":
            continue
        source = record.get("id.orig_h")
        unit = record.get("unit")
        func = record.get("func")
        if source is None or unit is None or func is None:
            continue

        key = (source, unit)
        entry = seen.setdefault(key, {"functions": set(), "address_min": None, "address_max": None})
        entry["functions"].add(func)

        address = record.get("address")
        if address is not None:
            last = address + max(1, record.get("quantity", 1)) - 1
            lo = entry["address_min"]
            hi = entry["address_max"]
            entry["address_min"] = address if lo is None else min(lo, address)
            entry["address_max"] = last if hi is None else max(hi, last)

    allowed = [
        AllowedTuple(
            source=source,
            unit=unit,
            functions=frozenset(entry["functions"]),
            address_min=entry["address_min"] if entry["address_min"] is not None else 0,
            address_max=entry["address_max"] if entry["address_max"] is not None else 0,
        )
        for (source, unit), entry in sorted(seen.items())
    ]
    return Baseline(allowed=allowed, thresholds=thresholds or Thresholds())


def baseline_to_yaml(baseline: Baseline) -> str:
    data = {
        "thresholds": dataclasses.asdict(baseline.thresholds),
        "allowed": [
            {
                "source": rule.source,
                "unit": rule.unit,
                "functions": sorted(rule.functions),
                "address_min": rule.address_min,
                "address_max": rule.address_max,
            }
            for rule in baseline.allowed
        ],
    }
    return yaml.safe_dump(data, sort_keys=False)


class Detector:
    def __init__(self, baseline: Baseline) -> None:
        self.baseline = baseline

    def analyze(self, records: Iterable[dict]) -> list[Alert]:
        records = list(records)
        alerts: list[Alert] = []
        alerts.extend(self._unauthorized_source(records))
        alerts.extend(self._unauthorized_write(records))
        alerts.extend(self._out_of_baseline(records))
        alerts.extend(self._point_enumeration(records))
        alerts.extend(self._unit_id_sweep(records))
        alerts.extend(self._exception_spike(records))
        alerts.extend(self._view_manipulation(records))
        return sorted(alerts, key=lambda a: SEVERITY_ORDER.get(a.severity, 9))

    def _requests(self, records: Iterable[dict]) -> list[dict]:
        return [r for r in records if r.get("pdu_type") == "request"]

    def _unauthorized_source(self, records: Iterable[dict]) -> list[Alert]:
        alerts = []
        counts: dict[str, int] = defaultdict(int)
        for record in self._requests(records):
            source = record.get("id.orig_h", "unknown")
            if not self.baseline.known_source(source):
                counts[source] += 1
        for source, count in sorted(counts.items()):
            alerts.append(
                Alert(
                    rule_id="MODBUS_UNAUTHORIZED_SOURCE",
                    severity="high",
                    scenario="S01",
                    technique="T0846 Remote System Discovery",
                    message=(
                        f"Modbus traffic from {source}, which is not in the baseline "
                        f"allowlist ({count} requests)"
                    ),
                    source=source,
                    evidence={"request_count": count},
                )
            )
        return alerts

    def _unauthorized_write(self, records: Iterable[dict]) -> list[Alert]:
        """Writes are the ones that move equipment — treated separately and
        rated critical, because an unexpected write is a physical event."""
        alerts = []
        offenders: dict[str, list[dict]] = defaultdict(list)
        for record in self._requests(records):
            if not record.get("is_write"):
                continue
            if self.baseline.permits(record):
                continue
            offenders[record.get("id.orig_h", "unknown")].append(record)

        for source, hits in sorted(offenders.items()):
            functions = sorted({h.get("func", "?") for h in hits})
            addresses = sorted({h.get("address") for h in hits if h.get("address") is not None})
            alerts.append(
                Alert(
                    rule_id="MODBUS_UNAUTHORIZED_WRITE",
                    severity="critical",
                    scenario="S03",
                    technique="T0855 Unauthorized Command Message",
                    message=(
                        f"Unauthorised write from {source}: {', '.join(functions)} "
                        f"to address(es) {addresses}"
                    ),
                    source=source,
                    evidence={
                        "functions": functions,
                        "addresses": addresses,
                        "write_count": len(hits),
                    },
                )
            )
        return alerts

    def _out_of_baseline(self, records: Iterable[dict]) -> list[Alert]:
        alerts = []
        offenders: dict[str, list[dict]] = defaultdict(list)
        for record in self._requests(records):
            if record.get("is_write"):
                continue  # already covered, at a higher severity
            if self.baseline.permits(record):
                continue
            if not self.baseline.known_source(record.get("id.orig_h", "unknown")):
                continue  # already covered by the unknown-source rule
            offenders[record.get("id.orig_h", "unknown")].append(record)

        for source, hits in sorted(offenders.items()):
            functions = sorted({h.get("func", "?") for h in hits})
            alerts.append(
                Alert(
                    rule_id="MODBUS_OUT_OF_BASELINE",
                    severity="medium",
                    scenario="S01",
                    technique="T0861 Point & Tag Identification",
                    message=(
                        f"{source} used function/address combinations outside its "
                        f"baseline: {', '.join(functions)} ({len(hits)} requests)"
                    ),
                    source=source,
                    evidence={"functions": functions, "request_count": len(hits)},
                )
            )
        return alerts

    def _point_enumeration(self, records: Iterable[dict]) -> list[Alert]:
        """A source reaching for points it has no business reading is
        mapping the device, not operating it.

        Counts only distinct points *outside* the baseline. A legitimate
        HMI touches a lot of points but always the same ones, so it scores
        zero here no matter how many it polls — which is what keeps this
        rule from firing on normal operation.
        """
        alerts = []
        touched: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for record in self._requests(records):
            address = record.get("address")
            if address is None:
                continue
            if self.baseline.permits(record):
                continue
            touched[record.get("id.orig_h", "unknown")].add((record.get("func", "?"), address))

        limit = self.baseline.thresholds.point_enumeration_addresses
        for source, points in sorted(touched.items()):
            if len(points) > limit:
                alerts.append(
                    Alert(
                        rule_id="MODBUS_POINT_ENUMERATION",
                        severity="high",
                        scenario="S01",
                        technique="T0861 Point & Tag Identification",
                        message=(
                            f"{source} probed {len(points)} distinct function/address "
                            f"combinations outside its baseline (threshold {limit}) — "
                            "consistent with a register sweep"
                        ),
                        source=source,
                        evidence={"distinct_points": len(points), "threshold": limit},
                    )
                )
        return alerts

    def _unit_id_sweep(self, records: Iterable[dict]) -> list[Alert]:
        alerts = []
        units: dict[str, set[int]] = defaultdict(set)
        for record in self._requests(records):
            unit = record.get("unit")
            if unit is not None:
                units[record.get("id.orig_h", "unknown")].add(unit)

        limit = self.baseline.thresholds.unit_id_sweep_count
        for source, seen in sorted(units.items()):
            if len(seen) > limit:
                alerts.append(
                    Alert(
                        rule_id="MODBUS_UNIT_ID_SWEEP",
                        severity="medium",
                        scenario="S01",
                        technique="T0846 Remote System Discovery",
                        message=(
                            f"{source} probed {len(seen)} distinct unit IDs "
                            f"(threshold {limit}) — consistent with device discovery"
                        ),
                        source=source,
                        evidence={"unit_ids": sorted(seen), "threshold": limit},
                    )
                )
        return alerts

    def _exception_spike(self, records: Iterable[dict]) -> list[Alert]:
        """Legitimate masters know the point map and rarely provoke
        exceptions. A scanner asking for addresses that don't exist does."""
        alerts = []
        totals: dict[str, int] = defaultdict(int)
        exceptions: dict[str, int] = defaultdict(int)
        for record in records:
            if record.get("pdu_type") != "response":
                continue
            source = record.get("id.orig_h", "unknown")
            totals[source] += 1
            if record.get("exception"):
                exceptions[source] += 1

        rate_limit = self.baseline.thresholds.exception_rate
        min_responses = self.baseline.thresholds.exception_min_responses
        for source, total in sorted(totals.items()):
            if total < min_responses:
                continue
            rate = exceptions[source] / total
            if rate > rate_limit:
                alerts.append(
                    Alert(
                        rule_id="MODBUS_EXCEPTION_SPIKE",
                        severity="medium",
                        scenario="S01",
                        technique="T0861 Point & Tag Identification",
                        message=(
                            f"{source} provoked {exceptions[source]}/{total} exception "
                            f"responses ({rate:.0%}, threshold {rate_limit:.0%}) — "
                            "consistent with probing addresses that do not exist"
                        ),
                        source=source,
                        evidence={
                            "exceptions": exceptions[source],
                            "responses": total,
                            "rate": round(rate, 3),
                        },
                    )
                )
        return alerts

    def _view_manipulation(self, records: Iterable[dict]) -> list[Alert]:
        """S05: cross-check two independent descriptions of the same
        physical thing. LSHH_101 is a hardwired float switch — nothing
        on the network can make it report tripped unless the tank is
        physically almost full. LT_101 is the analog transmitter's
        report of the same level, over the wire, spoofable. If the float
        says tripped while the transmitter reports a comfortable level,
        they can't both be true, and the float is the one that can't
        lie.

        Deliberately not source- or baseline-scoped like the other
        rules: this doesn't care who asked or whether the read looked
        routine, only whether the two answers the process gave are
        physically possible together. That's what makes it catch a lie
        the allowlist-based rules structurally cannot — a read from a
        legitimate, baselined source, asking for exactly the point it's
        supposed to ask for, just getting back a false answer.
        """
        lshh_tripped = False
        low_level_reads: list[tuple[dict, float]] = []
        limit = self.baseline.thresholds.view_manipulation_max_pct

        for record in records:
            if record.get("pdu_type") != "response" or "values" not in record:
                continue
            values = record["values"]
            if not values:
                continue

            if record.get("func_code") == 2 and record.get("address") == 0:  # LSHH_101
                if values[0]:
                    lshh_tripped = True
            elif record.get("func_code") == 4 and record.get("address") == 0:  # LT_101
                level_pct = values[0] / 100
                if level_pct < limit:
                    low_level_reads.append((record, level_pct))

        if not (lshh_tripped and low_level_reads):
            return []

        record, level_pct = min(low_level_reads, key=lambda pair: pair[1])
        return [
            Alert(
                rule_id="MODBUS_VIEW_MANIPULATION",
                severity="critical",
                scenario="S05",
                technique="T0856 Spoof Reporting Message",
                message=(
                    "Physical contradiction: the hardwired high-high float "
                    f"(LSHH_101) tripped while the analog transmitter (LT_101) "
                    f"reported {level_pct:.1f}% — these cannot both be true. "
                    "LT_101 is being spoofed."
                ),
                source=record.get("id.resp_h", "unknown"),
                evidence={"lt_101_reported_pct": level_pct, "lshh_101_tripped": True},
            )
        ]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", help="Path to the tap's modbus.log")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE_PATH))
    parser.add_argument(
        "--learn",
        action="store_true",
        help="Print a baseline learned from this log instead of analysing it",
    )
    parser.add_argument("--json", action="store_true", help="Emit alerts as JSON")
    args = parser.parse_args()

    records = load_records(args.log)

    if args.learn:
        print(baseline_to_yaml(learn_baseline(records)), end="")
        return 0

    alerts = Detector(Baseline.load(args.baseline)).analyze(records)
    if args.json:
        print(json.dumps([a.to_dict() for a in alerts], indent=2))
    else:
        if not alerts:
            print("no alerts")
        for alert in alerts:
            print(f"[{alert.severity.upper():<8}] {alert.rule_id}  ({alert.scenario})")
            print(f"           {alert.message}")
            print(f"           ATT&CK: {alert.technique}")
    return 1 if alerts else 0


if __name__ == "__main__":
    raise SystemExit(main())
