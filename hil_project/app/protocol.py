from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.signals import DATA_DEFINITIONS


READ_MESSAGE = 0x00
WRITE_MESSAGE = 0xFF
HEADER_LENGTH = 4
FRAME_LENGTH = 14
PAYLOAD_LENGTH = FRAME_LENGTH - HEADER_LENGTH


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedFrame:
    version: int
    message_type: int
    message_id: int
    length: int
    payload: bytes


def parse_frame(data: bytes) -> ParsedFrame:
    if len(data) < HEADER_LENGTH:
        raise ProtocolError("Frame is shorter than 4-byte header.")

    version, message_type, message_id, length = data[:HEADER_LENGTH]
    payload_length = len(data) - HEADER_LENGTH
    length_matches_total = length == len(data)
    length_matches_payload = length == payload_length
    if not length_matches_total and not length_matches_payload:
        raise ProtocolError(
            f"Header length {length} does not match frame length {len(data)} or payload length {payload_length}."
        )
    if message_type not in {READ_MESSAGE, WRITE_MESSAGE}:
        raise ProtocolError(f"Unsupported message type: 0x{message_type:02X}.")
    if message_type == WRITE_MESSAGE and len(data) != FRAME_LENGTH:
        raise ProtocolError(f"Write frame must be {FRAME_LENGTH} bytes.")
    if message_type == READ_MESSAGE and len(data) not in {HEADER_LENGTH, HEADER_LENGTH + 1, FRAME_LENGTH}:
        raise ProtocolError("Read frame must contain the header, a one-byte placeholder, or a complete state frame.")

    return ParsedFrame(
        version=version,
        message_type=message_type,
        message_id=message_id,
        length=length,
        payload=data[HEADER_LENGTH:],
    )


def decode_payload(data: bytes, steering_endian: Literal["little", "big"] = "little") -> dict[str, int]:
    if len(data) != FRAME_LENGTH:
        raise ProtocolError(f"State frame must be {FRAME_LENGTH} bytes.")

    values: dict[str, int] = {}
    for definition in DATA_DEFINITIONS:
        if definition.bit_number is not None:
            values[definition.name] = (data[definition.start_byte] >> definition.bit_number) & 0x01
        elif definition.name == "Steering":
            values[definition.name] = int.from_bytes(
                data[definition.start_byte : definition.start_byte + 2],
                byteorder=steering_endian,
                signed=True,
            )
        else:
            values[definition.name] = data[definition.start_byte]
    return values


def encode_state(
    signals: dict[str, int],
    *,
    version: int = 0x0C,
    message_id: int = 0,
    message_type: int = READ_MESSAGE,
    steering_endian: Literal["little", "big"] = "little",
) -> bytes:
    frame = bytearray(FRAME_LENGTH)
    frame[0] = version & 0xFF
    frame[1] = message_type & 0xFF
    frame[2] = message_id & 0xFF
    frame[3] = FRAME_LENGTH

    for definition in DATA_DEFINITIONS:
        value = int(signals.get(definition.name, definition.initial))
        if definition.bit_number is not None:
            if value:
                frame[definition.start_byte] |= 1 << definition.bit_number
            else:
                frame[definition.start_byte] &= ~(1 << definition.bit_number)
        elif definition.name == "Steering":
            frame[definition.start_byte : definition.start_byte + 2] = value.to_bytes(
                2,
                byteorder=steering_endian,
                signed=True,
            )
        else:
            frame[definition.start_byte] = value & 0xFF

    return bytes(frame)
