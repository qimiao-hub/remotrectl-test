"""Pure codec for the 14-byte HIL control protocol.

This module intentionally has no dependency on ``hil_project``.  The wire
contract is duplicated here so the simulator can be deployed independently.
"""

from dataclasses import dataclass


FRAME_LENGTH = 14
READ_MESSAGE = 0x00


BIT_SIGNALS = (
    ("S2-N", 4, 0),
    ("S2-R", 4, 1),
    ("S2-D", 4, 2),
    ("A1", 4, 3),
    ("A2", 4, 4),
    ("A3", 4, 5),
    ("B1", 4, 6),
    ("B2", 4, 7),
    ("B3", 5, 0),
    ("B4", 5, 1),
    ("B5", 5, 2),
    ("Q1", 5, 3),
    ("Q2", 5, 4),
    ("Q3", 5, 5),
    ("Q4", 5, 6),
    ("Q5", 5, 7),
    ("Q6", 6, 0),
    ("Q7-1", 6, 1),
    ("Q7-2", 6, 2),
    ("Q8-1", 6, 3),
    ("Q8-2", 6, 4),
    ("J1", 6, 5),
    ("X1", 7, 0),
    ("X2", 7, 1),
    ("X3-1", 7, 2),
    ("X3-2", 7, 3),
    ("X3-3", 7, 4),
    ("X4", 7, 5),
    ("X5-1", 7, 6),
    ("X5-2", 7, 7),
    ("X6-1", 8, 0),
    ("X6-2", 8, 1),
    ("X6-3", 8, 2),
    ("按键面板掉线故障", 13, 0),
    ("方向盘组件掉线故障", 13, 1),
    ("电机掉线故障", 13, 2),
    ("预留_Bit3", 13, 3),
    ("预留_Bit4", 13, 4),
    ("预留_Bit5", 13, 5),
    ("预留_Bit6", 13, 6),
    ("预留_Bit7", 13, 7),
)

ALL_SIGNAL_NAMES = tuple(name for name, _, _ in BIT_SIGNALS) + ("Steering", "Throttle", "Brake")


class HilProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class HilFrame:
    version: int
    message_type: int
    message_id: int
    declared_length: int
    signals: dict
    raw: bytes


def build_read_request(version=0x0C, message_id=0):
    """Build the minimal four-byte read request accepted by hil_project."""

    return bytes((version & 0xFF, READ_MESSAGE, message_id & 0xFF, 4))


def decode_state_frame(data, steering_endian="little"):
    if len(data) != FRAME_LENGTH:
        raise HilProtocolError("HIL state frame must be exactly 14 bytes")
    version, message_type, message_id, declared_length = data[:4]
    if declared_length not in (10, FRAME_LENGTH):
        raise HilProtocolError("HIL frame length byte must be 10 or 14")
    if message_type not in (0x00, 0xFF):
        raise HilProtocolError("unsupported HIL message type 0x%02X" % message_type)
    if steering_endian not in ("little", "big"):
        raise HilProtocolError("steering_endian must be little or big")

    signals = {}
    for name, byte_index, bit_index in BIT_SIGNALS:
        signals[name] = (data[byte_index] >> bit_index) & 0x01
    signals["Steering"] = int.from_bytes(data[9:11], steering_endian, signed=True)
    signals["Throttle"] = data[11]
    signals["Brake"] = data[12]
    return HilFrame(version, message_type, message_id, declared_length, signals, bytes(data))
