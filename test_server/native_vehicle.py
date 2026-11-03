"""由舱云控车事件启动真实 remotectl，保留现有云端 HTTP/TCP 协议。"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
import json
import os
from pathlib import Path
from typing import Any


async def run_native_vehicle(config: dict[str, Any], vehicle: dict[str, Any]) -> None:
    """为所选 VIN 生成车辆 YAML 并运行原生车端；任务取消时终止子进程，避免遗留重复 DDS 发布者。"""
    backend = config["native_vehicle"]
    vin = str(vehicle["vin"])
    iccid = str(vehicle.get("iccid", ""))
    if len(vin) != 17 or not vin.isascii() or not vin.isalnum():
        raise ValueError("native vehicle VIN must be 17 ASCII letters/digits")
    if not 1 <= len(iccid) <= 20 or not iccid.isascii():
        raise ValueError("native vehicle ICCID must contain 1..20 ASCII characters")
    runtime = Path(backend["runtime_dir"]).resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    yaml_path = runtime / f"vehicle-{vin}.yaml"
    # JSON 字符串语法可作为 YAML 双引号标量，防止车辆标识中的特殊字符改变配置结构。
    yaml_path.write_text(
        f"vehicle:\n  vehicle_VIN: {json.dumps(vin)}\n  vehicle_ICCID: {json.dumps(iccid)}\n"
        f"  vehicle_type: {int(backend.get('vehicle_type', 1))}\n"
        "system:\n  currentPart: sim\n", encoding="utf-8")
    env = dict(os.environ, REMOTECTL_VEHICLE_CONFIG=str(yaml_path),
               REMOTECTL_DDS_CONFIG=str(Path(backend["dds_config"]).resolve()),
               REMOTECTL_REMOTE_CONFIG=str(Path(backend["remote_config"]).resolve()))
    process = None
    # 持久部署交给 journald 轮转和限流；独立测试保留每个 VIN 的原始日志文件。
    log_context = nullcontext(None) if backend.get("log_mode") == "journal" else (
        runtime / f"vehicle-{vin}.log").open("a", encoding="utf-8")
    with log_context as log:
        try:
            process = await asyncio.create_subprocess_exec(str(Path(backend["executable"]).resolve()),
                                                           env=env, stdout=log, stderr=log)
            print(f"原生车端启动 vin={vin} pid={process.pid}", flush=True)
            code = await process.wait()
            if code:
                raise RuntimeError(f"native remotectl exited with code {code}; see vehicle logs or journal")
        finally:
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            print(f"原生车端结束 vin={vin}", flush=True)
