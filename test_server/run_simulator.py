from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

import uvicorn

from cloud_server import apply_overrides as apply_cloud_overrides
from cloud_server import build_push_frame, create_app
from vehicle_client import VehicleClient, find_vehicle


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def vehicle_args(config: dict[str, Any], args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        debug_hex=args.debug_hex if args.debug_hex is not None else config.get("tcp", {}).get("debug_hex", False),
        file_log=args.file_log,
        log_dir=args.log_dir,
        once=args.vehicle_once,
        reconnect_interval=args.reconnect_interval,
        report_interval=args.report_interval,
        vehicle_length_endian=args.vehicle_length_endian,
    )


def cockpit_endpoint(config: dict[str, Any], args: argparse.Namespace) -> tuple[str, int]:
    cockpit = config.get("cockpit", {})
    host = args.cockpit_host or cockpit.get("ip", "127.0.0.1")
    port = int(args.cockpit_port or cockpit.get("port", 60006))
    return host, port


def log_task_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        print(f"{task.get_name()} 异常退出：{exc}")


async def push_remote_task_status_later(app, delay_seconds: float, status: int) -> None:
    await asyncio.sleep(delay_seconds)
    state = app.state.sim_state
    session = state.latest_session()
    if not session:
        print("未找到舱云 TCP session，跳过自动下发 0x0E 遥控任务状态")
        return
    frame = build_push_frame(
        state,
        session,
        {
            "command": "remote_task_status",
            "status": status,
        },
    )
    session.writer.write(frame)
    await session.writer.drain()
    state.record_raw("tcp.tx.cloud.push", frame, "auto remote task status 0x0E")
    print(f"已自动下发 0x0E 遥控任务状态 status={status}")


async def vehicle_runner(
    app,
    config: dict[str, Any],
    args: argparse.Namespace,
    running: dict[str, asyncio.Task],
    vin: str,
) -> None:
    if not vin:
        print("控制事件没有 VIN，无法启动车端")
        return
    task = running.get(vin)
    if task and not task.done():
        print(f"车端 {vin} 已在运行，忽略重复触发")
        return

    try:
        vehicle = find_vehicle(config, vin)
    except SystemExit as exc:
        print(str(exc))
        return

    host, port = cockpit_endpoint(config, args)
    client = VehicleClient(config, vehicle, vehicle_args(config, args))
    task = asyncio.create_task(client.run_forever(host, port), name=f"vehicle-{vin}")
    running[vin] = task
    task.add_done_callback(lambda done_task: running.pop(vin, None) if running.get(vin) is done_task else None)
    task.add_done_callback(log_task_failure)
    print(f"已触发车端 {vin} 连接驾驶舱 {host}:{port}")

    if args.auto_remote_task_status:
        asyncio.create_task(
            push_remote_task_status_later(app, args.remote_task_delay, args.remote_task_status),
            name="auto-remote-task-status",
        )


async def control_event_loop(app, config: dict[str, Any], args: argparse.Namespace) -> None:
    state = app.state.sim_state
    running: dict[str, asyncio.Task] = {}
    pending_vin = args.vehicle or ""
    try:
        while True:
            event = await state.control_events.get()
            event_type = event.get("type")
            vin = str(event.get("vin") or pending_vin)
            if event_type == "control_request":
                pending_vin = vin
                print(f"收到 0x0B 请求控制车辆 vin={vin}")
                if args.vehicle_trigger == "control-request":
                    await vehicle_runner(app, config, args, running, vin)
            elif event_type == "control_status":
                pending_vin = vin or pending_vin
                print(f"收到 0x12 控车状态上报 vin={pending_vin} status={event.get('control_status')}")
                if args.vehicle_trigger == "control-status":
                    await vehicle_runner(app, config, args, running, pending_vin)
    finally:
        for task in running.values():
            task.cancel()
        if running:
            await asyncio.gather(*running.values(), return_exceptions=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="一键启动云端 HTTP/TCP，并在控车流程中自动启动车端 TCP client")
    parser.add_argument("--config", default="simulator.json", help="配置文件路径")
    parser.add_argument("--http-host", help="HTTP 监听地址")
    parser.add_argument("--http-port", type=int, help="HTTP 监听端口")
    parser.add_argument("--tcp-host", help="云端 TCP 监听地址")
    parser.add_argument("--tcp-port", type=int, help="云端 TCP 监听端口")
    parser.add_argument("--debug-hex", action=argparse.BooleanOptionalAction, default=None, help="打印/展示十六进制帧")
    parser.add_argument("--log-dir", help="落盘日志目录，默认 logs")
    parser.add_argument("--file-log", action=argparse.BooleanOptionalAction, default=None, help="是否启用落盘 JSONL 日志")
    parser.add_argument("--vehicle", help="兜底车辆 VIN；控制帧没有 VIN 时使用")
    parser.add_argument("--vehicle-trigger", choices=["control-request", "control-status"], default="control-request", help="启动车端的触发点")
    parser.add_argument("--cockpit-host", help="驾驶舱车端 TCP 地址")
    parser.add_argument("--cockpit-port", type=int, help="驾驶舱车端 TCP 端口")
    parser.add_argument("--vehicle-length-endian", choices=["auto", "big", "little"], default="auto", help="入站车端帧长度字段解析方式")
    parser.add_argument("--report-interval", type=float, default=0.2, help="车端实时上报周期，单位秒")
    parser.add_argument("--reconnect-interval", type=float, default=3.0, help="车端断线重连间隔，单位秒")
    parser.add_argument("--vehicle-once", action="store_true", help="车端连接断开后不重连")
    parser.add_argument("--auto-remote-task-status", action=argparse.BooleanOptionalAction, default=True, help="车端触发后自动下发 0x0E 遥控任务状态")
    parser.add_argument("--remote-task-delay", type=float, default=0.2, help="自动下发 0x0E 前等待秒数")
    parser.add_argument("--remote-task-status", type=int, default=1, help="自动下发 0x0E 的状态值")
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    config = load_config(str(Path(args.config)))
    apply_cloud_overrides(config, args)
    app = create_app(config)
    http_config = config.get("http", {})
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=http_config.get("host", "127.0.0.1"),
            port=int(http_config.get("port", 18080)),
        )
    )
    control_task = asyncio.create_task(control_event_loop(app, config, args), name="control-event-loop")
    try:
        await server.serve()
    finally:
        control_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await control_task


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main_async())


if __name__ == "__main__":
    main()
