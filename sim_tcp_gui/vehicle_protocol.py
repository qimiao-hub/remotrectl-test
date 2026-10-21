"""Vehicle-side 0x02 realtime report mapping and binary codec.

The field contract matches test_server's vehicle simulator protocol, but this
module remains standalone so sim_tcp_gui does not import another project.
"""

from datetime import datetime


START = b"##"
RESP_COMMAND = 0xFE
ENCRYPT_NONE = 0x01

REPORT_DEFAULTS = {
    "speed_deci_kmh": 0,
    "heading_raw": 0,
    "wheel_center_raw": 400,
    "throttle_percent": 0,
    "brake_percent": 0,
    "engine_speed_raw": 0,
    "total_mileage_deci_km": 0,
    "trip_mileage_deci_km": 0,
    "pitch_raw": 90,
    "gear": 3,
    "fuel_or_battery_percent": 89,
    "water_temperature_raw": 74,
    "brake_pressure1_raw": 0,
    "brake_pressure2_raw": 0,
    "left_switch": 0,
    "right_switch": 0,
    "button_operation1": 0,
    "button_operation2": 0,
    "driving_state": 2,
    "lift_reached": 0,
    "lower_reached": 0,
    "fault_info1": 0,
    "fault_info2": 0,
}

GEAR_TO_PROTOCOL = {
    "reverse": 0x01,
    "neutral": 0x02,
    "park": 0x02,
    "drive": 0x03,
    "first": 0x04,
    "second": 0x05,
    "third": 0x06,
    "fourth": 0x07,
    "fifth": 0x08,
}


def clamp_int(value, low, high):
    return max(low, min(high, int(round(value))))


def bit_pack(signals, names):
    result = 0
    for bit, name in enumerate(names):
        if signals.get(name):
            result |= 1 << bit
    return result


class VehicleReportMapper(object):
    def __init__(self, config=None):
        self.config = dict(config or {})
        self.steering_ratio = max(float(self.config.get("steering_ratio", 12.0)), 0.001)
        self.defaults = dict(REPORT_DEFAULTS)
        self.defaults.update(self.config.get("defaults", {}))

    def convert(self, snapshot, hil_signals):
        controls = snapshot["controls"]
        vehicle = snapshot["vehicle"]
        report = dict(self.defaults)
        wheel_angle = float(controls["steering"]) / self.steering_ratio
        report.update(
            {
                "speed_deci_kmh": clamp_int(vehicle["speed_kph"] * 10.0, 0, 0xFFFF),
                "heading_raw": clamp_int(vehicle["heading"] * 10.0, 0, 3599),
                "wheel_center_raw": clamp_int((wheel_angle + 40.0) * 10.0, 0, 0xFFFF),
                "throttle_percent": clamp_int(controls["throttle"], 0, 100),
                "brake_percent": clamp_int(controls["brake"], 0, 100),
                "engine_speed_raw": clamp_int(vehicle["engine_rpm"] / 0.125, 0, 0xFFFD),
                "total_mileage_deci_km": clamp_int(vehicle["distance"] / 100.0, 0, 0xFFFFFFFF),
                "trip_mileage_deci_km": clamp_int(vehicle["distance"] / 100.0, 0, 0xFFFF),
                "pitch_raw": clamp_int(vehicle["pitch"] + 90.0, 0, 0xFFFF),
                "gear": GEAR_TO_PROTOCOL.get(controls["gear"], 0x03),
                "left_switch": bit_pack(
                    hil_signals,
                    ("X3-1", "X3-2", "X3-3", "X4", "X5-1", "X5-2", "X6-1", "X6-2"),
                ),
                "right_switch": bit_pack(hil_signals, ("X6-3", "X2", "J1")),
                "button_operation1": bit_pack(
                    hil_signals, ("A1", "A2", "A3", "B1", "B2", "B3", "B4", "B5")
                ),
                "button_operation2": bit_pack(
                    hil_signals, ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7-1", "Q7-2")
                ),
                "fault_info1": bit_pack(
                    hil_signals,
                    (
                        "按键面板掉线故障",
                        "方向盘组件掉线故障",
                        "电机掉线故障",
                        "预留_Bit3",
                        "预留_Bit4",
                        "预留_Bit5",
                        "预留_Bit6",
                        "预留_Bit7",
                    ),
                ),
                "fault_info2": bit_pack(hil_signals, ("Q8-1", "Q8-2")),
            }
        )
        return {name: int(report[name]) for name in REPORT_DEFAULTS}


def protocol_time(now=None):
    current = now or datetime.now()
    return bytes(
        (
            current.year % 100,
            current.month,
            current.day,
            current.hour,
            current.minute,
            current.second,
        )
    ) + int(current.microsecond // 1000).to_bytes(2, "big")


def build_realtime_report_payload(report, now=None):
    values = dict(REPORT_DEFAULTS)
    values.update(report)
    data = bytearray(protocol_time(now))
    data += int(values["speed_deci_kmh"]).to_bytes(2, "big")
    data += int(values["heading_raw"]).to_bytes(2, "big")
    data += int(values["wheel_center_raw"]).to_bytes(2, "big")
    data.append(int(values["throttle_percent"]) & 0xFF)
    data.append(int(values["brake_percent"]) & 0xFF)
    data += int(values["engine_speed_raw"]).to_bytes(2, "big")
    data += int(values["total_mileage_deci_km"]).to_bytes(4, "big")
    data += int(values["trip_mileage_deci_km"]).to_bytes(2, "big")
    data += int(values["pitch_raw"]).to_bytes(2, "big")
    for name in (
        "gear",
        "fuel_or_battery_percent",
        "water_temperature_raw",
        "brake_pressure1_raw",
        "brake_pressure2_raw",
        "left_switch",
        "right_switch",
        "button_operation1",
        "button_operation2",
        "driving_state",
        "lift_reached",
        "lower_reached",
        "fault_info1",
        "fault_info2",
    ):
        data.append(int(values[name]) & 0xFF)
    if len(data) != 40:
        raise ValueError("vehicle realtime report payload must be 40 bytes")
    return bytes(data)


def xor_bcc(data):
    result = 0
    for value in data:
        result ^= value
    return result


def fixed_ascii(value, length):
    raw = str(value or "").encode("ascii", errors="ignore")[:length]
    return raw.ljust(length, b"\x00")


def build_vehicle_realtime_frame(vin, report, now=None):
    payload = build_realtime_report_payload(report, now)
    frame = START + bytes((0x02, RESP_COMMAND)) + fixed_ascii(vin, 17)
    frame += bytes((ENCRYPT_NONE,)) + len(payload).to_bytes(2, "big") + payload
    return frame + bytes((xor_bcc(frame[2:]),))
