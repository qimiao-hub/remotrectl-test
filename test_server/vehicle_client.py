from __future__ import annotations

import argparse
import asyncio
import json
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from sim_logging import JsonlLogger
from sim_protocol import (
    RESP_COMMAND,
    RESP_SUCCESS,
    build_vehicle_frame,
    fixed_ascii,
    parse_vehicle_frames,
    protocol_time,
    to_hex,
    u16,
    u32,
)


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def find_vehicle(config: dict[str, Any], vin: str | None) -> dict[str, Any]:
    vehicles = config.get("vehicles", [])
    if vin:
        for vehicle in vehicles:
            if vehicle.get("vin") == vin:
                return vehicle
        raise SystemExit(f"未找到车辆 VIN: {vin}")
    if not vehicles:
        raise SystemExit("配置中没有 vehicles")
    return vehicles[0]


def vehicle_login_payload(vehicle: dict[str, Any], serial: int) -> bytes:
    """0x01 车端登录 payload，字段顺序按协议表 7 固定排列。"""

    data = bytearray()
    data += protocol_time()
    data.append(serial & 0xFF)
    data += fixed_ascii(vehicle.get("iccid", ""), 20)
    data += fixed_ascii(vehicle.get("hardware_version", "0101"), 4)
    data += fixed_ascii(vehicle.get("software_version", "0101"), 4)
    data.append(int(vehicle.get("driving_state", 1)) & 0xFF)
    data.append(int(vehicle.get("gear", 0)) & 0xFF)
    data += u16(int(vehicle.get("length_mm", 10000)))
    data += u16(int(vehicle.get("width_mm", 3500)))
    data += u16(int(vehicle.get("camera_height_mm", 2500)))
    data += u16(int(vehicle.get("front_blind_spot_mm", 5000)))
    data += u16(int(vehicle.get("camera_pitch_deg", 0)))
    return bytes(data)


REALTIME_REPORT_FIELDS: dict[str, int] = {
    "speed_deci_kmh": 0,
    "heading_raw": -1,
    "wheel_center_raw": 0x0190,
    "throttle_percent": 0,
    "brake_percent": 0,
    "engine_speed_raw": 12000,
    "total_mileage_deci_km": 56500,
    "trip_mileage_deci_km": 1,
    "pitch_raw": 0x0059,
    "gear": 0,
    "fuel_or_battery_percent": 89,
    "water_temperature_raw": 0x4A,
    "brake_pressure1_raw": 0x81,
    "brake_pressure2_raw": 0x59,
    "left_switch": 0,
    "right_switch": 0,
    "button_operation1": 0,
    "button_operation2": 0,
    "driving_state": 1,
    "lift_reached": 0,
    "lower_reached": 0,
    "fault_info1": 0,
    "fault_info2": 0,
}


def report_value(vehicle: dict[str, Any], name: str, default: int) -> int:
    report_config = vehicle.get("realtime_report", {})
    if isinstance(report_config, dict) and name in report_config:
        return int(report_config[name])
    return int(vehicle.get(name, default))


def realtime_report_snapshot(vehicle: dict[str, Any]) -> dict[str, int]:
    return {name: report_value(vehicle, name, default) for name, default in REALTIME_REPORT_FIELDS.items()}


def update_realtime_report(vehicle: dict[str, Any], data: dict[str, Any]) -> dict[str, int]:
    unknown = sorted(set(data) - set(REALTIME_REPORT_FIELDS))
    if unknown:
        raise ValueError(f"不支持的实时上报字段: {', '.join(unknown)}")
    report_config = vehicle.setdefault("realtime_report", {})
    if not isinstance(report_config, dict):
        report_config = {}
        vehicle["realtime_report"] = report_config
    for name, value in data.items():
        report_config[name] = int(value)
    return realtime_report_snapshot(vehicle)


