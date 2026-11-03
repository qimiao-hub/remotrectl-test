"""加载相对车辆的环境配置，生成原生 DDS 桥所需的三组环境列表。"""

import copy
import json
import logging
import math
from pathlib import Path
import threading
import time

from dynamics_model import local_coordinates_to_gps


class EnvironmentConfig:
    """整份校验后替换环境缓存；热加载失败时继续使用上一份有效配置。"""

    def __init__(self, config, initial_snapshot=None):
        """解析配置路径并加载文件；提供初始快照时同时检查相对车辆的地理换算，失败即报错。"""
        self._lock = threading.RLock()
        self._legacy = {key: copy.deepcopy(config.get(key, [])) for key in (
            "front_obstacles", "back_obstacles", "round_vehicles")}
        filename = config.get("environment_config_file")
        self.path = Path(filename) if filename else None
        if self.path is not None:
            if not self.path.is_absolute():
                self.path = Path(__file__).resolve().parent / self.path
            self.path = self.path.resolve()
        self._data = None
        self._signature = None
        self._loaded_mtime_ns = None
        self._next_check = 0.0
        self._last_error = ""
        if self.path is not None:
            self._reload(required=True, snapshot=initial_snapshot)

    @staticmethod
    def _number(value, field, low=None, high=None):
        """校验 JSON 数值有限且处于指定范围；拒绝布尔值和数字字符串。"""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field} 必须是数值")
        try:
            number = float(value)
        except OverflowError as error:
            raise ValueError(f"{field} 数值过大") from error
        if not math.isfinite(number) or (low is not None and number < low) or (high is not None and number > high):
            raise ValueError(f"{field} 数值非有限或超出范围 [{low}, {high}]")
        return number

    @staticmethod
    def _integer(value, field, low, high):
        """校验协议类型字段为指定范围整数，避免转换为单字节时发生回绕。"""
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{field} 必须是 {low}～{high} 的整数")
        return value

    @classmethod
    def _center(cls, value, field):
        """校验 [横向,纵向,高度] 米坐标；横纵向可编码为有符号厘米，高度可编码为 float。"""
        if not isinstance(value, list) or len(value) != 3:
            raise ValueError(f"{field} 必须是长度为 3 的数组")
        return [cls._number(value[0], field + "[0]", -327.68, 327.67),
                cls._number(value[1], field + "[1]", -327.68, 327.67),
                cls._number(value[2], field + "[2]", -3.4028234e38, 3.4028234e38)]

    @classmethod
    def _validate(cls, data):
        """校验完整配置、互斥的绝对/相对航向及 DDS/TCP 容量；任何条目非法都拒绝整份更新。"""
        keys = ("front_obstacles", "back_obstacles", "round_vehicles")
        if not isinstance(data, dict) or set(data) != set(keys):
            raise ValueError("配置必须且仅包含 front_obstacles、back_obstacles、round_vehicles")
        result = {key: [] for key in keys}
        for key in keys:
            limit = 50 if key == "round_vehicles" else 200
            if not isinstance(data[key], list) or len(data[key]) > limit:
                raise ValueError(f"{key} 必须是最多 {limit} 条的数组")
        for key in keys[:2]:
            for index, item in enumerate(data[key]):
                field = f"{key}[{index}]"
                if not isinstance(item, dict):
                    raise ValueError(f"{field} 必须是对象")
                kind = cls._integer(item.get("type"), field + ".type", 0, 1)
                part = "box" if kind == 0 else "scatter"
                if set(item) != {"type", part} or not isinstance(item.get(part), dict):
                    raise ValueError(f"{field} 必须包含 type 和 {part}")
                value = item[part]
                allowed = {"center", "object_type", "object_width", "object_height"} if kind == 0 else {"center"}
                if set(value) - allowed:
                    raise ValueError(f"{field}.{part} 含不支持的字段")
                normalized = {"center": cls._center(value.get("center"), field + "." + part + ".center")}
                if kind == 0:
                    # 车端将类型加 3 写入单字节，尺寸按厘米写入无符号 16 位。
                    normalized.update(object_type=cls._integer(value.get("object_type", 0), field + ".box.object_type", 0, 252),
                                      object_width=cls._number(value.get("object_width"), field + ".box.object_width", 0, 655.35),
                                      object_height=cls._number(value.get("object_height"), field + ".box.object_height", 0, 655.35))
                result[key].append({"type": kind, part: normalized})
        for index, item in enumerate(data["round_vehicles"]):
            field = f"round_vehicles[{index}]"
            if not isinstance(item, dict):
                raise ValueError(f"{field} 必须是对象")
            heading_fields = set(item) & {"heading_deg", "heading_offset_deg"}
            if len(heading_fields) != 1:
                raise ValueError(f"{field} 必须且只能提供 heading_deg 或 heading_offset_deg")
            heading_field = next(iter(heading_fields))
            if set(item) != {"device_type", "lateral_m", "longitudinal_m", heading_field}:
                raise ValueError(f"{field} 必须包含 device_type、lateral_m、longitudinal_m 和一个航向字段")
            result["round_vehicles"].append({
                "device_type": cls._integer(item["device_type"], field + ".device_type", 0, 255),
                "lateral_m": cls._number(item["lateral_m"], field + ".lateral_m"),
                "longitudinal_m": cls._number(item["longitudinal_m"], field + ".longitudinal_m"),
                heading_field: cls._number(item[heading_field], field + "." + heading_field) % 360,
            })
        obstacles = len(result["front_obstacles"]) + len(result["back_obstacles"])
        if 47 + 9 * obstacles + 11 * len(result["round_vehicles"]) > 1024:
            raise ValueError("0x07 超过 1024 字节：要求 47+9×障碍物数+11×周边车辆数≤1024")
        return result

    @staticmethod
    def _project(data, snapshot):
        """将相对位置旋转为地图坐标并生成 V2V 经纬度；绝对航向固定，相对航向叠加本车航向。"""
        vehicle = snapshot["vehicle"]
        origin = snapshot["map"]
        heading = float(vehicle["heading"]) % 360
        angle = math.radians(heading)
        vehicles = []
        for index, item in enumerate(data["round_vehicles"]):
            x, y = item["lateral_m"], item["longitudinal_m"]
            east = x * math.cos(angle) + y * math.sin(angle)
            north = -x * math.sin(angle) + y * math.cos(angle)
            latitude, longitude = local_coordinates_to_gps(
                vehicle["x_m"] + east, vehicle["y_m"] + north,
                origin["center_latitude"], origin["center_longitude"])
            # 坐标越界时拒绝该配置，不让非法地理值进入车端的无符号缩放。
            if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise ValueError(f"round_vehicles[{index}] 换算后的经纬度超出协议范围")
            # 绝对航向不随本车转向旋转；旧偏差字段继续保持随车叠加语义。
            vehicle_heading = item["heading_deg"] if "heading_deg" in item else (heading + item["heading_offset_deg"]) % 360
            vehicles.append({"device_type": item["device_type"], "latitude": latitude,
                             "longitude": longitude, "heading": vehicle_heading})
        return vehicles

    def _reload(self, required=False, snapshot=None):
        """按 500 ms 周期检查文件，校验成功才提交；运行中失败保留旧数据及独立错误状态。"""
        now = time.monotonic()
        if not required and now < self._next_check:
            return
        self._next_check = now + 0.5
        try:
            before = self.path.stat()
            signature = (before.st_mtime_ns, before.st_size, before.st_ino)
            if not required and signature == self._signature and not self._last_error:
                return
            candidate = self._validate(json.loads(self.path.read_text(encoding="utf-8")))
            if snapshot is not None:
                self._project(candidate, snapshot)
            after = self.path.stat()
            if signature != (after.st_mtime_ns, after.st_size, after.st_ino):
                raise ValueError("文件正在写入，等待下一次完整加载")
            # 同一锁内提交三组列表，不向发布线程暴露半份配置。
            self._data = candidate
            self._signature = signature
            self._loaded_mtime_ns = after.st_mtime_ns
            self._last_error = ""
        except (OSError, ValueError, TypeError, OverflowError) as error:
            message = f"环境配置 {self.path}：{error}"
            if message != self._last_error:
                logging.error("%s", message)
            self._last_error = message
            if required:
                raise ValueError(message) from error

    def sample(self, snapshot):
        """返回独立环境快照；文件模式动态转换周边车辆，未配置文件时原样兼容内联配置。"""
        with self._lock:
            if self.path is None:
                return copy.deepcopy(self._legacy)
            self._reload(snapshot=snapshot)
            result = copy.deepcopy(self._data)
            try:
                result["round_vehicles"] = self._project(result, snapshot)
            except (ValueError, OverflowError) as error:
                self._last_error = f"环境坐标转换失败：{error}"
                raise
            return result

    def status(self, snapshot):
        """返回文件状态、最后错误和已生效计数；状态读取与 DDS 发布共用同一加载缓存。"""
        with self._lock:
            if self.path is not None:
                self._reload(snapshot=snapshot)
            data = self._data if self.path is not None else self._legacy
            front, back, vehicles = (len(data[key]) for key in ("front_obstacles", "back_obstacles", "round_vehicles"))
            return {"source": "file" if self.path is not None else "inline", "loaded": True,
                    "path": str(self.path) if self.path is not None else None,
                    "mtime_ns": self._loaded_mtime_ns, "front_count": front, "back_count": back,
                    "vehicle_count": vehicles, "packet_bytes": 47 + 9 * (front + back) + 11 * vehicles,
                    "last_error": self._last_error}
