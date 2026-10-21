from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from sim_logging import JsonlLogger
from sim_protocol import (
    CLOUD_DEVICE_COCKPIT,
    RESP_COMMAND,
    RESP_SUCCESS,
    Packet,
    build_cloud_frame,
    fixed_ascii,
    pack_cloud_map_versions,
    pack_vehicle_items,
    parse_cloud_frames,
    parse_u16,
    to_hex,
    u16,
)


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def request_id() -> str:
    return secrets.token_hex(8)


def envelope(data: Any = None, **extra: Any) -> dict[str, Any]:
    """HTTP 响应统一包一层 requestId，贴近日志里的云端返回格式。"""

    body = {"requestId": request_id(), "data": data if data is not None else {}}
    body.update(extra)
    return body


class CloudSession:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        peer = writer.get_extra_info("peername")
        self.peer = f"{peer[0]}:{peer[1]}" if peer else "unknown"
        self.unique_id = ""
        self.connected_at = time.time()
        self.last_seen = self.connected_at
        self.selected_vin = ""
        self.command_counts: dict[str, int] = {}

    def summary(self) -> dict[str, Any]:
        return {
            "peer": self.peer,
            "unique_id": self.unique_id,
            "selected_vin": self.selected_vin,
            "connected_seconds": round(time.time() - self.connected_at, 3),
            "idle_seconds": round(time.time() - self.last_seen, 3),
            "command_counts": self.command_counts,
        }


class SimulatorState:
    def __init__(self, config: dict[str, Any], debug_hex: bool):
        self.config = config
        self.debug_hex = debug_hex
        logging_config = config.get("logging", {})
        self.logger = JsonlLogger(
            logging_config.get("dir", "logs"),
            enabled=bool(logging_config.get("enabled", True)),
        )
        self.tcp_server: asyncio.AbstractServer | None = None
        self.sessions: list[CloudSession] = []
        self.last_token = ""
        self.selected_vehicle_vin = ""
        self.control_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.recent_frames: deque[dict[str, Any]] = deque(maxlen=100)
        self.serial = 1

    def next_serial(self) -> int:
        self.serial = (self.serial + 1) & 0xFFFF
        return self.serial

    def record_raw(self, direction: str, raw: bytes, note: str = "") -> None:
        item = {
            "time": round(time.time(), 3),
            "direction": direction,
            "size": len(raw),
            "note": note,
        }
        if self.debug_hex:
            item["hex"] = to_hex(raw)
        self.recent_frames.append(item)
        # 落盘日志始终保留原始十六进制，方便现场按帧回放。
        self.logger.write("cloud-tcp", {**item, "hex": to_hex(raw)})

    def record_packet(self, direction: str, packet: Packet, note: str = "") -> None:
        summary = note or f"cmd=0x{packet.command_id:02X} flag=0x{packet.response_flag:02X}"
        if packet.payload_name:
            summary += f" {packet.payload_name}"
        item = {
            "time": round(time.time(), 3),
            "direction": direction,
            "size": len(packet.raw),
            "note": summary,
            "payload_name": packet.payload_name,
            "decoded_payload": packet.decoded_payload,
            "real_payload": packet.real_payload,
            "parse_warnings": packet.parse_warnings or [],
        }
        if self.debug_hex:
            item["hex"] = to_hex(packet.raw)
        self.recent_frames.append(item)
        self.logger.write("cloud-tcp", {**item, "hex": to_hex(packet.raw)})

    def record_event(self, channel: str, item: dict[str, Any]) -> None:
        self.logger.write(channel, item)

    def latest_session(self) -> CloudSession | None:
        live = [session for session in self.sessions if not session.writer.is_closing()]
        return live[-1] if live else None