def realtime_report_payload(vehicle: dict[str, Any], sequence: int) -> bytes:
    """0x02 高频实时上报，长度固定 40 字节；数值保持温和，方便接管流程演示。"""

    heading = report_value(vehicle, "heading_raw", -1)
    if heading < 0:
        heading = sequence % 3600
    data = bytearray()
    data += protocol_time()
    data += u16(report_value(vehicle, "speed_deci_kmh", 0))
    data += u16(heading)
    data += u16(report_value(vehicle, "wheel_center_raw", 0x0190))
    data.append(report_value(vehicle, "throttle_percent", 0) & 0xFF)
    data.append(report_value(vehicle, "brake_percent", 0) & 0xFF)
    data += u16(report_value(vehicle, "engine_speed_raw", 12000))
    data += u32(report_value(vehicle, "total_mileage_deci_km", 56500))
    data += u16(report_value(vehicle, "trip_mileage_deci_km", 1))
    data += u16(report_value(vehicle, "pitch_raw", 0x0059))
    data.append(report_value(vehicle, "gear", 0) & 0xFF)
    data.append(report_value(vehicle, "fuel_or_battery_percent", 89) & 0xFF)
    data.append(report_value(vehicle, "water_temperature_raw", 0x4A) & 0xFF)
    data.append(report_value(vehicle, "brake_pressure1_raw", 0x81) & 0xFF)
    data.append(report_value(vehicle, "brake_pressure2_raw", 0x59) & 0xFF)
    data.append(report_value(vehicle, "left_switch", 0) & 0xFF)
    data.append(report_value(vehicle, "right_switch", 0) & 0xFF)
    data.append(report_value(vehicle, "button_operation1", 0) & 0xFF)
    data.append(report_value(vehicle, "button_operation2", 0) & 0xFF)
    data.append(report_value(vehicle, "driving_state", 1) & 0xFF)
    data.append(report_value(vehicle, "lift_reached", 0) & 0xFF)
    data.append(report_value(vehicle, "lower_reached", 0) & 0xFF)
    data.append(report_value(vehicle, "fault_info1", 0) & 0xFF)
    data.append(report_value(vehicle, "fault_info2", 0) & 0xFF)
    return bytes(data)


def response_payload_time_serial(serial: bytes) -> bytes:
    return protocol_time() + serial


