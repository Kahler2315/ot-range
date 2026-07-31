"""Modbus TCP frame parsing for the sensor.

Pure parsing, no I/O — the transport layer lives in sensor/tap.py and the
detection logic in sensor/detect.py. Field names follow Zeek's
`modbus.log` / `modbus_detailed.log` so rules written against this are
portable to a real Zeek deployment at M4.

Modbus TCP framing: a 7-byte MBAP header (transaction id, protocol id,
length, unit id) followed by the PDU (function code + data). TCP gives no
message boundaries, so frames must be extracted using the length field —
they arrive split across segments and coalesced together.
"""

from __future__ import annotations

import dataclasses

MBAP_HEADER_LEN = 7
MIN_FRAME_LEN = MBAP_HEADER_LEN + 1  # header + function code
MAX_FRAME_LEN = 260  # Modbus TCP maximum ADU
EXCEPTION_MASK = 0x80

FUNCTION_NAMES = {
    1: "READ_COILS",
    2: "READ_DISCRETE_INPUTS",
    3: "READ_HOLDING_REGISTERS",
    4: "READ_INPUT_REGISTERS",
    5: "WRITE_SINGLE_COIL",
    6: "WRITE_SINGLE_REGISTER",
    15: "WRITE_MULTIPLE_COILS",
    16: "WRITE_MULTIPLE_REGISTERS",
    23: "READ_WRITE_MULTIPLE_REGISTERS",
}

# Function codes that change state. The single most useful split in OT
# detection: reads are routine, writes are the ones that move equipment.
WRITE_FUNCTIONS = frozenset({5, 6, 15, 16, 23})

EXCEPTION_NAMES = {
    1: "ILLEGAL_FUNCTION",
    2: "ILLEGAL_DATA_ADDRESS",
    3: "ILLEGAL_DATA_VALUE",
    4: "SLAVE_DEVICE_FAILURE",
    6: "SLAVE_DEVICE_BUSY",
}


@dataclasses.dataclass(frozen=True)
class ModbusFrame:
    """One parsed Modbus TCP ADU."""

    tid: int
    unit: int
    func: int
    is_request: bool
    raw_len: int
    address: int | None = None
    quantity: int | None = None
    exception: int | None = None

    @property
    def func_name(self) -> str:
        return FUNCTION_NAMES.get(self.func, f"UNKNOWN_{self.func}")

    @property
    def exception_name(self) -> str | None:
        if self.exception is None:
            return None
        return EXCEPTION_NAMES.get(self.exception, f"UNKNOWN_{self.exception}")

    @property
    def is_write(self) -> bool:
        return self.func in WRITE_FUNCTIONS

    @property
    def last_address(self) -> int | None:
        """Highest address touched, for range checks against an allowlist."""
        if self.address is None:
            return None
        span = self.quantity if self.quantity else 1
        return self.address + span - 1


def extract_frames(buffer: bytearray, is_request: bool) -> list[ModbusFrame]:
    """Pull every complete frame out of `buffer`, consuming what it parses.

    Mutates `buffer` in place, leaving any partial trailing frame for the
    next call. Returns the frames parsed this round.
    """
    frames: list[ModbusFrame] = []

    while len(buffer) >= MBAP_HEADER_LEN:
        length = int.from_bytes(buffer[4:6], "big")

        # length counts the unit id byte plus the PDU. A sane frame is at
        # least 2 (unit + function code) and never exceeds the ADU max.
        if length < 2 or length > MAX_FRAME_LEN:
            buffer.clear()  # desynchronised; do not guess
            break

        total = 6 + length
        if len(buffer) < total:
            break  # partial frame, wait for more bytes

        frame_bytes = bytes(buffer[:total])
        del buffer[:total]

        parsed = parse_frame(frame_bytes, is_request)
        if parsed is not None:
            frames.append(parsed)

    return frames


def parse_frame(data: bytes, is_request: bool) -> ModbusFrame | None:
    """Parse one complete ADU. Returns None if it is malformed."""
    if len(data) < MIN_FRAME_LEN:
        return None

    tid = int.from_bytes(data[0:2], "big")
    unit = data[6]
    func = data[7]
    payload = data[8:]

    if func & EXCEPTION_MASK:
        return ModbusFrame(
            tid=tid,
            unit=unit,
            func=func & ~EXCEPTION_MASK,
            is_request=is_request,
            raw_len=len(data),
            exception=payload[0] if payload else None,
        )

    address, quantity = _parse_address_quantity(func, payload, is_request)
    return ModbusFrame(
        tid=tid,
        unit=unit,
        func=func,
        is_request=is_request,
        raw_len=len(data),
        address=address,
        quantity=quantity,
    )


def _parse_address_quantity(
    func: int, payload: bytes, is_request: bool
) -> tuple[int | None, int | None]:
    """Address and quantity, where the function code and direction carry them.

    Responses to reads carry only a byte count, not the address — the
    address has to be correlated from the matching request by transaction
    id, which sensor/tap.py does.
    """
    # Responses to reads carry a byte count where a request carries
    # address+quantity, so only write echoes are readable on the way back.
    readable = (1, 2, 3, 4, 5, 6, 15, 16) if is_request else (5, 6, 15, 16)
    if func not in readable or len(payload) < 4:
        return None, None

    address = int.from_bytes(payload[0:2], "big")
    # Single-point writes carry a value in the second field, not a count.
    quantity = 1 if func in (5, 6) else int.from_bytes(payload[2:4], "big")
    return address, quantity
