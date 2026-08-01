"""Tests for sensor/tap.py's value decoding.

Framing/parsing itself is tested in test_modbus_frames.py; this covers
turning a read response's raw payload into actual point values.
"""

from __future__ import annotations

from sensor.modbus_frames import ModbusFrame
from sensor.tap import decode_values, frame_to_record


def test_decode_single_discrete_input():
    assert decode_values(2, b"\x01", 1) == [True]
    assert decode_values(2, b"\x00", 1) == [False]


def test_decode_packed_coils_lsb_first():
    # 0b00000101 -> bit0=1, bit1=0, bit2=1
    assert decode_values(1, bytes([0b101]), 3) == [True, False, True]


def test_decode_single_register():
    assert decode_values(4, bytes.fromhex("2134"), 1) == [8500]


def test_decode_multiple_registers():
    assert decode_values(3, bytes.fromhex("00010002"), 2) == [1, 2]


def test_decode_unknown_function_returns_empty():
    assert decode_values(99, b"\x01", 1) == []


def test_frame_to_record_includes_decoded_values_for_read_response():
    """Integration: a response frame correlated with its request ends up
    with a decoded 'values' field in the log record."""
    pending: dict[int, ModbusFrame] = {}
    request = ModbusFrame(tid=1, unit=0, func=4, is_request=True, raw_len=12, address=0, quantity=1)
    response = ModbusFrame(
        tid=1,
        unit=0,
        func=4,
        is_request=False,
        raw_len=7,
        raw_data=bytes.fromhex("1388"),  # 5000 -> 50.00%
    )

    frame_to_record(request, "u", "10.0.0.1", 5000, "10.0.0.2", 5502, pending)
    record = frame_to_record(response, "u", "10.0.0.1", 5000, "10.0.0.2", 5502, pending)

    assert record["values"] == [5000]
    assert record["address"] == 0  # correlated back from the request


def test_frame_to_record_omits_values_when_quantity_unknown():
    """No correlated request, no known quantity — must not crash trying
    to decode, and must not fabricate a values field."""
    pending: dict[int, ModbusFrame] = {}
    response = ModbusFrame(
        tid=99, unit=0, func=4, is_request=False, raw_len=7, raw_data=bytes.fromhex("1388")
    )
    record = frame_to_record(response, "u", "10.0.0.1", 5000, "10.0.0.2", 5502, pending)
    assert "values" not in record
