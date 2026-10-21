import copy
import json
import math
import threading
import time
import uuid
from pathlib import Path


GEAR_RATIOS = {
    "park": 0.0,
    "neutral": 0.0,
    "reverse": -3.1,
    "first": 3.4,
    "second": 2.1,
    "third": 1.4,
    "fourth": 1.0,
    "fifth": 0.82,
    "drive": 1.2,
}


DEFAULT_RESPONSE_CONFIG = {
    "engine_force_base": 6.8,
    "brake_force": 12.0,
    "rolling_drag_base": 0.18,
    "rolling_drag_speed": 0.016,
    "aero_drag": 0.0045,
    "max_rpm": 6200.0,
}

LOCAL_STATE_DIR = Path(__file__).resolve().parent / "local_state"
MAP_STATE_FILE = LOCAL_STATE_DIR / "elevation_map.json"
DEFAULT_CENTER_LATITUDE = 31.230416
DEFAULT_CENTER_LONGITUDE = 121.473701
OBJECT_TYPES = {
    "obstacle": ("cone", "barrier", "pedestrian"),
    "v2v": ("car", "truck", "bus"),
}


class ElevationMap(object):
    def __init__(self, width=200, height=200, resolution_m=5.0, base_elevation_m=30.0):
        self.width = int(width)
        self.height = int(height)
        self.resolution_m = float(resolution_m)
        self.base_elevation_m = float(base_elevation_m)
        self.center_latitude = DEFAULT_CENTER_LATITUDE
        self.center_longitude = DEFAULT_CENTER_LONGITUDE
        self.objects = []
        self.origin_x_m = -(self.width * self.resolution_m) / 2.0
        self.origin_y_m = -(self.height * self.resolution_m) / 2.0
        self.elevation = self._default_elevation()

    def _default_elevation(self):
        return [[self.base_elevation_m for _ in range(self.width)] for _ in range(self.height)]

    def metadata(self):
        flat = [value for row in self.elevation for value in row]
        return {
            "width": self.width,
            "height": self.height,
            "width_m": self.width * self.resolution_m,
            "height_m": self.height * self.resolution_m,
            "resolution_m": self.resolution_m,
            "base_elevation_m": self.base_elevation_m,
            "origin_x_m": self.origin_x_m,
            "origin_y_m": self.origin_y_m,
            "min_elevation_m": min(flat),
            "max_elevation_m": max(flat),
            "center_latitude": self.center_latitude,
            "center_longitude": self.center_longitude,
            "object_types": copy.deepcopy(OBJECT_TYPES),
        }

    def export_payload(self):
        payload = self.metadata()
        payload["elevation"] = copy.deepcopy(self.elevation)
        payload["objects"] = copy.deepcopy(self.objects)
        return payload

    def import_payload(self, payload):
        width = int(payload["width"])
        height = int(payload["height"])
        resolution = float(payload["resolution_m"])
        elevation = payload["elevation"]
        if width < 8 or height < 8:
            raise ValueError("map width and height must be at least 8")
        if len(elevation) != height or any(len(row) != width for row in elevation):
            raise ValueError("elevation grid dimensions do not match metadata")
        self.width = width
        self.height = height
        self.resolution_m = clamp(resolution, 0.5, 100.0)
        self.base_elevation_m = float(payload.get("base_elevation_m", 30.0))
        self.center_latitude = float(payload.get("center_latitude", DEFAULT_CENTER_LATITUDE))
        self.center_longitude = float(payload.get("center_longitude", DEFAULT_CENTER_LONGITUDE))
        self.origin_x_m = float(payload.get("origin_x_m", -(width * self.resolution_m) / 2.0))
        self.origin_y_m = float(payload.get("origin_y_m", -(height * self.resolution_m) / 2.0))
        self.elevation = [[float(value) for value in row] for row in elevation]
        self.objects = [self._normalize_object(item) for item in payload.get("objects", [])]

    def resize(self, payload):
        width_m = clamp(float(payload.get("width_m", self.width * self.resolution_m)), 50.0, 5000.0)
        height_m = clamp(float(payload.get("height_m", self.height * self.resolution_m)), 50.0, 5000.0)
        resolution_m = clamp(float(payload.get("resolution_m", self.resolution_m)), 1.0, 50.0)
        base_elevation_m = clamp(float(payload.get("base_elevation_m", self.base_elevation_m)), -200.0, 500.0)
        old_center_latitude = self.center_latitude
        old_center_longitude = self.center_longitude
        self.center_latitude = clamp(float(payload.get("center_latitude", self.center_latitude)), -89.999999, 89.999999)
        self.center_longitude = clamp(float(payload.get("center_longitude", self.center_longitude)), -180.0, 180.0)
        width = max(8, int(round(width_m / resolution_m)))
        height = max(8, int(round(height_m / resolution_m)))
        if width > 600 or height > 600:
            raise ValueError("map grid cannot exceed 600 cells per side; increase resolution or reduce size")
        grid_changed = (
            width != self.width
            or height != self.height
            or resolution_m != self.resolution_m
            or base_elevation_m != self.base_elevation_m
        )
        self.width = width
        self.height = height
        self.resolution_m = resolution_m
        self.base_elevation_m = base_elevation_m
        self.origin_x_m = -(self.width * self.resolution_m) / 2.0
        self.origin_y_m = -(self.height * self.resolution_m) / 2.0
        if grid_changed:
            self.elevation = self._default_elevation()
        if old_center_latitude != self.center_latitude or old_center_longitude != self.center_longitude:
            self._refresh_object_local_positions()
        return self.export_payload()

    def gps_to_local(self, latitude, longitude):
        lat = float(latitude)
        lon = float(longitude)
        y_m = (lat - self.center_latitude) * 111_320.0
        meters_per_lon = max(111_320.0 * math.cos(math.radians(self.center_latitude)), 1.0)
        x_m = (lon - self.center_longitude) * meters_per_lon
        return x_m, y_m

    def local_to_gps(self, x_m, y_m):
        latitude = self.center_latitude + (float(y_m) / 111_320.0)
        meters_per_lon = max(111_320.0 * math.cos(math.radians(self.center_latitude)), 1.0)
        longitude = self.center_longitude + (float(x_m) / meters_per_lon)
        return latitude, longitude

    def add_object(self, payload):
        item = self._normalize_object(payload, new_id=True)
        self.objects.append(item)
        return item

    def update_object(self, object_id, payload):
        for index, item in enumerate(self.objects):
            if item["id"] == object_id:
                merged = dict(item)
                merged.update(payload)
                merged["id"] = object_id
                self.objects[index] = self._normalize_object(merged)
                return self.objects[index]
        raise ValueError("map object not found")

    def delete_object(self, object_id):
        before = len(self.objects)
        self.objects = [item for item in self.objects if item["id"] != object_id]
        if len(self.objects) == before:
            raise ValueError("map object not found")
        return {"deleted": object_id, "objects": copy.deepcopy(self.objects)}

    def _normalize_object(self, payload, new_id=False):
        category = payload.get("category", "obstacle")
        if category not in OBJECT_TYPES:
            category = "obstacle"
        subtype = payload.get("subtype", OBJECT_TYPES[category][0])
        if subtype not in OBJECT_TYPES[category]:
            subtype = OBJECT_TYPES[category][0]
        heading = normalize_degrees(float(payload.get("heading", 0.0)))

        if "latitude" in payload and "longitude" in payload:
            latitude = float(payload["latitude"])
            longitude = float(payload["longitude"])
            x_m, y_m = self.gps_to_local(latitude, longitude)
        else:
            x_m = float(payload.get("x_m", 0.0))
            y_m = float(payload.get("y_m", 0.0))
            latitude, longitude = self.local_to_gps(x_m, y_m)

        return {
            "id": payload.get("id") if not new_id and payload.get("id") else f"obj-{uuid.uuid4().hex[:10]}",
            "category": category,
            "subtype": subtype,
            "latitude": latitude,
            "longitude": longitude,
            "heading": heading,
            "x_m": x_m,
            "y_m": y_m,
        }

    def _refresh_object_local_positions(self):
        self.objects = [self._normalize_object(item) for item in self.objects]

    def sample(self, x_m, y_m):
        gx = (x_m - self.origin_x_m) / self.resolution_m
        gy = (y_m - self.origin_y_m) / self.resolution_m
        gx = clamp(gx, 0.0, self.width - 1.0)
        gy = clamp(gy, 0.0, self.height - 1.0)
        x0 = int(math.floor(gx))
        y0 = int(math.floor(gy))
        x1 = min(x0 + 1, self.width - 1)
        y1 = min(y0 + 1, self.height - 1)
        tx = gx - x0
        ty = gy - y0
        z00 = self.elevation[y0][x0]
        z10 = self.elevation[y0][x1]
        z01 = self.elevation[y1][x0]
        z11 = self.elevation[y1][x1]
        return (
            z00 * (1.0 - tx) * (1.0 - ty)
            + z10 * tx * (1.0 - ty)
            + z01 * (1.0 - tx) * ty
            + z11 * tx * ty
        )

    def grade_along_heading(self, x_m, y_m, heading_deg):
        heading = math.radians(heading_deg)
        step = max(self.resolution_m, 1.0)
        dx = math.sin(heading) * step
        dy = math.cos(heading) * step
        forward = self.sample(x_m + dx, y_m + dy)
        backward = self.sample(x_m - dx, y_m - dy)
        rise = forward - backward
        return math.degrees(math.atan2(rise, step * 2.0))

    def brush(self, payload):
        x_m = float(payload.get("x_m", 0.0))
        y_m = float(payload.get("y_m", 0.0))
        radius_m = clamp(float(payload.get("radius_m", 35.0)), self.resolution_m, 250.0)
        strength_m = clamp(float(payload.get("strength_m", 2.0)), 0.1, 25.0)
        mode = payload.get("mode", "raise")

        cx = (x_m - self.origin_x_m) / self.resolution_m
        cy = (y_m - self.origin_y_m) / self.resolution_m
        radius_cells = max(radius_m / self.resolution_m, 1.0)
        min_x = max(int(math.floor(cx - radius_cells)), 0)
        max_x = min(int(math.ceil(cx + radius_cells)), self.width - 1)
        min_y = max(int(math.floor(cy - radius_cells)), 0)
        max_y = min(int(math.ceil(cy + radius_cells)), self.height - 1)
        target = self.sample(x_m, y_m)
        source = copy.deepcopy(self.elevation) if mode == "smooth" else self.elevation
        changed = 0

        for row in range(min_y, max_y + 1):
            for col in range(min_x, max_x + 1):
                distance = math.hypot(col - cx, row - cy)
                if distance > radius_cells:
                    continue
                weight = 1.0 - (distance / radius_cells)
                if mode == "lower":
                    self.elevation[row][col] -= strength_m * weight
                elif mode == "smooth":
                    self.elevation[row][col] = blend(source[row][col], self._neighbor_average(source, col, row), weight * 0.65)
                elif mode == "flatten":
                    self.elevation[row][col] = blend(self.elevation[row][col], target, weight)
                else:
                    self.elevation[row][col] += strength_m * weight
                self.elevation[row][col] = round(clamp(self.elevation[row][col], -200.0, 500.0), 3)
                changed += 1
        return {"changed_cells": changed, "map": self.export_payload()}

    def _neighbor_average(self, source, col, row):
        values = []
        for y in range(max(0, row - 1), min(self.height, row + 2)):
            for x in range(max(0, col - 1), min(self.width, col + 2)):
                values.append(source[y][x])
        return sum(values) / len(values)


