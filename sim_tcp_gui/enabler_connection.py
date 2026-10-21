import json
import socket
import threading
import time


class TcpTelemetryServer(object):
    def __init__(self, state_provider, control_handler, host="0.0.0.0", port=50001):
        self.state_provider = state_provider
        self.control_handler = control_handler
        self.host = host
        self.port = port
        self.clients = []
        self.received = []
        self.local_ip = self._detect_local_ip()

        listen_thread = threading.Thread(target=self._listen_loop, name="TCP-Control")
        listen_thread.daemon = True
        listen_thread.start()

        telemetry_thread = threading.Thread(target=self._telemetry_loop, name="TCP-Telemetry")
        telemetry_thread.daemon = True
        telemetry_thread.start()

    def _detect_local_ip(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("8.8.8.8", 80))
                return probe.getsockname()[0]
        except OSError:
            return socket.gethostbyname(socket.gethostname())

    def _listen_loop(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(8)
        print(f"Listening for TCP control clients on {self.host}:{self.port}")
        while True:
            conn, addr = server.accept()
            handler = SocketHandler(conn, addr, self.control_handler, self._record_message)
            handler.daemon = True
            handler.start()
            self.clients.append(handler)

    def _telemetry_loop(self):
        while True:
            try:
                payload = json.dumps(self.state_provider()["telemetry"], separators=(",", ":")) + "\n"
                self._broadcast(payload.encode("utf-8"))
            except Exception as exc:
                print(f"Telemetry loop error: {exc}")
            time.sleep(0.05)

    def _broadcast(self, payload):
        alive = []
        for client in self.clients:
            try:
                client.send(payload)
                alive.append(client)
            except OSError:
                client.close()
        self.clients = alive

    def _record_message(self, address, payload):
        self.received.append({"address": f"{address[0]}:{address[1]}", "payload": payload})
        self.received = self.received[-40:]


class SocketHandler(threading.Thread):
    def __init__(self, connection, address, control_handler, record_message):
        super(SocketHandler, self).__init__()
        self.connection = connection
        self.address = address
        self.control_handler = control_handler
        self.record_message = record_message
        self.buffer = ""

    def send(self, data):
        self.connection.sendall(data)

    def close(self):
        try:
            self.connection.close()
        except OSError:
            pass

    def run(self):
        try:
            self.connection.sendall(
                b'{"type":"hello","message":"send JSON lines: throttle, brake, steering, gear"}\n'
            )
            while True:
                try:
                    data = self.connection.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                self.buffer += data.decode("utf-8", errors="replace").replace("\x00", "\n")
                self._drain_buffer()
        finally:
            self.close()

    def _drain_buffer(self):
        while "\n" in self.buffer:
            raw, self.buffer = self.buffer.split("\n", 1)
            raw = raw.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("payload must be a JSON object")
                snapshot = self.control_handler(payload)
                response = {
                    "type": "ack",
                    "accepted": True,
                    "controls": snapshot["controls"],
                }
            except Exception as exc:
                response = {"type": "ack", "accepted": False, "error": str(exc)}
            self.record_message(self.address, raw)
            self.connection.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