class VehicleClient:
    def __init__(self, config: dict[str, Any], vehicle: dict[str, Any], args: argparse.Namespace):
        self.config = config
        self.vehicle = vehicle
        self.args = args
        self.vin = str(vehicle["vin"])
        self.login_serial = 1
        self.login_sent = False
        self.sequence = 0
        self.debug_hex = args.debug_hex if args.debug_hex is not None else bool(config.get("tcp", {}).get("debug_hex", False))
        logging_config = config.get("logging", {})
        self.logger = JsonlLogger(
            args.log_dir or logging_config.get("dir", "logs"),
            enabled=args.file_log if args.file_log is not None else bool(logging_config.get("enabled", True)),
        )

    def log_frame(self, direction: str, frame: bytes, note: str, packet=None) -> None:
        item = {
            "direction": direction,
            "vehicle_vin": self.vin,
            "size": len(frame),
            "note": note,
            "hex": to_hex(frame),
        }
        if packet is not None:
            item.update(
                {
                    "payload_name": packet.payload_name,
                    "decoded_payload": packet.decoded_payload,
                    "real_payload": packet.real_payload,
                    "parse_warnings": packet.parse_warnings or [],
                }
            )
        self.logger.write("vehicle-tcp", item)

    def frame(self, command_id: int, response_flag: int, payload: bytes) -> bytes:
        return build_vehicle_frame(command_id, response_flag, self.vin, payload)

    async def send_frame(self, writer: asyncio.StreamWriter, command_id: int, response_flag: int, payload: bytes, note: str) -> None:
        frame = self.frame(command_id, response_flag, payload)
        writer.write(frame)
        await writer.drain()
        self.log_frame("tcp.tx.vehicle", frame, note)
        if self.debug_hex:
            print(f"[TX] {note} {to_hex(frame)}")

    async def send_login_once(self, writer: asyncio.StreamWriter) -> None:
        if self.login_sent:
            return
        await self.send_frame(writer, 0x01, RESP_COMMAND, vehicle_login_payload(self.vehicle, self.login_serial), "车端登录 0x01")
        self.login_sent = True

    async def report_loop(self, writer: asyncio.StreamWriter) -> None:
        while not writer.is_closing():
            await asyncio.sleep(float(self.args.report_interval))
            self.sequence += 1
            await self.send_frame(
                writer,
                0x02,
                RESP_COMMAND,
                realtime_report_payload(self.vehicle, self.sequence),
                "实时上报 0x02",
            )

    async def handle_packet(self, writer: asyncio.StreamWriter, packet) -> bool:
        """处理驾驶舱下行命令；返回 False 表示收到登出并主动断开。"""

        if self.debug_hex:
            print(f"[RX] cmd=0x{packet.command_id:02X} flag=0x{packet.response_flag:02X} {to_hex(packet.raw)}")
        self.log_frame(
            "tcp.rx.vehicle",
            packet.raw,
            f"cmd=0x{packet.command_id:02X} flag=0x{packet.response_flag:02X}",
            packet,
        )

        payload = packet.payload
        if packet.command_id == 0x01:
            print("车端登录响应已收到")
        elif packet.command_id == 0x03:
            serial = payload[8:9] if len(payload) >= 9 else b"\x00"
            mode = payload[9] if len(payload) >= 10 else 0x01
            status = 0x01 if mode == 0x01 else (0x02 if mode == 0x02 else 0x00)
            await self.send_frame(writer, 0x03, RESP_SUCCESS, protocol_time() + serial + bytes([status]), "控车请求响应 0x03")
        elif packet.command_id == 0x04:
            serial = payload[8:9] if len(payload) >= 9 else b"\x00"
            await self.send_frame(writer, 0x04, RESP_SUCCESS, response_payload_time_serial(serial), "实时控制响应 0x04")
        elif packet.command_id == 0x05:
            serial = payload[8:10] if len(payload) >= 10 else b"\x00\x00"
            await self.send_frame(writer, 0x05, RESP_SUCCESS, response_payload_time_serial(serial), "车舱心跳响应 0x05")
        elif packet.command_id == 0x06:
            serial = payload[8:9] if len(payload) >= 9 else b"\x00"
            await self.send_frame(writer, 0x06, RESP_SUCCESS, response_payload_time_serial(serial), "紧急停车响应 0x06")
        elif packet.command_id == 0x08:
            await self.send_frame(writer, 0x08, RESP_SUCCESS, protocol_time(), "车端登出响应 0x08")
        return True

    async def read_loop(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        buffer = bytearray()
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            buffer.extend(chunk)
            for packet in parse_vehicle_frames(buffer, self.args.vehicle_length_endian):
                keep_running = await self.handle_packet(writer, packet)
                if not keep_running:
                    writer.close()
                    return

    async def run_once(self, host: str, port: int) -> None:
        reader, writer = await asyncio.open_connection(host, port)
        self.login_sent = False
        print(f"车端 {self.vin} 已连接驾驶舱 {host}:{port}")
        await self.send_login_once(writer)
        reporter = asyncio.create_task(self.report_loop(writer))
        try:
            await self.read_loop(reader, writer)
        finally:
            reporter.cancel()
            writer.close()
            await writer.wait_closed()

    async def run_forever(self, host: str, port: int) -> None:
        while True:
            try:
                await self.run_once(host, port)
            except (ConnectionError, OSError) as exc:
                print(f"连接驾驶舱失败或断开：{exc}")
            if self.args.once:
                break
            await asyncio.sleep(float(self.args.reconnect_interval))


def create_control_app(client: VehicleClient) -> FastAPI:
    app = FastAPI(title="车端实时上报控制", version="1.0.0")

    @app.get("/__vehicle/realtime_report")
    async def get_realtime_report() -> JSONResponse:
        return JSONResponse(
            {
                "vin": client.vin,
                "fields": list(REALTIME_REPORT_FIELDS),
                "data": realtime_report_snapshot(client.vehicle),
            }
        )

    @app.post("/__vehicle/realtime_report")
    async def post_realtime_report(request: Request) -> JSONResponse:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        data = body.get("data", body)
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="data must be a JSON object")
        try:
            snapshot = update_realtime_report(client.vehicle, data)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"ok": True, "vin": client.vin, "data": snapshot})

    return app


