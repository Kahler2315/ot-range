"""Tests for the detection rules, against synthetic traffic records.

Fast and deterministic — no sockets. The end-to-end proof that real
attacks trip these rules lives in tests/test_attack_detection.py.
"""

from __future__ import annotations

import pytest

from sensor.detect import (
    AllowedTuple,
    Baseline,
    Detector,
    Thresholds,
    learn_baseline,
)

HMI = "127.0.0.1"
ATTACKER = "127.0.0.2"


def record(
    source=HMI,
    func="READ_INPUT_REGISTERS",
    address=0,
    quantity=1,
    unit=0,
    pdu_type="request",
    is_write=False,
    exception=None,
    func_code=None,
    values=None,
):
    rec = {
        "ts": 1.0,
        "uid": "abc",
        "id.orig_h": source,
        "id.orig_p": 5000,
        "id.resp_h": "127.0.0.1",
        "id.resp_p": 5502,
        "tid": 1,
        "unit": unit,
        "func": func,
        "pdu_type": pdu_type,
        "is_write": is_write,
    }
    if address is not None:
        rec["address"] = address
        rec["quantity"] = quantity
    if exception is not None:
        rec["exception"] = exception
    if func_code is not None:
        rec["func_code"] = func_code
    if values is not None:
        rec["values"] = values
    return rec


@pytest.fixture
def baseline():
    return Baseline(
        allowed=[
            AllowedTuple(
                source=HMI,
                unit=0,
                functions=frozenset(
                    {
                        "READ_COILS",
                        "READ_DISCRETE_INPUTS",
                        "READ_INPUT_REGISTERS",
                        "READ_HOLDING_REGISTERS",
                    }
                ),
                address_min=0,
                address_max=4,
            )
        ],
        thresholds=Thresholds(),
    )


def rule_ids(alerts):
    return {a.rule_id for a in alerts}


def test_clean_hmi_traffic_produces_no_alerts(baseline):
    records = []
    for _ in range(50):
        for addr in range(5):
            records.append(record(address=addr, func="READ_COILS"))
            records.append(record(address=addr, func="READ_HOLDING_REGISTERS"))
    assert Detector(baseline).analyze(records) == []


def test_many_baseline_points_do_not_trip_enumeration(baseline):
    """The legitimate HMI polls a lot of points — but always the same
    ones, so the enumeration rule must stay quiet."""
    records = []
    for func in (
        "READ_COILS",
        "READ_DISCRETE_INPUTS",
        "READ_INPUT_REGISTERS",
        "READ_HOLDING_REGISTERS",
    ):
        for addr in range(5):
            records.append(record(func=func, address=addr))
    alerts = Detector(baseline).analyze(records)
    assert "MODBUS_POINT_ENUMERATION" not in rule_ids(alerts)


def test_unknown_source_alerts(baseline):
    alerts = Detector(baseline).analyze([record(source=ATTACKER)])
    assert "MODBUS_UNAUTHORIZED_SOURCE" in rule_ids(alerts)


def test_unauthorized_write_is_critical(baseline):
    alerts = Detector(baseline).analyze(
        [record(source=ATTACKER, func="WRITE_SINGLE_COIL", address=0, is_write=True)]
    )
    write_alerts = [a for a in alerts if a.rule_id == "MODBUS_UNAUTHORIZED_WRITE"]
    assert len(write_alerts) == 1
    assert write_alerts[0].severity == "critical"
    assert write_alerts[0].technique.startswith("T0855")


def test_write_from_allowlisted_read_only_source_still_alerts(baseline):
    """The HMI is allowed to read. It is not allowed to write — a write
    from the HMI's own address is still unauthorised."""
    alerts = Detector(baseline).analyze(
        [record(source=HMI, func="WRITE_SINGLE_COIL", address=0, is_write=True)]
    )
    assert "MODBUS_UNAUTHORIZED_WRITE" in rule_ids(alerts)


