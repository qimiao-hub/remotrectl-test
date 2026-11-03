"""保存网页设置的车辆模式与制动气压，并映射为 DDS 反馈物理量。"""

import json
import logging
import math
import os
from pathlib import Path
import tempfile
import threading


MODE_FIELDS = {"manual": (0, 0), "auto": (5, 6), "remote": (5, 8)}


class VehicleFeedback:
    """保存网页默认反馈，并允许有效 JSON 控制按字段覆盖运行态。"""

    def __init__(self, dds_config, path=None):
        """从本地文件加载设置；未保存时沿用现有气压及跟随遥控模式。"""
        self._lock = threading.RLock()
        self.path = Path(path or os.environ.get("SIM_VEHICLE_FEEDBACK_CONFIG") or
                         Path(__file__).resolve().parent / "local_state/vehicle_feedback.json")
        defaults = dds_config.get("wire_defaults", {})
        self._config = self._validate({"mode": "follow", **{
            key: defaults.get(key, 0) for key in ("air_pressure_1", "air_pressure_2")}})
        if self.path.exists():
            try:
                self._config = self._validate(json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError) as error:
                # 损坏的配置不影响通信启动，保留原文件供排查。
                logging.warning("车辆反馈配置加载失败，使用默认值：%s", error)

    @staticmethod
    def _validate(payload):
        """校验模式和网页允许的 0～8 MPa 气压；仅调整仿真输入范围，不改变车舱编码。"""
        if not isinstance(payload, dict):
            raise ValueError("车辆反馈配置必须是对象")
        mode = payload.get("mode", "follow")
        if not isinstance(mode, str) or mode not in ("follow", *MODE_FIELDS):
            raise ValueError("请选择跟随遥控、人工、自动或遥控模式")
        result = {"mode": mode}
        for key in ("air_pressure_1", "air_pressure_2"):
            value = payload.get(key, 0)
            if isinstance(value, bool):
                raise ValueError("气压必须是 0～8 MPa 的数值")
            try:
                value = float(value)
            except (ValueError, TypeError, OverflowError) as error:
                raise ValueError("气压必须是 0～8 MPa 的数值") from error
            if not math.isfinite(value) or not 0 <= value <= 8:
                raise ValueError("气压范围为 0～8 MPa")
            result[key] = round(value, 2)
        return result

    def payload(self, wire=None):
        """返回网页默认值及叠加控制后的实际模式、两路气压，不暴露内部可变对象。"""
        with self._lock:
            feedback = self.apply({}, wire)
            fields = (feedback.get("vcu_state", 0), feedback.get("Remote_or_Unman", 0))
            effective = next((mode for mode, pair in MODE_FIELDS.items() if pair == fields), "manual")
            return {**self._config, "effective_mode": effective,
                    "effective_air_pressure_1": feedback["air_pressure_1"],
                    "effective_air_pressure_2": feedback["air_pressure_2"]}

    def parse_override(self, payload):
        """校验 JSON 中的模式、原始状态和气压；仅返回明确提供的字段，follow 释放模式覆盖。"""
        if not isinstance(payload, dict):
            raise ValueError("JSON 控制必须是对象")
        nested = payload.get("vehicle_feedback", {})
        if not isinstance(nested, dict):
            raise ValueError("vehicle_feedback 必须是对象")
        supplied = {**payload, **nested}
        with self._lock:
            checked = self._validate({**self._config, **{
                key: supplied[key] for key in ("mode", "air_pressure_1", "air_pressure_2") if key in supplied}})
        result = {key: checked[key] for key in ("air_pressure_1", "air_pressure_2") if key in supplied}
        if "mode" in supplied:
            fields = MODE_FIELDS.get(checked["mode"], (None, None))
            result.update(zip(("vcu_state", "Remote_or_Unman"), fields))
        for key in ("vcu_state", "Remote_or_Unman"):
            if key in supplied:
                value = supplied[key]
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
                    raise ValueError(f"{key} 必须是 0～255 的整数")
                result[key] = value
        return result

    def update(self, payload):
        """校验后原子保存设置；磁盘写入成功才更新运行态，失败时保留原设置。"""
        if not isinstance(payload, dict):
            raise ValueError("车辆反馈配置必须是对象")
        with self._lock:
            config = self._validate({**self._config, **payload})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                 prefix="vehicle-feedback-", delete=False) as output:
                    temporary = Path(output.name)
                    json.dump(config, output, ensure_ascii=False, indent=2)
                    output.write("\n")
                # 同目录替换防止进程中断留下半份 JSON。
                os.replace(temporary, self.path)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
            self._config = config

    def apply(self, wire, overrides=None):
        """先应用网页默认值，再逐字段叠加有效控制；不受人工、自动、遥控模式取值限制。"""
        with self._lock:
            wire.update({key: self._config[key] for key in ("air_pressure_1", "air_pressure_2")})
            if self._config["mode"] != "follow":
                wire["vcu_state"], wire["Remote_or_Unman"] = MODE_FIELDS[self._config["mode"]]
            # 所有明确提供的控制反馈最后写入，网页仅充当未提供字段的默认值。
            wire.update(overrides or {})
            return wire
