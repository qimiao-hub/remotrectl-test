import json
import os
import threading
from pathlib import Path

import dynamics_model
import enabler_connection
from hil_client import HilTcpClient
from hil_protocol import ALL_SIGNAL_NAMES
from vehicle_protocol import VehicleReportMapper, build_vehicle_realtime_frame
from vehicle_publisher import VehicleReportPublisher


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
        self.dynamics_model = dynamics or dynamics_model.DynamicsModel()
        self.bridge_config = bridge_config if bridge_config is not None else load_bridge_config()
        self._bridge_lock = threading.RLock()
        self.hil_signals = {name: 0 for name in ALL_SIGNAL_NAMES}
        self.connection = telemetry_server_factory(
            self.snapshot,
            self.update_controls,
        )

        vehicle_config = self.bridge_config.get("vehicle_output", {})
        self.vehicle_mapper = VehicleReportMapper(vehicle_config)
        self.hil_client = hil_client_factory(self.bridge_config.get("hil", {}), self.apply_hil_signals)
        self.vehicle_publisher = publisher_factory(vehicle_config, self.vehicle_report)
        if auto_start:
            self.hil_client.start()
            self.vehicle_publisher.start()

    @property
    def tcp_port(self):
        return self.connection.port

    @property
    def local_ip(self):
        return self.connection.local_ip

    def snapshot(self):
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
        return snapshot

    def apply_hil_signals(self, signals):
        """Translate protocol signals to domain controls.

        All HIL-only fields remain available under ``snapshot()['hil']['signals']``
        while DynamicsModel receives only controls it understands.
        """

        with self._bridge_lock:
            for name in ALL_SIGNAL_NAMES:
                if name in signals:
                    self.hil_signals[name] = int(signals[name])
            current = dict(self.hil_signals)

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
        self.hil_client.stop()
        self.vehicle_publisher.stop()

    def update_controls(self, payload):
        return self.dynamics_model.update_controls(payload)

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
