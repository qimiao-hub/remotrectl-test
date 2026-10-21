#!/usr/bin/env python
"""接收并记录车端 0x02 TCP 帧。"""

import argparse
import socket
import threading

from raw_logger import RawFrameLogger
from vehicle_frame import VehicleFrameParser


class VehicleTcpReceiver(object):
    def __init__(self, host="0.0.0.0", port=60006, log_dir="logs"):
        self.host = host
        self.port = int(port)
        self.logger = RawFrameLogger(log_dir)
        self._server = None
        self._stop_event = threading.Event()

    def serve_forever(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(16)
        server.settimeout(1.0)
        self._server = server
        print("车端 TCP 接收器监听 %s:%s" % (self.host, self.port), flush=True)
        print("原始报文日志目录：%s" % self.logger.log_dir.resolve(), flush=True)

        try:
            while not self._stop_event.is_set():
                try:
                    connection, address = server.accept()
                except socket.timeout:
                    continue
                thread = threading.Thread(
                    target=self._handle_connection,
                    args=(connection, address),
                    name="vehicle-%s:%s" % address,
                    daemon=True,
                )
                thread.start()
        finally:
            server.close()
            self._server = None

    def stop(self):
        self._stop_event.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass

    def _handle_connection(self, connection, address):
        peer = "%s:%s" % address
        parser = VehicleFrameParser()
        print("客户端已连接：%s" % peer, flush=True)
        try:
            with connection:
                while not self._stop_event.is_set():
                    data = connection.recv(4096)
                    if not data:
                        break
                    self.logger.record_chunk(peer, data)
                    for frame in parser.feed(data):
                        self.logger.record_frame(peer, frame)
                        label = "02实时上报" if frame.is_realtime_report else "其他帧"
                        print(
                            "[%s] %s cmd=0x%02X vin=%s payload=%s BCC=%s raw=%s"
                            % (
                                label,
                                peer,
                                frame.command_id,
                                frame.vin,
                                frame.payload_length,
                                "OK" if frame.bcc_valid else "FAIL",
                                frame.raw.hex(" ").upper(),
                            ),
                            flush=True,
                        )
        except (ConnectionError, OSError) as exc:
            print("连接异常 %s：%s" % (peer, exc), flush=True)
        finally:
            print("客户端已断开：%s" % peer, flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description="接收车端 0x02 TCP 帧并保存原始报文")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=60006, help="监听端口，默认 60006")
    parser.add_argument("--log-dir", default="logs", help="日志目录，默认 logs")
    return parser.parse_args()


def main():
    args = parse_args()
    receiver = VehicleTcpReceiver(args.host, args.port, args.log_dir)
    try:
        receiver.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止接收器……", flush=True)
        receiver.stop()


if __name__ == "__main__":
    main()