async def run_control_server(client: VehicleClient, host: str, port: int) -> None:
    server = uvicorn.Server(
        uvicorn.Config(
            create_control_app(client),
            host=host,
            port=port,
            log_level="warning",
        )
    )
    await server.serve()


def apply_overrides(config: dict[str, Any], args: argparse.Namespace) -> tuple[str, int]:
    cockpit = config.setdefault("cockpit", {})
    host = args.cockpit_host or cockpit.get("ip", "127.0.0.1")
    port = int(args.cockpit_port or cockpit.get("port", 60006))
    return host, port


def control_endpoint(config: dict[str, Any], args: argparse.Namespace) -> tuple[bool, str, int]:
    control = config.get("vehicle_control", {})
    enabled = args.control_http if args.control_http is not None else bool(control.get("enabled", False))
    host = args.control_host or control.get("host", "127.0.0.1")
    port = int(args.control_port or control.get("port", 18081))
    return enabled, host, port


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="车端 TCP client 模拟器")
    parser.add_argument("--config", default="simulator.json", help="配置文件路径")
    parser.add_argument("--vehicle", help="要模拟的车辆 VIN；默认取配置第一辆")
    parser.add_argument("--cockpit-host", help="驾驶舱 TCP 地址")
    parser.add_argument("--cockpit-port", type=int, help="驾驶舱 TCP 端口")
    parser.add_argument("--debug-hex", action=argparse.BooleanOptionalAction, default=None, help="打印收发帧十六进制")
    parser.add_argument("--vehicle-length-endian", choices=["auto", "big", "little"], default="auto", help="入站车端帧长度字段解析方式")
    parser.add_argument("--report-interval", type=float, default=0.2, help="实时上报周期，单位秒")
    parser.add_argument("--reconnect-interval", type=float, default=3.0, help="断线重连间隔，单位秒")
    parser.add_argument("--once", action="store_true", help="连接断开后不重连")
    parser.add_argument("--log-dir", help="落盘日志目录，默认 logs")
    parser.add_argument("--file-log", action=argparse.BooleanOptionalAction, default=None, help="是否启用落盘 JSONL 日志")
    parser.add_argument("--control-http", action=argparse.BooleanOptionalAction, default=None, help="是否启用车端 HTTP 控制接口")
    parser.add_argument("--control-host", help="车端 HTTP 控制接口监听地址")
    parser.add_argument("--control-port", type=int, help="车端 HTTP 控制接口监听端口")
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    config = load_config(str(Path(args.config)))
    vehicle = find_vehicle(config, args.vehicle)
    host, port = apply_overrides(config, args)
    client = VehicleClient(config, vehicle, args)
    tasks = [asyncio.create_task(client.run_forever(host, port), name="vehicle-tcp")]
    control_enabled, control_host, control_port = control_endpoint(config, args)
    if control_enabled:
        print(f"车端 HTTP 控制接口已启动 http://{control_host}:{control_port}/__vehicle/realtime_report")
        tasks.append(asyncio.create_task(run_control_server(client, control_host, control_port), name="vehicle-control-http"))
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks)


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(main_async())


if __name__ == "__main__":
    main()
