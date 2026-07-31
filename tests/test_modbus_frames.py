"""Tests for Modbus TCP frame parsing.

TCP gives no message boundaries, so the framing tests matter more than the
field-decoding ones: frames arrive split across segments, coalesced
together, and occasionally malformed.
"""

from __future__ import annotations

from sensor.modbus_frames import (
    ModbusFrame,
    extract_frames,
    parse_frame,
)


def build_frame(tid: int, unit: int, func: int, payload: bytes) -> bytes:
    pdu = bytes([func]) + payload
    length = len(pdu) + 1  # +1 for the unit id byte
    return tid.to_bytes(2, "big") + b"\x00\x00" + length.to_bytes(2, "big") + bytes([unit]) + pdu


def read_request(tid=1, unit=0, func=3, address=0, quantity=1) -> bytes:
    return build_frame(tid, unit, func, address.to_bytes(2, "big") + quantity.to_bytes(2, "big"))


def exception_response(tid=1, unit=0, func=3, code=2) -> bytes:
    return build_frame(tid, unit, func | 0x80, bytes([code]))


def test_parse_read_request():
    frame = parse_frame(read_request(tid=7, unit=2, func=3, address=10, quantity=4), True)
    assert frame == ModbusFrame(
        tid=7,
        unit=2,
        func=3,
        is_request=True,
        raw_len=12,
        address=10,
        quantity=4,
    )
    assert frame.func_name == "READ_HOLDING_REGISTERS"
    assert frame.is_write is False
    assert frame.last_address == 13


def test_parse_write_single_coil_request():
    frame = parse_frame(read_request(func=5, address=3, quantity=0xFF00), True)
    assert frame.func_name == "WRITE_SINGLE_COIL"
    assert frame.is_write is True
    assert frame.address == 3
    assert frame.quantity == 1
    assert frame.last_address == 3


def test_parse_write_multiple_registers_request():
    frame = parse_frame(read_request(func=16, address=100, quantity=5), True)
    assert frame.is_write is True
    assert frame.address == 100
    assert frame.last_address == 104


def test_parse_exception_response():
    frame = parse_frame(exception_response(func=3, code=2), False)
    assert frame.func == 3
    assert frame.exception == 2
    assert frame.exception_name == "ILLEGAL_DATA_ADDRESS"


def test_unknown_function_still_parses():
    frame = parse_frame(build_frame(1, 0, 99, b"\x00\x00"), True)
    assert frame.func == 99
    assert frame.func_name == "UNKNOWN_99"
    assert frame.is_write is False


def test_truncated_frame_returns_none():
    assert parse_frame(b"\x00\x01\x00\x00", True) is None


def test_extract_single_frame():
    buffer = bytearray(read_request(tid=1))
    frames = extract_frames(buffer, True)
    assert len(frames) == 1
    assert frames[0].tid == 1
    assert not buffer


def test_extract_multiple_coalesced_frames():
    buffer = bytearray(read_request(tid=1) + read_request(tid=2) + read_request(tid=3))
    frames = extract_frames(buffer, True)
    assert [f.tid for f in frames] == [1, 2, 3]
    assert not buffer


def test_partial_frame_is_retained_for_next_read():
    whole = read_request(tid=9, address=42)
    buffer = bytearray(whole[:5])
    assert extract_frames(buffer, True) == []
    assert len(buffer) == 5

    buffer.extend(whole[5:])
    frames = extract_frames(buffer, True)
    assert len(frames) == 1
    assert frames[0].tid == 9
    assert frames[0].address == 42
    assert not buffer


def test_frame_split_byte_by_byte():
    """The pathological case: one byte per TCP segment."""
    whole = read_request(tid=4, address=7, quantity=2)
    buffer = bytearray()
    collected = []
    for byte in whole:
        buffer.append(byte)
        collected.extend(extract_frames(buffer, True))
    assert len(collected) == 1
    assert collected[0].tid == 4
    assert collected[0].address == 7


def test_frame_and_a_half():
    whole = read_request(tid=1)
    second = read_request(tid=2)
    buffer = bytearray(whole + second[:4])
    frames = extract_frames(buffer, True)
    assert [f.tid for f in frames] == [1]
    assert len(buffer) == 4


def test_absurd_length_field_resets_buffer():
    """A desynchronised stream must not be guessed at."""
    bogus = b"\x00\x01\x00\x00\xff\xff\x00\x03"
    buffer = bytearray(bogus)
    assert extract_frames(buffer, True) == []
    assert not buffer


def test_zero_length_field_resets_buffer():
    bogus = b"\x00\x01\x00\x00\x00\x00\x00\x03"
    buffer = bytearray(bogus)
    assert extract_frames(buffer, True) == []
    assert not buffer


def test_read_response_carries_no_address():
    """Read responses have only a byte count; the tap correlates the
    address from the request by transaction id."""
    response = build_frame(1, 0, 3, b"\x02\x00\x2a")
    frame = parse_frame(response, False)
    assert frame.address is None
    assert frame.quantity is None


def test_write_response_echoes_address():
    response = build_frame(1, 0, 6, (5).to_bytes(2, "big") + (99).to_bytes(2, "big"))
    frame = parse_frame(response, False)
    assert frame.address == 5
    assert frame.is_write is True
