"""Adapter for real Zeek output (M4's router container).

sensor/tap.py's synthetic log was deliberately field-compatible with
Zeek's own naming — see its docstring — so that sensor/detect.py's rules
would port to a real Zeek deployment unchanged. This module is where
that promise gets kept: it joins Zeek's built-in `modbus.log` (ts, uid,
id.*, tid, unit, func, pdu_type, exception — see
base/protocols/modbus/main.zeek) with the custom `modbus_detailed.log`
(address, quantity, values — router/local.zeek) and normalizes the
handful of real shape differences into exactly what tap.py already
produces, so `Detector.analyze()` itself needed no changes at all:

- pdu_type: Zeek's analyzer emits "REQ"/"RESP"; tap.py chose lowercase
  "request"/"response". Translated here, not in detect.py, so tap.py's
  already-tested output is untouched.
- func_code: Zeek's Info record only carries the function *name*
  (func), not its numeric code — detect.py's view-manipulation rule
  needs the code. Recovered via sensor.modbus_frames.FUNCTION_NAMES,
  the same table tap.py itself is built from, so the mapping can't
  drift between the two paths.
- is_write: not a field Zeek's analyzer produces at all; derived the
  same way tap.py derives it, from function name membership.
- values: modbus_detailed.log logs it as a space-joined string (a
  single Zeek log column can't be "vector of bool for coils, vector of
  count for registers" — see router/local.zeek) rather than a JSON
  list. Split and retyped by function code here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sensor.modbus_frames import FUNCTION_NAMES, WRITE_FUNCTIONS

_FUNC_CODE_BY_NAME = {name: code for code, name in FUNCTION_NAMES.items()}
_WRITE_FUNC_NAMES = frozenset(FUNCTION_NAMES[code] for code in WRITE_FUNCTIONS)
_PDU_TYPE_MAP = {"REQ": "request", "RESP": "response"}
_COIL_FUNC_CODES = frozenset({1, 2})  # coils / discrete inputs -> bool


def _load_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _parse_values(raw: str, func_code: int | None) -> list[bool] | list[int]:
    parts = raw.split()
    if func_code in _COIL_FUNC_CODES:
        return [p == "1" for p in parts]
    return [int(p) for p in parts]


def load_records(modbus_log: Path | str, modbus_detailed_log: Path | str) -> list[dict]:
    """Join and normalize Zeek's two logs into tap.py's record shape.

    modbus_detailed.log only has rows for the function types
    router/local.zeek has handlers for (the common read/write ones) —
    a left join from modbus.log means messages outside that set (e.g.
    exceptions, diagnostics) still come through with everything
    modbus.log itself carries, just without address/quantity/values.
    """
    detailed_by_key: dict[tuple, dict] = {}
    for rec in _load_jsonl(Path(modbus_detailed_log)):
        key = (rec.get("uid"), rec.get("tid"), rec.get("pdu_type"))
        detailed_by_key[key] = rec

    out: list[dict[str, Any]] = []
    for rec in _load_jsonl(Path(modbus_log)):
        key = (rec.get("uid"), rec.get("tid"), rec.get("pdu_type"))
        detail = detailed_by_key.get(key, {})
        func = rec.get("func")
        func_code = _FUNC_CODE_BY_NAME.get(func)

        merged: dict[str, Any] = {
            "ts": rec.get("ts"),
            "uid": rec.get("uid"),
            "id.orig_h": rec.get("id.orig_h"),
            "id.orig_p": rec.get("id.orig_p"),
            "id.resp_h": rec.get("id.resp_h"),
            "id.resp_p": rec.get("id.resp_p"),
            "tid": rec.get("tid"),
            "unit": rec.get("unit"),
            "func": func,
            "func_code": func_code,
            "pdu_type": _PDU_TYPE_MAP.get(rec.get("pdu_type"), rec.get("pdu_type")),
            "is_write": func in _WRITE_FUNC_NAMES,
        }
        if rec.get("exception"):
            merged["exception"] = rec["exception"]
        if "address" in detail:
            merged["address"] = detail["address"]
        if "quantity" in detail:
            merged["quantity"] = detail["quantity"]
        if detail.get("values"):
            merged["values"] = _parse_values(detail["values"], func_code)

        out.append(merged)
    return out
