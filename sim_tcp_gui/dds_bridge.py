"""使用原生 Fast DDS 子进程对接 remotectl；JSON 仅经过本地标准输入输出管道。"""

import json
import ipaddress
import math
import os
from pathlib import Path
import subprocess
import threading
import time
from environment_config import EnvironmentConfig


class DdsSnapshotMapper:
    """将动力学与 HIL 数据映射到原 IDL 物理量，不套用 TCP 字节缩放。"""

    def __init__(self, config, initial_snapshot=None):
        """保存车型、传动比与环境加载器；独立环境文件启动时校验，不修改传入配置。"""
        self.config = dict(config)
        self.ratio = float(self.config.get("steering_ratio", 12.0))
        if not math.isfinite(self.ratio) or self.ratio <= 0:
            raise ValueError("dds_output.steering_ratio must be positive")
        if self.config.get("enabled"):
            host, port = self.config["cockpit_address"].rsplit(":", 1)
            ipaddress.IPv4Address(host)
            if not 1 <= int(port) <= 65535 or len(self.config["cockpit_address"]) > 21:
                raise ValueError("cockpit_address must be IPv4:port, max 21 bytes")
            if self.config.get("vehicle_type", 1) not in (1, 2, 3):
                raise ValueError("vehicle_type must be 1, 2 or 3")
            if self.config.get("control_source", "remotectl") not in ("remotectl", "hil"):
                raise ValueError("control_source must be remotectl or hil")
        self.environment = EnvironmentConfig(config, initial_snapshot)

    def convert(self, snapshot, signals):
        """返回 DDS 物理量快照；环境文件热加载并逐帧将相对周边车辆转换为地理位置。"""
        vehicle, controls = snapshot["vehicle"], snapshot["controls"]
        heading = math.radians(vehicle["heading"])
        speed = vehicle["signed_speed_mps"]
        environment = self.environment.sample(snapshot)
        gear = {"reverse": -1, "neutral": 0, "park": 0, "drive": 2,
                "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}[controls["gear"]]
        wire = dict(self.config.get("wire_defaults", {}))
        wire.update({
            "tachograph_vehicle_speed": vehicle["speed_kph"],
            "steering_left_angle_info": controls["steering"] / self.ratio,
            "brake_pedal_SYMC": controls["brake"],
            "accel_pedal_position": controls["throttle"],
            "actual_engine_torque": controls["throttle"],
            "engine_speed": int(vehicle["engine_rpm"]),
            # IDL 明确规定为米；保留旧 TCP 层的车型处理，不能在 DDS 入口重复缩放。
            "total_vehicle_distance": vehicle["distance"],
            "trans_current_gear": gear,
            "parking": bool(controls["parking_brake"]),
            "vcu_state": 0,
            "Remote_or_Unman": 0,
        })
        for field, signal in {
            "left_turn_start_status": "X3-1", "right_turn_start_status": "X3-2",
            "low_light_start_status": "X3-3", "high_beam_start_status": "X4",
            "outline_lamp_start_status": "X5-2", "wiper_start_status": "X6-1",
            "wiper_slow_start_status": "X6-2", "horn_start_status": "X6-3",
        }.items():
            wire[field] = bool(signals.get(signal, 0))
        return {
            "address": self.config["cockpit_address"],
            "sequence": int(self.config.get("address_sequence", 1)),
            "wire": wire,
            "gps": {"gps_lat": vehicle["latitude"], "gps_lng": vehicle["longitude"],
                    "gps_heading": vehicle["heading"], "gps_pitch": vehicle["pitch"],
                    "gps_speed": vehicle["speed_kph"], "gps_ve": speed * math.sin(heading),
                    "gps_vn": speed * math.cos(heading), "gps_height": vehicle["elevation_m"]},
            "safe": dict(self.config.get("safe_feedback", {})),
            "curb": dict(self.config.get("curb_feedback", {})),
            "front": {"objects": environment["front_obstacles"]},
            "back": {"objects": environment["back_obstacles"]},
            "v2v": {"vehicles": environment["round_vehicles"]},
        }


class DdsBridge:
    """启动原生端点、推送快照并接收控制事件，状态可用于判断实际 DDS 发布与匹配。"""

    def __init__(self, config, snapshot_provider, control_receiver):
        """保存快照提供者和控制回调，初始化线程同步对象；构造时不启动外部进程。"""
        self.config = dict(config)
        self.snapshot_provider = snapshot_provider
        self.control_receiver = control_receiver
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._process = None
        self._thread = None
        self._reader = None
        self._log = None
        self._last_control = None
        self._state = {"enabled": bool(config.get("enabled", False)), "ready": False,
                       "published": 0, "last_error": "", "address_ack": None,
                       "matched_wire": 0, "matched_address": 0, "last_control": None}

    def status(self):
        """返回状态副本；published 只统计原生进程确认成功发布的快照。"""
        with self._lock:
            return dict(self._state)

    def start(self):
        """在启用且未运行时启动后台监督线程；缺少可执行文件会记录错误并定期重试。"""
        if not self._state["enabled"] or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="DDS-Bridge", daemon=True)
        self._thread.start()

    def stop(self):
        """停止快照线程并终止原生进程，关闭管道与日志，等待事件读取线程退出。"""
        self._stop.set()
        process = self._process
        if process and process.poll() is None:
            try:
                # 先结束读管道的进程，解除监督线程可能阻塞的 stdin 写入，再等待线程退出。
                process.terminate()
            except OSError:
                pass  # 进程可能已在监督线程中退出，后续清理仍负责关闭管道。
        if self._thread:
            self._thread.join()
        self._cleanup()

    def _cleanup(self):
        """先结束进程再关闭管道，避免读取线程永远阻塞在 stdout。"""
        process = self._process
        self._process = None
        if process:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
            if self._reader:
                self._reader.join(timeout=2.0)
            process.stdin.close()
            process.stdout.close()
        if self._log:
            self._log.close()
            self._log = None
        with self._lock:
            self._state.update(ready=False, matched_wire=0, matched_address=0)

    def _spawn(self):
        """按配置启动原生 DDS 端点，共享 Topic 配置路径通过环境变量传递。"""
        base = Path(__file__).resolve().parent
        executable = Path(self.config.get("executable", "native/build/bin/sim-dds-bridge.exe"))
        if not executable.is_absolute():
            executable = base / executable
        topic_config = Path(self.config.get("topics_config", "dds_topics.json"))
        if not topic_config.is_absolute():
            topic_config = base / topic_config
        env = dict(os.environ, REMOTECTL_DDS_CONFIG=str(topic_config.resolve()))
        self._log = (base / "dds_bridge.log").open("a", encoding="utf-8")
        self._process = subprocess.Popen(
            [str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._log, text=True, encoding="utf-8", bufsize=1, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self._reader = threading.Thread(target=self._read_events, args=(self._process,), daemon=True)
        self._reader.start()

    def _read_events(self, process):
        """串行解析原生端点事件，将控制交给状态管理器；格式错误写入状态并继续读。"""
        try:
            for line in process.stdout:
                if self._stop.is_set():
                    break
                try:
                    event = json.loads(line)
                    kind = event.get("event")
                    with self._lock:
                        if kind == "ready":
                            self._state.update(ready=True, last_error="")
                        elif kind == "published":
                            self._state.update(published=self._state["published"] + 1,
                                               matched_wire=event["matched_wire"],
                                               matched_address=event["matched_address"], last_error="")
                        elif kind == "address_ack":
                            self._state["address_ack"] = event["sequence"]
                        elif kind == "error":
                            self._state["last_error"] = event["message"]
                        elif kind == "control":
                            self._last_control = time.monotonic()
                            self._state["last_control"] = dict(event)
                    if kind == "control":
                        self.control_receiver(event)
                except (ValueError, KeyError, TypeError) as error:
                    with self._lock:
                        self._state["last_error"] = str(error)
        finally:
            with self._lock:
                self._state["ready"] = False

    def _run(self):
        """周期推送快照并监督进程；遥控超时后触发一次仿真制动，进程故障后重启。"""
        interval = max(0.02, float(self.config.get("publish_interval", 0.05)))
        while not self._stop.is_set():
            try:
                if not self._process or self._process.poll() is not None:
                    self._cleanup()
                    self._spawn()
                sample = self.snapshot_provider()
                # 禁止 NaN/Infinity 穿过管道，避免无效物理量进入生成类型的数值转换。
                self._process.stdin.write(json.dumps(sample, allow_nan=False, separators=(",", ":")) + "\n")
                self._process.stdin.flush()
                with self._lock:
                    stale = self._last_control is not None and time.monotonic() - self._last_control > float(self.config.get("control_timeout", 1.0))
                    if stale:
                        self._last_control = None
                if stale:
                    self.control_receiver({"event": "timeout"})
                self._stop.wait(interval)
            except (OSError, ValueError, KeyError, TypeError) as error:
                with self._lock:
                    self._state["last_error"] = str(error)
                self.control_receiver({"event": "timeout"})
                self._cleanup()
                self._stop.wait(1.0)
