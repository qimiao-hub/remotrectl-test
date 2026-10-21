from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class JsonlLogger:
    """按天写 JSONL 日志；每行一条事件，便于 grep、jq 和现场回放。"""

    def __init__(self, log_dir: str | Path = "logs", *, enabled: bool = True):
        self.log_dir = Path(log_dir)
        self.enabled = enabled
        self._lock = threading.Lock()

    def write(self, channel: str, record: dict[str, Any]) -> None:
        if not self.enabled:
            return
        now = datetime.now()
        event = {
            "ts": now.isoformat(timespec="milliseconds"),
            "time": round(time.time(), 3),
            **record,
        }
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"{channel}-{now:%Y-%m-%d}.jsonl"
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with open(path, "a", encoding="utf-8") as file:
                file.write(line + "\n")
