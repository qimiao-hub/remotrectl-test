"""车端 TCP 帧的流式解析器。"""

from dataclasses import dataclass


START = b"##"
HEADER_LENGTH = 24
TRAILER_LENGTH = 1
REALTIME_REPORT_COMMAND = 0x02


class VehicleFrameError(ValueError):
    pass


@dataclass(frozen=True)
class VehicleFrame:
    command_id: int
    response_flag: int
    vin: str
    encryption: int
    payload_length: int
    payload: bytes
    bcc: int
    bcc_valid: bool
    raw: bytes

    @property
    def is_realtime_report(self):
        return self.command_id == REALTIME_REPORT_COMMAND


def xor_bcc(data):
    value = 0
    for byte in data:
        value ^= byte
    return value


class VehicleFrameParser(object):
    """支持 TCP 半包、粘包以及帧头之前的无效字节。"""

    def __init__(self, max_payload_length=65535):
        self.buffer = bytearray()
        self.max_payload_length = int(max_payload_length)
        self.discarded_bytes = 0

    def feed(self, data):
        if data:
            self.buffer.extend(data)
        frames = []
        while True:
            if not self._align_start():
                break
            if len(self.buffer) < HEADER_LENGTH:
                break

            payload_length = int.from_bytes(self.buffer[22:24], "big")
            if payload_length > self.max_payload_length:
                del self.buffer[0]
                self.discarded_bytes += 1
                continue

            total_length = HEADER_LENGTH + payload_length + TRAILER_LENGTH
            if len(self.buffer) < total_length:
                break

            raw = bytes(self.buffer[:total_length])
            del self.buffer[:total_length]
            expected_bcc = xor_bcc(raw[2:-1])
            frames.append(
                VehicleFrame(
                    command_id=raw[2],
                    response_flag=raw[3],
                    vin=raw[4:21].decode("ascii", errors="replace").rstrip("\x00"),
                    encryption=raw[21],
                    payload_length=payload_length,
                    payload=raw[24:-1],
                    bcc=raw[-1],
                    bcc_valid=raw[-1] == expected_bcc,
                    raw=raw,
                )
            )
        return frames

    def _align_start(self):
        index = self.buffer.find(START)
        if index < 0:
            if self.buffer.endswith(START[:1]):
                self.discarded_bytes += len(self.buffer) - 1
                del self.buffer[:-1]
            else:
                self.discarded_bytes += len(self.buffer)
                self.buffer.clear()
            return False
        if index:
            self.discarded_bytes += index
            del self.buffer[:index]
        return True
