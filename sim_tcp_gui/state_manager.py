import json
import os
import threading
import time
from pathlib import Path

import dynamics_model
import enabler_connection
from hil_client import HilTcpClient
from hil_protocol import ALL_SIGNAL_NAMES
from vehicle_protocol import VehicleReportMapper, build_vehicle_realtime_frame
from vehicle_publisher import VehicleReportPublisher
from dds_bridge import DdsBridge, DdsSnapshotMapper
from vehicle_feedback import VehicleFeedback


DEFAULT_BRIDGE_CONFIG = Path(__file__).resolve().with_name("bridge_config.json")


def load_bridge_config(path=None):
    config_path = Path(path or os.environ.get("SIM_BRIDGE_CONFIG", DEFAULT_BRIDGE_CONFIG))
    if not config_path.exists():
        return {"hil": {"enabled": True}, "vehicle_output": {"enabled": False}}
    return json.loads(config_path.read_text(encoding="utf-8"))


class StateManager(object):
    """Application state plus decoupled protocol adapters."""

    def __init__(
        self,
        bridge_config=None,
        auto_start=True,
        dynamics=None,
        hil_client_factory=HilTcpClient,
        publisher_factory=VehicleReportPublisher,
        telemetry_server_factory=enabler_connection.TcpTelemetryServer,
    ):
        """先加载默认反馈及环境文件再启动适配器；环境启动错误直接报出，auto_start 控制通信启动。"""
        self.dynamics_model = dynamics or dynamics_model.DynamicsModel()
        self.bridge_config = bridge_config if bridge_config is not None else load_bridge_config()
        self._bridge_lock = threading.RLock()
        self.hil_signals = {name: 0 for name in ALL_SIGNAL_NAMES}
        self._dds_wire_feedback = {}
        self._json_wire_feedback = {}
        self._json_feedback_expires = 0.0
        self.connection = None

        vehicle_config = self.bridge_config.get("vehicle_output", {})
        self.vehicle_mapper = VehicleReportMapper(vehicle_config)
        self.hil_client = hil_client_factory(self.bridge_config.get("hil", {}), self.apply_hil_signals)
        self.vehicle_publisher = publisher_factory(vehicle_config, self.vehicle_report)
        dds_config = self.bridge_config.get("dds_output", {})
        self.vehicle_feedback = VehicleFeedback(dds_config)
        self.dds_mapper = DdsSnapshotMapper(dds_config, self.dynamics_model.snapshot())
        self.dds_bridge = DdsBridge(dds_config, self.dds_snapshot, self.apply_dds_control)
        # 遥测工厂可能立即在线程中调用 snapshot，此时各适配器必须已经存在。
        self.connection = telemetry_server_factory(self.snapshot, self.update_controls)
        if auto_start:
            self.hil_client.start()
            self.vehicle_publisher.start()
            self.dds_bridge.start()

    @property
    def tcp_port(self):
        return self.connection.port

    @property
    def local_ip(self):
        return self.connection.local_ip

    def snapshot(self):
        """汇总动力学、通信及有效反馈，额外返回环境热加载状态、条目数和独立错误信息。"""
        snapshot = self.dynamics_model.snapshot()
        if self.connection is None:
            snapshot["tcp"] = {"host": "", "port": 50001, "clients": 0, "received": []}
        else:
            snapshot["tcp"] = {
                "host": self.local_ip,
                "port": self.tcp_port,
                "clients": len(self.connection.clients),
                "received": list(reversed(self.connection.received)),
            }

        report = self._convert_vehicle_report(snapshot)
        vin = self.bridge_config.get("vehicle_output", {}).get("vin", "")
        frame = build_vehicle_realtime_frame(vin, report)
        snapshot["hil"] = {
            **self.hil_client.status(),
            "signals": self.hil_signal_snapshot(),
        }
        snapshot["vehicle_protocol"] = {
            **self.vehicle_publisher.status(),
            "vin": vin,
            "report": report,
            "frame_hex": frame.hex(" ").upper(),
            "frame_length": len(frame),
        }
        snapshot["dds"] = self.dds_bridge.status()
        snapshot["vehicle_feedback"] = self.vehicle_feedback_payload()
        snapshot["environment_config"] = self.dds_mapper.environment.status(snapshot)
        return snapshot

    def vehicle_feedback_payload(self):
        """在反馈锁内返回默认配置及有效 JSON/DDS 反馈，用于网页回显。"""
        with self._bridge_lock:
            return self.vehicle_feedback.payload(self._feedback_overrides())

    def update_vehicle_feedback(self, payload):
        """持久保存网页配置并返回有效状态；参数或磁盘错误交由 HTTP 层处理。"""
        with self._bridge_lock:
            self.vehicle_feedback.update(payload)
            return self.vehicle_feedback.payload(self._feedback_overrides())

    def _feedback_overrides(self):
        """由持有反馈锁的调用方合并控制反馈；JSON 超时后自动释放覆盖，回到 DDS 或网页默认。"""
        if time.monotonic() >= self._json_feedback_expires:
            self._json_wire_feedback.clear()
        return {**self._dds_wire_feedback, **self._json_wire_feedback}

    def apply_hil_signals(self, signals):
        """保留全部 HIL 信号；选择 HIL 控制源时才更新动力学，避免和 DDS 遥控争用输入。"""

        with self._bridge_lock:
            for name in ALL_SIGNAL_NAMES:
                if name in signals:
                    self.hil_signals[name] = int(signals[name])
            current = dict(self.hil_signals)

        dds_config = self.bridge_config.get("dds_output", {})
        if dds_config.get("enabled") and dds_config.get("control_source", "remotectl") == "remotectl":
            return

        controls = {
            "throttle": current["Throttle"],
            "brake": current["Brake"],
            "steering": current["Steering"],
            "parking_brake": bool(current["J1"]),
            "ignition": "run" if current["X1"] else "off",
        }
        if current["S2-R"]:
            controls["gear"] = "reverse"
        elif current["S2-D"]:
            controls["gear"] = "drive"
        elif current["S2-N"]:
            controls["gear"] = "neutral"
        self.dynamics_model.update_controls(controls)

    def hil_signal_snapshot(self):
        with self._bridge_lock:
            return dict(self.hil_signals)

    def _convert_vehicle_report(self, dynamics_snapshot):
        return self.vehicle_mapper.convert(dynamics_snapshot, self.hil_signal_snapshot())

    def vehicle_report(self):
        return self._convert_vehicle_report(self.dynamics_model.snapshot())

    def vehicle_protocol_payload(self):
        report = self.vehicle_report()
        vin = self.bridge_config.get("vehicle_output", {}).get("vin", "")
        frame = build_vehicle_realtime_frame(vin, report)
        return {
            "vin": vin,
            "report": report,
            "frame_hex": frame.hex(" ").upper(),
            "frame_length": len(frame),
        }

    def close(self):
        """依次停止通信适配器，释放 DDS 子进程和管道。"""
        self.hil_client.stop()
        self.vehicle_publisher.stop()
        self.dds_bridge.stop()

    def dds_snapshot(self):
        """生成原 DDS 样本；有效 JSON/DDS 控制逐字段覆盖网页模式与 MPa 气压。"""
        sample = self.dds_mapper.convert(self.dynamics_model.snapshot(), self.hil_signal_snapshot())
        with self._bridge_lock:
            self.vehicle_feedback.apply(sample["wire"], self._feedback_overrides())
        return sample

    def apply_dds_control(self, event):
        """将 DDS JSON 事件转换为动力学及反馈；退出或超时清除覆盖并施加制动。"""
        config = self.bridge_config.get("dds_output", {})
        if config.get("control_source", "remotectl") != "remotectl":
            return
        active = (event.get("event") == "control" and event.get("Remote_enable") == 3
                  and event.get("remote_unman_flag") == 8 and event.get("unmanned_enable"))
        if not active:
            with self._bridge_lock:
                # 退出后释放模式和开关覆盖，恢复网页默认值而非强行覆盖为人工。
                self._dds_wire_feedback.clear()
            self.dynamics_model.update_controls({"throttle": 0, "brake": 100, "parking_brake": True})
            return
        # 油车使用 J1939 请求码，电/混车型在原业务中使用 0/1/2，不能混为反馈档位。
        gears = {0xDF: "reverse", 0xFC: "drive", 0x7D: "neutral"} if config.get("vehicle_type", 1) == 1 else {
            2: "reverse", 1: "drive", 0: "neutral"}
        controls = {"throttle": max(0, min(100, float(event["throttle"]))),
                    "brake": max(0, min(100, float(event["brake"]))),
                    "steering": float(event["wheel_angle"]) * self.dds_mapper.ratio,
                    # 原业务在驻车按键及急停时设置 parking_req=true，按其实际输出模拟。
                    "parking_brake": bool(event["parking_request"])}
        # on_start_enable 在旧遥控实现中固定为 false，并不是点火状态，保留动力学已有点火设置。
        if event["gear"] in gears:
            controls["gear"] = gears[event["gear"]]
        self.dynamics_model.update_controls(controls)
        with self._bridge_lock:
            self._dds_wire_feedback = dict(event.get("wire_switches", {}), vcu_state=5, Remote_or_Unman=8)

    def update_controls(self, payload):
        """接受 Web/TCP JSON 控制；模式和气压临时覆盖网页默认，动力学字段沿用现有处理。"""
        overrides = self.vehicle_feedback.parse_override(payload)
        release = payload.get("release_feedback", False)
        if not isinstance(release, bool):
            raise ValueError("release_feedback 必须是布尔值")
        with self._bridge_lock:
            # 所有反馈字段先校验，避免反馈错误时仍修改动力学控制量。
            snapshot = self.dynamics_model.update_controls(payload)
            if release:
                self._json_wire_feedback.clear()
            for key, value in overrides.items():
                if value is None:
                    self._json_wire_feedback.pop(key, None)
                else:
                    self._json_wire_feedback[key] = value
            if overrides:
                timeout = float(self.bridge_config.get("dds_output", {}).get("control_timeout", 1.0))
                self._json_feedback_expires = time.monotonic() + max(0.05, timeout)
            snapshot["vehicle_feedback"] = self.vehicle_feedback.payload(self._feedback_overrides())
            return snapshot

    def map_payload(self):
        return self.dynamics_model.map_payload()

    def brush_elevation(self, payload):
        return self.dynamics_model.brush_elevation(payload)

    def import_map(self, payload):
        return self.dynamics_model.import_map(payload)

    def update_map_settings(self, payload):
        return self.dynamics_model.update_map_settings(payload)

    def add_map_object(self, payload):
        return self.dynamics_model.add_map_object(payload)

    def update_map_object(self, object_id, payload):
        return self.dynamics_model.update_map_object(object_id, payload)

    def delete_map_object(self, object_id):
        return self.dynamics_model.delete_map_object(object_id)

    def reset_map(self):
        return self.dynamics_model.reset_map()

    def response_config(self):
        return self.dynamics_model.response_config_payload()

    def update_response_config(self, payload):
        return self.dynamics_model.update_response_config(payload)

    def set_paused(self, paused):
        return self.dynamics_model.set_paused(paused)
