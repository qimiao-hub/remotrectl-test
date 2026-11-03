from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.signals import DATA_DEFINITIONS


READ_MESSAGE = 0x00
WRITE_MESSAGE = 0xFF
HEADER_LENGTH = 4
FRAME_LENGTH = 14
PAYLOAD_LENGTH = FRAME_LENGTH - HEADER_LENGTH
PROTOCOL_FRAME_LENGTHS = {"T1": FRAME_LENGTH, "T1-3": FRAME_LENGTH + 1}


class ProtocolError(ValueError):
    pass


def protocol_frame_length(protocol_name: str) -> int:
    """返回指定输出协议的总帧长，拒绝未知协议，默认旧协议长度常量保持不变。"""
    if protocol_name not in PROTOCOL_FRAME_LENGTHS:
        raise ProtocolError(f"Unsupported protocol: {protocol_name}.")
    return PROTOCOL_FRAME_LENGTHS[protocol_name]


@dataclass(frozen=True)
class ParsedFrame:
    version: int
    message_type: int
    message_id: int
    length: int
    payload: bytes


def parse_frame(data: bytes) -> ParsedFrame:
    """校验 T1/T1-3 输入帧及既有读取请求，兼容总长和数据区长度两种头部约定。"""
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
    if message_type == WRITE_MESSAGE and len(data) not in PROTOCOL_FRAME_LENGTHS.values():
        raise ProtocolError("Write frame must be 14 (T1) or 15 (T1-3) bytes.")
    if message_type == READ_MESSAGE and len(data) not in {HEADER_LENGTH, HEADER_LENGTH + 1, *PROTOCOL_FRAME_LENGTHS.values()}:
        raise ProtocolError("Read frame must contain the header, a one-byte placeholder, or a complete state frame.")

    return ParsedFrame(
        version=version,
        message_type=message_type,
        message_id=message_id,
        length=length,
        payload=data[HEADER_LENGTH:],
    )


def decode_payload(data: bytes, steering_endian: Literal["little", "big"] = "little") -> dict[str, int]:
    """按实际帧长解码独立信号；旧 T1 帧不提供 Brake2，也不覆盖其已保存的运行态值。"""
    if len(data) not in PROTOCOL_FRAME_LENGTHS.values():
        raise ProtocolError("State frame must be 14 (T1) or 15 (T1-3) bytes.")

    values: dict[str, int] = {}
    for definition in DATA_DEFINITIONS:
        if definition.start_byte >= len(data):
            continue
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
    protocol_name: Literal["T1", "T1-3"] = "T1",
) -> bytes:
    """按选择的协议编码；T1-3 仅在旧布局后追加独立 Brake2 字节，长度字段同步更新。"""
    frame_length = protocol_frame_length(protocol_name)
    frame = bytearray(frame_length)
    frame[0] = version & 0xFF
    frame[1] = message_type & 0xFF
    frame[2] = message_id & 0xFF
    frame[3] = frame_length

    for definition in DATA_DEFINITIONS:
        # T1 保持原 14 字节，扩展信号只在 T1-3 中占用偏移 14。
        if definition.start_byte >= frame_length:
            continue
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
