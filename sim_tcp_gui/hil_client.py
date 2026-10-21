"""Reconnectable polling client for the HIL TCP service."""

import socket
import threading
import time

from hil_protocol import FRAME_LENGTH, build_read_request, decode_state_frame


class HilTcpClient(object):
    def __init__(self, config, signal_handler):
        self.config = dict(config)
        self.signal_handler = signal_handler
        self._stop_event = threading.Event()
        self._socket = None
        self._lock = threading.RLock()
        self._thread = None
        self._status = {
            "enabled": bool(self.config.get("enabled", True)),
            "connected": False,
            "host": self.config.get("host", "127.0.0.1"),
            "port": int(self.config.get("port", 2000)),
            "frames_received": 0,
            "last_frame_hex": "",
            "last_error": "",
            "last_update": None,
        }

    def start(self):
        if not self._status["enabled"] or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._run, name="HIL-TCP-Client", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._close_socket()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def status(self):
        with self._lock:
            return dict(self._status)

    def _set_status(self, **values):
        with self._lock:
            self._status.update(values)

    def _run(self):
        reconnect_interval = float(self.config.get("reconnect_interval", 1.0))
        while not self._stop_event.is_set():
            try:
                self._connect_and_poll()
            except (OSError, ValueError) as exc:
                self._set_status(connected=False, last_error=str(exc))
            finally:
                self._close_socket()
            self._stop_event.wait(reconnect_interval)

    def _connect_and_poll(self):
        host = self._status["host"]
        port = self._status["port"]
        timeout = float(self.config.get("socket_timeout", 1.0))
        poll_interval = float(self.config.get("poll_interval", 0.05))
        version = int(self.config.get("version", 0x0C))
        steering_endian = self.config.get("steering_endian", "little")

        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)
        self._socket = sock
        self._set_status(connected=True, last_error="")
        message_id = 0

        while not self._stop_event.is_set():
            sock.sendall(build_read_request(version, message_id))
            raw = self._recv_exact(sock, FRAME_LENGTH)
            frame = decode_state_frame(raw, steering_endian)
            self.signal_handler(frame.signals)
            current = self.status()["frames_received"]
            self._set_status(
                connected=True,
                frames_received=current + 1,
                last_frame_hex=raw.hex(" ").upper(),
                last_update=time.time(),
                last_error="",
            )
            message_id = (message_id + 1) & 0xFF
            self._stop_event.wait(poll_interval)

    def _recv_exact(self, sock, length):
        chunks = bytearray()
        while len(chunks) < length:
            chunk = sock.recv(length - len(chunks))
            if not chunk:
                raise ConnectionError("HIL TCP server closed the connection")
            chunks.extend(chunk)
        return bytes(chunks)

    def _close_socket(self):
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self._set_status(connected=False)
