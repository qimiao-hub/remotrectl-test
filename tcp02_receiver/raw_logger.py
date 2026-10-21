"""线程安全的原始 TCP 数据与逐帧日志。"""

import json
import threading
from datetime import datetime
from pathlib import Path


class RawFrameLogger(object):
    def __init__(self, log_dir):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def record_chunk(self, peer, data):
        """原样追加每次 recv 得到的字节，不做任何转换。"""

        with self._lock:
            with self._binary_path().open("ab") as file:
                file.write(data)
            self._append_json(
                self._chunks_path(),
                {
                    "timestamp": self._timestamp(),
                    "peer": peer,
                    "size": len(data),
                    "raw_hex": data.hex(" ").upper(),
                },
            )

    def record_frame(self, peer, frame):
        item = {
            "timestamp": self._timestamp(),
            "peer": peer,
            "command_id": "0x%02X" % frame.command_id,
            "response_flag": "0x%02X" % frame.response_flag,
            "vin": frame.vin,
            "payload_length": frame.payload_length,
            "frame_length": len(frame.raw),
            "bcc": "0x%02X" % frame.bcc,
            "bcc_valid": frame.bcc_valid,
            "is_02_realtime_report": frame.is_realtime_report,
            "raw_hex": frame.raw.hex(" ").upper(),
        }
        with self._lock:
            self._append_json(self._frames_path(), item)

    def _append_json(self, path, item):
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _date(self):
        return datetime.now().strftime("%Y-%m-%d")

    def _timestamp(self):
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    def _binary_path(self):
        return self.log_dir / ("tcp-raw-%s.bin" % self._date())

    def _chunks_path(self):
        return self.log_dir / ("tcp-chunks-%s.jsonl" % self._date())

    def _frames_path(self):
        return self.log_dir / ("vehicle-frames-%s.jsonl" % self._date())