def test_read_outside_baseline_address_range_alerts(baseline):
    alerts = Detector(baseline).analyze(
        [record(source=HMI, func="READ_HOLDING_REGISTERS", address=99)]
    )
    assert "MODBUS_OUT_OF_BASELINE" in rule_ids(alerts)


def test_read_spanning_past_baseline_range_alerts(baseline):
    """A single request for addresses 0..19 reaches outside 0..4."""
    alerts = Detector(baseline).analyze(
        [record(source=HMI, func="READ_HOLDING_REGISTERS", address=0, quantity=20)]
    )
    assert "MODBUS_OUT_OF_BASELINE" in rule_ids(alerts)


def test_point_enumeration_alerts_on_sweep(baseline):
    records = [
        record(source=ATTACKER, func="READ_HOLDING_REGISTERS", address=addr) for addr in range(40)
    ]
    alerts = Detector(baseline).analyze(records)
    assert "MODBUS_POINT_ENUMERATION" in rule_ids(alerts)


def test_unit_id_sweep_alerts(baseline):
    records = [record(source=ATTACKER, unit=unit) for unit in range(8)]
    alerts = Detector(baseline).analyze(records)
    assert "MODBUS_UNIT_ID_SWEEP" in rule_ids(alerts)


def test_single_unit_does_not_trip_sweep(baseline):
    records = [record(source=ATTACKER, unit=0) for _ in range(50)]
    alerts = Detector(baseline).analyze(records)
    assert "MODBUS_UNIT_ID_SWEEP" not in rule_ids(alerts)


def test_exception_spike_alerts(baseline):
    records = [
        record(source=ATTACKER, pdu_type="response", exception="ILLEGAL_DATA_ADDRESS")
        for _ in range(15)
    ]
    records += [record(source=ATTACKER, pdu_type="response") for _ in range(5)]
    alerts = Detector(baseline).analyze(records)
    assert "MODBUS_EXCEPTION_SPIKE" in rule_ids(alerts)


def test_few_exceptions_do_not_trip_spike(baseline):
    records = [record(pdu_type="response") for _ in range(100)]
    records.append(record(pdu_type="response", exception="ILLEGAL_DATA_ADDRESS"))
    alerts = Detector(baseline).analyze(records)
    assert "MODBUS_EXCEPTION_SPIKE" not in rule_ids(alerts)


def test_exception_spike_ignores_tiny_samples(baseline):
    """Two exceptions out of three responses is a 67% rate but far too
    small a sample to alert on."""
    records = [
        record(source=ATTACKER, pdu_type="response", exception="ILLEGAL_DATA_ADDRESS"),
        record(source=ATTACKER, pdu_type="response", exception="ILLEGAL_DATA_ADDRESS"),
        record(source=ATTACKER, pdu_type="response"),
    ]
    alerts = Detector(baseline).analyze(records)
    assert "MODBUS_EXCEPTION_SPIKE" not in rule_ids(alerts)


def test_alerts_sorted_most_severe_first(baseline):
    records = [record(source=ATTACKER, unit=u) for u in range(8)]
    records.append(record(source=ATTACKER, func="WRITE_SINGLE_COIL", address=0, is_write=True))
    alerts = Detector(baseline).analyze(records)
    assert len(alerts) > 1
    assert alerts[0].severity == "critical"


def test_learn_baseline_captures_observed_traffic():
    records = [
        record(func="READ_COILS", address=0),
        record(func="READ_COILS", address=4),
        record(func="READ_INPUT_REGISTERS", address=2),
    ]
    learned = learn_baseline(records)
    assert len(learned.allowed) == 1
    rule = learned.allowed[0]
    assert rule.source == HMI
    assert rule.functions == {"READ_COILS", "READ_INPUT_REGISTERS"}
    assert rule.address_min == 0
    assert rule.address_max == 4


def test_learned_baseline_accepts_the_traffic_it_learned_from():
    records = []
    for func in ("READ_COILS", "READ_HOLDING_REGISTERS"):
        for addr in range(5):
            records.append(record(func=func, address=addr))
    learned = learn_baseline(records)
    assert Detector(learned).analyze(records) == []


