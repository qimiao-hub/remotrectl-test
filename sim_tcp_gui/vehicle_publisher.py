"""Decoupled publisher for test_server's vehicle report control contract."""

import json
import threading
import time
import urllib.error
import urllib.request


class VehicleReportPublisher(object):
    def __init__(self, config, report_provider):
        self.config = dict(config)
        self.report_provider = report_provider
        self._stop_event = threading.Event()
        self._thread = None
        self._lock = threading.RLock()
        self._status = {
            "enabled": bool(self.config.get("enabled", True)),
            "url": self.config.get("url", "http://127.0.0.1:18081/__vehicle/realtime_report"),
            "published": 0,
            "last_status_code": None,
            "last_error": "",
            "last_update": None,
        }

    def start(self):
        if not self._status["enabled"] or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._run, name="Vehicle-Report-Publisher", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def status(self):
        with self._lock:
            return dict(self._status)

    def _set_status(self, **values):
        with self._lock:
            self._status.update(values)

    def _run(self):
        interval = float(self.config.get("publish_interval", 0.2))
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self.publish_once()
            except (OSError, ValueError, urllib.error.URLError) as exc:
                self._set_status(last_error=str(exc), last_update=time.time())
            elapsed = time.monotonic() - started
            self._stop_event.wait(max(0.0, interval - elapsed))

    def publish_once(self):
        payload = json.dumps(self.report_provider(), separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self._status["url"],
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout = float(self.config.get("request_timeout", 0.5))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            status_code = response.status
        current = self.status()["published"]
        self._set_status(
            published=current + 1,
            last_status_code=status_code,
            last_error="",
            last_update=time.time(),
        )