def account_for_login(config: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    accounts = config.get("accounts", [])
    mobile = str(body.get("mobile", ""))
    password = str(body.get("password", ""))
    for account in accounts:
        if account.get("mobile") == mobile and account.get("password") == password:
            return account
    return accounts[0] if accounts else {}


def vehicle_by_vin(config: dict[str, Any], vin: str) -> dict[str, Any] | None:
    for vehicle in config.get("vehicles", []):
        if vehicle.get("vin") == vin:
            return vehicle
    return None


def user_menus(config: dict[str, Any]) -> list[dict[str, Any]]:
    menus = config.get("user_menus")
    if isinstance(menus, list) and menus:
        return menus
    return [
        {"menuName": "添加用户", "path": "admin"},
        {"menuName": "允许控车", "path": "driver"},
        {"menuName": "监控车辆", "path": "observer"},
    ]


def cloud_response_payload(config: dict[str, Any], packet: Packet, session: CloudSession) -> bytes:
    """按云端 TCP 命令构造响应 payload；只做联调必要业务，不做真实鉴权。"""

    command = packet.command_id
    payload = packet.payload
    if command == 0x09:
        # 登录响应：协议支持附带地图版本；配置默认关闭，匹配日志中的 2 字节响应。
        login_serial = payload[:2].ljust(2, b"\x00")
        if config.get("map", {}).get("include_versions_in_tcp"):
            return login_serial + pack_cloud_map_versions(config)
        return login_serial
    if command == 0x0A:
        return pack_vehicle_items(config.get("vehicles", []))
    if command == 0x0B:
        vin = payload[:17].decode("ascii", errors="ignore").rstrip("\x00")
        session.selected_vin = vin
        response = bytes([0x01, 0x01])
        if config.get("map", {}).get("include_versions_in_tcp"):
            response += pack_cloud_map_versions(config)
        return response
    if command == 0x0C:
        return payload[:2].ljust(2, b"\x00")
    if command in (0x0D, 0x0F, 0x10, 0x12):
        return b""
    return b""


def response_command_for(command_id: int) -> int:
    return command_id


async def handle_cloud_packet(state: SimulatorState, session: CloudSession, packet: Packet) -> None:
    session.unique_id = packet.unique_id
    session.last_seen = time.time()
    key = f"0x{packet.command_id:02X}"
    session.command_counts[key] = session.command_counts.get(key, 0) + 1
    state.record_packet("tcp.rx.cloud", packet)

    if packet.response_flag != RESP_COMMAND:
        return

    response_payload = cloud_response_payload(state.config, packet, session)
    frame = build_cloud_frame(
        response_command_for(packet.command_id),
        RESP_SUCCESS,
        packet.unique_id or state.config.get("cockpit", {}).get("id", ""),
        response_payload,
        device_type=CLOUD_DEVICE_COCKPIT,
    )
    session.writer.write(frame)
    await session.writer.drain()
    state.record_raw("tcp.tx.cloud", frame, f"cmd=0x{packet.command_id:02X} response")

    if packet.command_id == 0x0B:
        state.selected_vehicle_vin = session.selected_vin
        state.control_events.put_nowait(
            {
                "type": "control_request",
                "time": round(time.time(), 3),
                "vin": session.selected_vin,
                "unique_id": packet.unique_id,
            }
        )
    elif packet.command_id == 0x12:
        control_status = packet.payload[0] if packet.payload else None
        state.control_events.put_nowait(
            {
                "type": "control_status",
                "time": round(time.time(), 3),
                "vin": session.selected_vin or state.selected_vehicle_vin,
                "unique_id": packet.unique_id,
                "control_status": control_status,
            }
        )


async def cloud_tcp_client_loop(state: SimulatorState, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    session = CloudSession(reader, writer)
    state.sessions.append(session)
    buffer = bytearray()
    try:
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            buffer.extend(chunk)
            for packet in parse_cloud_frames(buffer):
                await handle_cloud_packet(state, session, packet)
    except Exception as exc:  # noqa: BLE001 - 联调服务要把协议错误写入状态页，便于排查。
        item = {"time": round(time.time(), 3), "direction": "tcp.error.cloud", "note": str(exc)}
        state.recent_frames.append(item)
        state.record_event("cloud-tcp", item)
    finally:
        writer.close()
        await writer.wait_closed()
        if session in state.sessions:
            state.sessions.remove(session)


async def start_cloud_tcp_server(app: FastAPI) -> None:
    state: SimulatorState = app.state.sim_state
    tcp_config = state.config.get("tcp", {})
    host = tcp_config.get("host", "127.0.0.1")
    port = int(tcp_config.get("port", 25300))
    state.tcp_server = await asyncio.start_server(
        lambda reader, writer: cloud_tcp_client_loop(state, reader, writer),
        host,
        port,
    )


async def stop_cloud_tcp_server(app: FastAPI) -> None:
    state: SimulatorState = app.state.sim_state
    if state.tcp_server:
        state.tcp_server.close()
        await state.tcp_server.wait_closed()


def parse_command_id(value: Any) -> int:
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text, 16) if text.lower().startswith("0x") else int(text)


def build_push_frame(state: SimulatorState, session: CloudSession, body: dict[str, Any]) -> bytes:
    """/__simulator/push 用于手动触发云端下行，比如遥控任务状态和车辆列表更新。"""

    command = body.get("command") or body.get("type") or body.get("command_id") or body.get("commandId")
    serial = int(body.get("serial", state.next_serial()))
    unique_id = session.unique_id or state.config.get("cockpit", {}).get("id", "")
    if command in ("remote_task_status", "task_status", "0x0E", 0x0E):
        payload = u16(serial) + bytes([int(body.get("status", 1)) & 0xFF])
        return build_cloud_frame(0x0E, RESP_COMMAND, unique_id, payload)
    if command in ("vehicle_list_update", "vehicles", "0x11", 0x11):
        payload = u16(serial) + pack_vehicle_items(state.config.get("vehicles", []))
        return build_cloud_frame(0x11, RESP_COMMAND, unique_id, payload)

    command_id = parse_command_id(command)
    payload_hex = str(body.get("payload_hex", body.get("payloadHex", ""))).replace("-", "").replace(" ", "")
    payload = bytes.fromhex(payload_hex) if payload_hex else b""
    return build_cloud_frame(command_id, RESP_COMMAND, unique_id, payload)


def create_app(config: dict[str, Any]) -> FastAPI:
    state = SimulatorState(config, bool(config.get("tcp", {}).get("debug_hex", False)))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # FastAPI 启动时同时拉起云端 TCP server，关闭时释放监听端口。
        app.state.sim_state = state
        await start_cloud_tcp_server(app)
        try:
            yield
        finally:
            await stop_cloud_tcp_server(app)

    app = FastAPI(title="本地云端模拟器", version="1.0.0", lifespan=lifespan)
    app.state.sim_state = state

    @app.middleware("http")
    async def log_http_request(request: Request, call_next):
        # HTTP 落盘只记录摘要，不读取 body，避免影响 FastAPI 后续处理请求体。
        started = time.time()
        response = await call_next(request)
        state.record_event(
            "http",
            {
                "method": request.method,
                "path": request.url.path,
                "query": request.url.query,
                "status_code": response.status_code,
                "duration_ms": round((time.time() - started) * 1000, 3),
                "client": request.client.host if request.client else None,
                "content_length": request.headers.get("content-length"),
            },
        )
        return response

    @app.post("/api/user/account/login")
    async def login(request: Request) -> JSONResponse:
        body = await request.json()
        if config.get("scenario", {}).get("force_auth_failure"):
            raise HTTPException(status_code=401, detail="simulated auth failure")
        account = account_for_login(config, body)
        state.last_token = account.get("token", "sim-access-token")
        return JSONResponse(
            envelope(
                {
                    "access_token": state.last_token,
                    "token_type": "bearer",
                    "refresh_token": account.get("refresh_token", "sim-refresh-token"),
                    "expires_in": int(account.get("expires_in", 259199)),
                    "scope": "all",
                    "user_id": account.get("user_id", "0"),
                    "jti": account.get("jti", "sim-jti"),
                    "userName": account.get("user_name", account.get("mobile", "sim-user")),
                }
            )
        )

    @app.post("/api/user/account/logout")
    async def logout() -> JSONResponse:
        return JSONResponse(envelope(True))

    @app.api_route("/api/metadata/cockpit/describeUserMenu", methods=["GET", "POST"])
    async def describe_user_menu() -> JSONResponse:
        return JSONResponse(envelope(user_menus(config)))

    @app.post("/api/metadata/role/DescribeRoles")
    async def describe_roles() -> JSONResponse:
        return JSONResponse(envelope([{"id": "sim-role-driver", "name": "远程驾驶员"}]))

    @app.post("/api/metadata/cockpit/createUser")
    async def create_user() -> JSONResponse:
        return JSONResponse(envelope(True))

    @app.post("/api/metadata/cockpit/updatePassword")
    async def update_password() -> JSONResponse:
        return JSONResponse(envelope(True))

    @app.post("/api/dispatch/mission/taskContinue/InitTaskContinue")
    async def init_task_continue() -> JSONResponse:
        vehicles = config.get("vehicles", [])
        return JSONResponse(
            envelope(
                {
                    "procedures": [{"id": 1, "name": "装载"}, {"id": 2, "name": "卸载"}],
                    "vehicles": vehicles,
                }
            )
        )

    @app.post("/api/dispatch/mission/taskContinue/InitListByProcedure")
    async def init_list_by_procedure() -> JSONResponse:
        return JSONResponse(envelope([{"id": 1, "name": "默认工步"}]))

    @app.post("/api/dispatch/mission/taskContinue/InitListByTargetArea")
    async def init_list_by_target_area() -> JSONResponse:
        return JSONResponse(envelope([{"id": 1, "name": "默认目标区域"}]))

    @app.post("/api/dispatch/mission/taskContinue/InitRelatedDeviceList")
    async def init_related_device_list() -> JSONResponse:
        return JSONResponse(envelope(config.get("vehicles", [])))

    @app.post("/api/dispatch/mission/taskContinue/TaskContinueExecute")
    async def task_continue_execute() -> JSONResponse:
        return JSONResponse(envelope({"success": True}))

    @app.post("/api/device/access/DescribeMineByVin")
    async def describe_mine_by_vin(request: Request) -> JSONResponse:
        body = await request.json()
        vin = str(body.get("vin", ""))
        vehicle = vehicle_by_vin(config, vin) or {}
        map_config = config.get("map", {})
        return JSONResponse(
            envelope(
                {
                    "mineId": vehicle.get("mine_id", map_config.get("mine_id")),
                    "mineName": map_config.get("mine_name"),
                    "mineIdentifier": map_config.get("mine_identifier"),
                }
            )
        )

    @app.api_route("/api/gis/business/hd/map/CurrentVersion", methods=["GET", "POST"])
    async def current_map_version() -> JSONResponse:
        map_config = config.get("map", {})
        return JSONResponse(
            envelope(
                {
                    "roadVersion": map_config.get("road_version"),
                    "areaVersion": map_config.get("area_version"),
                    "baseUrl": map_config.get("base_url"),
                }
            )
        )

    @app.get("/static/map/{path:path}")
    async def static_map(path: str) -> PlainTextResponse:
        return PlainTextResponse(f"simulated map file: {path}\n")

    @app.get("/__simulator/status")
    async def simulator_status() -> JSONResponse:
        return JSONResponse(
            {
                "http": config.get("http", {}),
                "tcp": config.get("tcp", {}),
                "sessions": [session.summary() for session in state.sessions],
                "selected_vehicle_vin": state.selected_vehicle_vin,
                "last_token": state.last_token,
                "vehicles": config.get("vehicles", []),
                "recent_frames": list(state.recent_frames),
                "logging": config.get("logging", {}),
            }
        )

    @app.post("/__simulator/push")
    async def simulator_push(request: Request) -> JSONResponse:
        body = await request.json()
        session = state.latest_session()
        if not session:
            raise HTTPException(status_code=409, detail="no active cloud TCP session")
        frame = build_push_frame(state, session, body)
        session.writer.write(frame)
        await session.writer.drain()
        state.record_raw("tcp.tx.cloud.push", frame, "manual push")
        return JSONResponse({"ok": True, "hex": to_hex(frame)})

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def api_fallback(path: str) -> JSONResponse:
        # 驾驶舱偶尔会访问文档之外的辅助接口，兜底返回空 data，避免联调流程被 404 打断。
        return JSONResponse(envelope({"path": f"/api/{path}", "mock": True}))

    return app


def apply_overrides(config: dict[str, Any], args: argparse.Namespace) -> None:
    config.setdefault("http", {})
    config.setdefault("tcp", {})
    if args.http_host:
        config["http"]["host"] = args.http_host
    if args.http_port:
        config["http"]["port"] = args.http_port
    if args.tcp_host:
        config["tcp"]["host"] = args.tcp_host
    if args.tcp_port:
        config["tcp"]["port"] = args.tcp_port
    if args.debug_hex is not None:
        config["tcp"]["debug_hex"] = args.debug_hex
    config.setdefault("logging", {})
    if args.log_dir:
        config["logging"]["dir"] = args.log_dir
    if args.file_log is not None:
        config["logging"]["enabled"] = args.file_log
    config["logging"].setdefault("dir", "logs")
    config["logging"].setdefault("enabled", True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="云端 HTTP + TCP 模拟器")
    parser.add_argument("--config", default="simulator.json", help="配置文件路径")
    parser.add_argument("--http-host", help="HTTP 监听地址")
    parser.add_argument("--http-port", type=int, help="HTTP 监听端口")
    parser.add_argument("--tcp-host", help="云端 TCP 监听地址")
    parser.add_argument("--tcp-port", type=int, help="云端 TCP 监听端口")
    parser.add_argument("--debug-hex", action=argparse.BooleanOptionalAction, default=None, help="状态页是否展示十六进制帧")
    parser.add_argument("--log-dir", help="落盘日志目录，默认 logs")
    parser.add_argument("--file-log", action=argparse.BooleanOptionalAction, default=None, help="是否启用落盘 JSONL 日志")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config)
    config = load_config(str(config_path))
    apply_overrides(config, args)
    app = create_app(config)
    http_config = config.get("http", {})
    uvicorn.run(app, host=http_config.get("host", "127.0.0.1"), port=int(http_config.get("port", 18080)))


if __name__ == "__main__":
    main()