def test_learn_baseline_ignores_responses():
    records = [
        record(func="READ_COILS", address=0),
        record(func="READ_COILS", address=50, pdu_type="response"),
    ]
    learned = learn_baseline(records)
    assert learned.allowed[0].address_max == 0


def test_shipped_baseline_loads_and_is_usable():
    from sensor.detect import DEFAULT_BASELINE_PATH

    loaded = Baseline.load(DEFAULT_BASELINE_PATH)
    assert loaded.allowed, "shipped baseline must define at least one allowed tuple"
    assert loaded.known_source(HMI)


def lshh_response(tripped, source=HMI):
    return record(
        source=source,
        func="READ_DISCRETE_INPUTS",
        func_code=2,
        address=0,
        pdu_type="response",
        values=[tripped],
    )


def lt101_response(pct, source=HMI):
    return record(
        source=source,
        func="READ_INPUT_REGISTERS",
        func_code=4,
        address=0,
        pdu_type="response",
        values=[round(pct * 100)],
    )


def test_view_manipulation_alerts_when_float_contradicts_transmitter(baseline):
    alerts = Detector(baseline).analyze([lshh_response(True), lt101_response(50.0)])
    view_alerts = [a for a in alerts if a.rule_id == "MODBUS_VIEW_MANIPULATION"]
    assert len(view_alerts) == 1
    assert view_alerts[0].severity == "critical"
    assert view_alerts[0].technique.startswith("T0856")
    assert view_alerts[0].evidence["lt_101_reported_pct"] == 50.0


def test_view_manipulation_silent_when_readings_agree(baseline):
    """LSHH tripped and LT_101 genuinely reporting a high level — no
    contradiction, no alert. A real overflow must not trip this rule."""
    alerts = Detector(baseline).analyze([lshh_response(True), lt101_response(98.5)])
    assert "MODBUS_VIEW_MANIPULATION" not in rule_ids(alerts)


def test_view_manipulation_silent_when_float_never_trips(baseline):
    """A low LT_101 reading alone is completely normal — most of the
    time, the tank just isn't full. Only the pairing is suspicious."""
    alerts = Detector(baseline).analyze([lshh_response(False), lt101_response(50.0)])
    assert "MODBUS_VIEW_MANIPULATION" not in rule_ids(alerts)


def test_view_manipulation_ignores_requests():
    """Requests never carry values (only responses do) — must not crash
    or false-positive on a log made up entirely of requests."""
    baseline_obj = Baseline(allowed=[], thresholds=Thresholds())
    alerts = Detector(baseline_obj).analyze(
        [record(func="READ_DISCRETE_INPUTS", address=0), record(func="READ_INPUT_REGISTERS")]
    )
    assert "MODBUS_VIEW_MANIPULATION" not in rule_ids(alerts)


def test_view_manipulation_fires_even_from_an_allowlisted_source(baseline):
    """The whole point: unlike every other rule here, this one isn't
    about who asked or whether the request looked routine — LT_101's
    own value is physically inconsistent with LSHH_101's, regardless of
    source. A read from the baselined HMI address must still trip it."""
    alerts = Detector(baseline).analyze(
        [lshh_response(True, source=HMI), lt101_response(50.0, source=HMI)]
    )
    assert "MODBUS_VIEW_MANIPULATION" in rule_ids(alerts)


def test_view_manipulation_uses_the_lowest_contradicting_reading(baseline):
    """Report the most damning evidence when there are several."""
    alerts = Detector(baseline).analyze(
        [lshh_response(True), lt101_response(80.0), lt101_response(20.0), lt101_response(60.0)]
    )
    view_alerts = [a for a in alerts if a.rule_id == "MODBUS_VIEW_MANIPULATION"]
    assert view_alerts[0].evidence["lt_101_reported_pct"] == 20.0
