import sys
import unittest
from datetime import datetime
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from hil_protocol import ALL_SIGNAL_NAMES, build_read_request, decode_state_frame
from vehicle_protocol import (
    REPORT_DEFAULTS,
    VehicleReportMapper,
    build_realtime_report_payload,
    build_vehicle_realtime_frame,
    xor_bcc,
)
from state_manager import StateManager


class HilProtocolTests(unittest.TestCase):
    def test_read_request_and_full_state_decode(self):
        self.assertEqual(build_read_request(0x0C, 7), bytes((0x0C, 0x00, 7, 4)))
        frame = bytearray(14)
        frame[:4] = bytes((0x0C, 0x00, 7, 14))
        frame[4] = 0b01000100  # D + B1
        frame[7] = 0b11000001  # ignition + both indicators
        frame[9:11] = (-120).to_bytes(2, "little", signed=True)
        frame[11] = 35
        frame[12] = 8
        decoded = decode_state_frame(bytes(frame))
        self.assertEqual(decoded.signals["S2-D"], 1)
        self.assertEqual(decoded.signals["B1"], 1)
        self.assertEqual(decoded.signals["X1"], 1)
        self.assertEqual(decoded.signals["X5-1"], 1)
        self.assertEqual(decoded.signals["X5-2"], 1)
        self.assertEqual(decoded.signals["Steering"], -120)
        self.assertEqual(decoded.signals["Throttle"], 35)
        self.assertEqual(decoded.signals["Brake"], 8)
        self.assertEqual(set(decoded.signals), set(ALL_SIGNAL_NAMES))


class VehicleProtocolTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "controls": {
                "throttle": 35,
                "brake": 8,
                "steering": -120,
                "gear": "drive",
                "parking_brake": False,
                "ignition": "run",
            },
            "vehicle": {
                "speed_kph": 12.4,
                "engine_rpm": 1500,
                "heading": 3.2,
                "pitch": 4.1,
                "distance": 1250,
            },
        }

    def test_mapping_creates_every_vehicle_report_field(self):
        signals = {name: 0 for name in ALL_SIGNAL_NAMES}
        signals.update({"X5-1": 1, "A1": 1, "按键面板掉线故障": 1})
        report = VehicleReportMapper({"steering_ratio": 12}).convert(self.snapshot, signals)
        self.assertEqual(set(report), set(REPORT_DEFAULTS))
        self.assertEqual(report["speed_deci_kmh"], 124)
        self.assertEqual(report["heading_raw"], 32)
        self.assertEqual(report["wheel_center_raw"], 300)
        self.assertEqual(report["engine_speed_raw"], 12000)
        self.assertEqual(report["left_switch"] & (1 << 4), 1 << 4)
        self.assertEqual(report["button_operation1"], 1)
        self.assertEqual(report["fault_info1"], 1)

    def test_binary_frame_matches_vehicle_protocol_shape_and_bcc(self):
        report = VehicleReportMapper().convert(self.snapshot, {})
        now = datetime(2026, 7, 27, 12, 34, 56, 789000)
        payload = build_realtime_report_payload(report, now)
        frame = build_vehicle_realtime_frame("SYZKRMCAB25090501", report, now)
        self.assertEqual(len(payload), 40)
        self.assertEqual(len(frame), 65)
        self.assertEqual(frame[:4], b"##\x02\xFE")
        self.assertEqual(frame[22:24], b"\x00\x28")
        self.assertEqual(frame[24:], payload + bytes((xor_bcc(frame[2:-1]),)))


class _FakeDynamics(object):
    def __init__(self):
        self.controls = {
            "throttle": 0,
            "brake": 0,
            "steering": 0,
            "gear": "drive",
            "parking_brake": False,
            "ignition": "off",
        }

    def update_controls(self, values):
        self.controls.update(values)
        return self.snapshot()

    def snapshot(self):
        return {
            "controls": dict(self.controls),
            "vehicle": {
                "speed_kph": 10,
                "engine_rpm": 1000,
                "heading": 20,
                "pitch": 1,
                "distance": 500,
            },
            "telemetry": {},
        }


class _FakeTelemetry(object):
    def __init__(self, state_provider, control_handler):
        self.port = 50001
        self.local_ip = "127.0.0.1"
        self.clients = []
        self.received = []


class _FakeBackgroundComponent(object):
    def __init__(self, config, callback):
        self.config = config
        self.callback = callback

    def start(self):
        pass

    def stop(self):
        pass

    def status(self):
        return {"enabled": False}


class StateBridgeTests(unittest.TestCase):
    def test_hil_fields_are_retained_while_controls_are_decoupled(self):
        manager = StateManager(
            bridge_config={
                "hil": {"enabled": False},
                "vehicle_output": {"enabled": False, "vin": "SYZKRMCAB25090501"},
            },
            auto_start=False,
            dynamics=_FakeDynamics(),
            hil_client_factory=_FakeBackgroundComponent,
            publisher_factory=_FakeBackgroundComponent,
            telemetry_server_factory=_FakeTelemetry,
        )
        manager.apply_hil_signals(
            {
                "X1": 1,
                "S2-R": 1,
                "Throttle": 50,
                "Brake": 7,
                "Steering": 120,
                "Q8-2": 1,
            }
        )
        snapshot = manager.snapshot()
        self.assertEqual(snapshot["controls"]["gear"], "reverse")
        self.assertEqual(snapshot["controls"]["throttle"], 50)
        self.assertEqual(snapshot["controls"]["steering"], 120)
        self.assertEqual(snapshot["hil"]["signals"]["Q8-2"], 1)
        self.assertEqual(set(snapshot["hil"]["signals"]), set(ALL_SIGNAL_NAMES))
        self.assertEqual(set(snapshot["vehicle_protocol"]["report"]), set(REPORT_DEFAULTS))
        self.assertEqual(snapshot["vehicle_protocol"]["frame_length"], 65)


if __name__ == "__main__":
    unittest.main()