class DynamicsModel(object):
    def __init__(self):
        self.lock = threading.RLock()
        self.paused = False
        self.elevation_map = self._load_elevation_map()
        self.response_config = dict(DEFAULT_RESPONSE_CONFIG)
        self.controls = {
            "throttle": 0.0,
            "brake": 0.0,
            "steering": 0.0,
            "gear": "drive",
            "parking_brake": False,
            "ignition": "run",
        }
        self.vehicle = {
            "speed": 0.0,
            "engine_rpm": 800.0,
            "latitude": self.elevation_map.center_latitude,
            "longitude": self.elevation_map.center_longitude,
            "heading": 0.0,
            "pitch": 0.0,
            "x_m": 0.0,
            "y_m": 0.0,
            "distance": 0.0,
            "acceleration": 0.0,
            "elevation_m": self.elevation_map.sample(0.0, 0.0),
            "terrain_grade": 0.0,
        }
        self._last_tick = time.monotonic()

        thread = threading.Thread(target=self.physics_loop, name="Thread-Physics")
        thread.daemon = True
        thread.start()

    def _load_elevation_map(self):
        if not MAP_STATE_FILE.exists():
            return ElevationMap()
        try:
            payload = json.loads(MAP_STATE_FILE.read_text(encoding="utf-8"))
            elevation_map = ElevationMap()
            elevation_map.import_payload(payload)
            return elevation_map
        except Exception as exc:
            print(f"Could not load saved elevation map: {exc}")
            return ElevationMap()

    def _save_elevation_map(self):
        LOCAL_STATE_DIR.mkdir(parents=True, exist_ok=True)
        temp_file = MAP_STATE_FILE.with_suffix(".json.tmp")
        temp_file.write_text(
            json.dumps(self.elevation_map.export_payload(), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temp_file.replace(MAP_STATE_FILE)

    def _refresh_vehicle_terrain(self):
        self.vehicle["elevation_m"] = self.elevation_map.sample(self.vehicle["x_m"], self.vehicle["y_m"])
        self.vehicle["terrain_grade"] = self.elevation_map.grade_along_heading(
            self.vehicle["x_m"], self.vehicle["y_m"], self.vehicle["heading"]
        )
        self.vehicle["pitch"] = clamp(
            self.vehicle["terrain_grade"] + self.vehicle["acceleration"] * 0.35,
            -35.0,
            35.0,
        )

    def physics_loop(self):
        while True:
            now = time.monotonic()
            dt = min(now - self._last_tick, 0.1)
            self._last_tick = now
            if not self.paused:
                self.iterate(dt)
            time.sleep(0.02)

    def iterate(self, dt):
        with self.lock:
            gear_ratio = GEAR_RATIOS.get(self.controls["gear"], GEAR_RATIOS["drive"])
            throttle = self.controls["throttle"] / 100.0
            brake = self.controls["brake"] / 100.0
            speed = self.vehicle["speed"]
            config = self.response_config
            terrain_grade = self.elevation_map.grade_along_heading(
                self.vehicle["x_m"], self.vehicle["y_m"], self.vehicle["heading"]
            )

            engine_force = config["engine_force_base"] * throttle * (abs(gear_ratio) if gear_ratio else 0.1)
            brake_force = config["brake_force"] * brake
            rolling_drag = config["rolling_drag_base"] + config["rolling_drag_speed"] * abs(speed)
            aero_drag = config["aero_drag"] * speed * abs(speed)
            slope_force = 9.81 * math.sin(math.radians(terrain_grade))

            if self.controls["ignition"] == "off" or self.controls["gear"] in ("park", "neutral"):
                engine_force = 0.0
            if self.controls["parking_brake"]:
                brake_force += config["brake_force"] * 1.5

            direction = -1.0 if self.controls["gear"] == "reverse" else 1.0
            drag_direction = -1.0 if speed >= 0 else 1.0
            acceleration = (engine_force * direction) - (brake_force * sign_or_zero(speed, direction)) + (rolling_drag + abs(aero_drag)) * drag_direction - slope_force

            new_speed = speed + acceleration * dt
            if self.controls["gear"] != "reverse":
                new_speed = max(new_speed, 0.0)
            else:
                new_speed = min(new_speed, 0.0)
            if abs(new_speed) < 0.03 and throttle < 0.02:
                new_speed = 0.0

            steering_rad = math.radians(self.controls["steering"] / 12.0)
            curvature = math.tan(steering_rad) / 2.8 if abs(steering_rad) > 0.001 else 0.0
            yaw_rate = curvature * new_speed
            self.vehicle["heading"] = normalize_degrees(self.vehicle["heading"] + math.degrees(yaw_rate * dt))

            distance_delta = new_speed * dt
            self._move_local(distance_delta)
            self._sync_vehicle_gps_from_local()
            self.vehicle["speed"] = new_speed
            self.vehicle["distance"] += abs(distance_delta)
            self.vehicle["acceleration"] = acceleration
            self.vehicle["elevation_m"] = self.elevation_map.sample(self.vehicle["x_m"], self.vehicle["y_m"])
            self.vehicle["terrain_grade"] = terrain_grade
            self.vehicle["pitch"] = clamp(terrain_grade + acceleration * 0.35, -35.0, 35.0)
            self.vehicle["engine_rpm"] = self._engine_rpm(new_speed, gear_ratio)

    def _move_local(self, meters):
        heading_rad = math.radians(self.vehicle["heading"])
        self.vehicle["x_m"] += meters * math.sin(heading_rad)
        self.vehicle["y_m"] += meters * math.cos(heading_rad)

    def _sync_vehicle_gps_from_local(self):
        latitude, longitude = self.elevation_map.local_to_gps(self.vehicle["x_m"], self.vehicle["y_m"])
        self.vehicle["latitude"] = latitude
        self.vehicle["longitude"] = longitude

    def _sync_vehicle_local_from_gps(self):
        x_m, y_m = self.elevation_map.gps_to_local(self.vehicle["latitude"], self.vehicle["longitude"])
        self.vehicle["x_m"] = x_m
        self.vehicle["y_m"] = y_m

    def _engine_rpm(self, speed, gear_ratio):
        if self.controls["ignition"] == "off":
            return 0.0
        if self.controls["gear"] in ("park", "neutral"):
            return 800.0 + self.controls["throttle"] * 28.0
        wheel_rpm = abs(speed) * 60.0 / 2.02
        return clamp(780.0 + wheel_rpm * abs(gear_ratio) * 3.6, 650.0, self.response_config["max_rpm"])

    def update_controls(self, payload):
        with self.lock:
            gps_changed = "latitude" in payload or "longitude" in payload
            if "throttle" in payload:
                self.controls["throttle"] = clamp(float(payload["throttle"]), 0.0, 100.0)
            if "accelerator" in payload:
                self.controls["throttle"] = clamp(float(payload["accelerator"]), 0.0, 100.0)
            if "brake" in payload:
                self.controls["brake"] = clamp(float(payload["brake"]), 0.0, 100.0)
            if "steering" in payload:
                self.controls["steering"] = clamp(float(payload["steering"]), -600.0, 600.0)
            if "gear" in payload and payload["gear"] in GEAR_RATIOS:
                self.controls["gear"] = payload["gear"]
            if "parking_brake" in payload:
                self.controls["parking_brake"] = bool(payload["parking_brake"])
            if "ignition" in payload and payload["ignition"] in ("off", "accessory", "run", "start"):
                self.controls["ignition"] = "run" if payload["ignition"] == "start" else payload["ignition"]
            if "latitude" in payload:
                self.vehicle["latitude"] = float(payload["latitude"])
            if "longitude" in payload:
                self.vehicle["longitude"] = float(payload["longitude"])
            if gps_changed:
                self._sync_vehicle_local_from_gps()
            if "heading" in payload:
                self.vehicle["heading"] = normalize_degrees(float(payload["heading"]))
            if payload.get("reset_position"):
                if not gps_changed:
                    self.vehicle["x_m"] = 0.0
                    self.vehicle["y_m"] = 0.0
                    self._sync_vehicle_gps_from_local()
                self.vehicle["distance"] = 0.0
                self.vehicle["speed"] = 0.0
                self.vehicle["acceleration"] = 0.0
            if "heading" in payload or payload.get("reset_position") or gps_changed:
                self._refresh_vehicle_terrain()
            return self.snapshot()

    def brush_elevation(self, payload):
        with self.lock:
            result = self.elevation_map.brush(payload)
            self._refresh_vehicle_terrain()
            self._save_elevation_map()
            return result

    def map_payload(self):
        with self.lock:
            return self.elevation_map.export_payload()

    def import_map(self, payload):
        with self.lock:
            self.elevation_map.import_payload(payload)
            self._sync_vehicle_local_from_gps()
            self._refresh_vehicle_terrain()
            self._save_elevation_map()
            return self.elevation_map.export_payload()

    def reset_map(self):
        with self.lock:
            self.elevation_map = ElevationMap()
            self._sync_vehicle_local_from_gps()
            self._refresh_vehicle_terrain()
            self._save_elevation_map()
            return self.elevation_map.export_payload()

    def update_map_settings(self, payload):
        with self.lock:
            result = self.elevation_map.resize(payload)
            self._sync_vehicle_local_from_gps()
            self._refresh_vehicle_terrain()
            self._save_elevation_map()
            return self.elevation_map.export_payload()

    def add_map_object(self, payload):
        with self.lock:
            item = self.elevation_map.add_object(payload)
            self._save_elevation_map()
            return {"object": item, "map": self.elevation_map.export_payload()}

    def update_map_object(self, object_id, payload):
        with self.lock:
            item = self.elevation_map.update_object(object_id, payload)
            self._save_elevation_map()
            return {"object": item, "map": self.elevation_map.export_payload()}

    def delete_map_object(self, object_id):
        with self.lock:
            result = self.elevation_map.delete_object(object_id)
            self._save_elevation_map()
            result["map"] = self.elevation_map.export_payload()
            return result

    def update_response_config(self, payload):
        with self.lock:
            for key, default in DEFAULT_RESPONSE_CONFIG.items():
                if key in payload:
                    high = 12000.0 if key == "max_rpm" else 100.0
                    self.response_config[key] = clamp(float(payload[key]), 0.0, high)
                elif key not in self.response_config:
                    self.response_config[key] = default
            return dict(self.response_config)

    def response_config_payload(self):
        with self.lock:
            return dict(self.response_config)

    def set_paused(self, paused):
        self.paused = bool(paused)
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            speed_ms = self.vehicle["speed"]
            speed_kph = abs(speed_ms) * 3.6
            vehicle = {
                "speed_mps": abs(speed_ms),
                "signed_speed_mps": speed_ms,
                "speed_kph": speed_kph,
                "engine_rpm": self.vehicle["engine_rpm"],
                "latitude": self.vehicle["latitude"],
                "longitude": self.vehicle["longitude"],
                "heading": self.vehicle["heading"],
                "pitch": self.vehicle["pitch"],
                "distance": self.vehicle["distance"],
                "x_m": self.vehicle["x_m"],
                "y_m": self.vehicle["y_m"],
                "acceleration": self.vehicle["acceleration"],
                "elevation_m": self.vehicle["elevation_m"],
                "terrain_grade": self.vehicle["terrain_grade"],
            }
            return {
                "controls": dict(self.controls),
                "vehicle": vehicle,
                "map": self.elevation_map.metadata(),
                "response_config": dict(self.response_config),
                "telemetry": {
                    "vehicle_speed": speed_kph,
                    "engine_speed": self.vehicle["engine_rpm"],
                    "latitude": self.vehicle["latitude"],
                    "longitude": self.vehicle["longitude"],
                    "heading": self.vehicle["heading"],
                    "pitch": self.vehicle["pitch"],
                    "road_slope": self.vehicle["terrain_grade"],
                    "terrain_grade": self.vehicle["terrain_grade"],
                    "elevation_m": self.vehicle["elevation_m"],
                    "x_m": self.vehicle["x_m"],
                    "y_m": self.vehicle["y_m"],
                    "gear_lever_position": self.controls["gear"],
                    "accelerator_pedal_position": self.controls["throttle"],
                    "brake_pedal_position": self.controls["brake"],
                    "parking_brake_status": self.controls["parking_brake"],
                    "ignition_status": self.controls["ignition"],
                },
                "paused": self.paused,
            }


def clamp(value, low, high):
    return max(low, min(high, value))


def blend(old, new, amount):
    amount = clamp(amount, 0.0, 1.0)
    return old + (new - old) * amount


def sign_or_zero(speed, fallback_direction):
    if speed > 0:
        return 1.0
    if speed < 0:
        return -1.0
    return fallback_direction


def normalize_degrees(value):
    while value >= 360.0:
        value -= 360.0
    while value < 0.0:
        value += 360.0
    return value
