"""验证驾驶模式、气压保存及遥控反馈覆盖规则。"""

import json
from pathlib import Path
import tempfile
import unittest

from vehicle_feedback import VehicleFeedback


class VehicleFeedbackTests(unittest.TestCase):
    """使用临时目录验证持久化与协议范围，不启动后台通信。"""

    def test_control_feedback_overrides_saved_auto_mode_until_release(self):
        """自动默认值可被遥控或人工覆盖，释放覆盖后恢复自动，两路默认气压保持。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feedback.json"
            feedback = VehicleFeedback({}, path)
            feedback.update({"mode": "auto", "air_pressure_1": 8, "air_pressure_2": 8})
            reloaded = VehicleFeedback({}, path)
            for previous, expected, mode in (
                    ({"vcu_state": 5, "Remote_or_Unman": 8}, (5, 8), "remote"),
                    ({"vcu_state": 0, "Remote_or_Unman": 0}, (0, 0), "manual")):
                wire = reloaded.apply({}, previous)
                self.assertEqual((wire["vcu_state"], wire["Remote_or_Unman"]), expected)
                self.assertEqual((wire["air_pressure_1"], wire["air_pressure_2"]), (8, 8))
                self.assertEqual(reloaded.payload(previous)["effective_mode"], mode)
                self.assertEqual(reloaded.payload(previous)["mode"], "auto")
            self.assertEqual(reloaded.payload()["effective_mode"], "auto")

    def test_json_mode_and_pressure_override_only_supplied_fields(self):
        """临时 JSON 覆盖所提供字段，原始状态及 follow 释放受支持，网页保存值不变。"""
        with tempfile.TemporaryDirectory() as directory:
            feedback = VehicleFeedback({}, Path(directory) / "feedback.json")
            feedback.update({"mode": "auto", "air_pressure_1": 8, "air_pressure_2": 8})
            override = feedback.parse_override({"vehicle_feedback": {"mode": "manual", "air_pressure_1": 6}})
            effective = feedback.payload(override)
            self.assertEqual(effective["effective_mode"], "manual")
            self.assertEqual(effective["effective_air_pressure_1"], 6)
            self.assertEqual(effective["effective_air_pressure_2"], 8)
            self.assertEqual(effective["mode"], "auto")
            self.assertEqual(feedback.payload()["effective_air_pressure_1"], 8)
            self.assertEqual(feedback.parse_override({"mode": "follow"}),
                             {"vcu_state": None, "Remote_or_Unman": None})
            for invalid in ({"vcu_state": True}, {"Remote_or_Unman": 256},
                            {"vehicle_feedback": []}, {"air_pressure_1": "NaN"}):
                with self.assertRaises(ValueError):
                    feedback.parse_override(invalid)

    def test_follow_mode_retains_remote_and_timeout_states(self):
        """跟随模式保留遥控接管及超时回退状态，固定人工和遥控模式使用对应协议值。"""
        with tempfile.TemporaryDirectory() as directory:
            feedback = VehicleFeedback({}, Path(directory) / "feedback.json")
            self.assertEqual(feedback.payload({"vcu_state": 5, "Remote_or_Unman": 8})["effective_mode"], "remote")
            self.assertEqual(feedback.payload({"vcu_state": 0, "Remote_or_Unman": 0})["effective_mode"], "manual")
            for mode, expected in (("manual", (0, 0)), ("remote", (5, 8))):
                feedback.update({"mode": mode})
                wire = feedback.apply({})
                self.assertEqual((wire["vcu_state"], wire["Remote_or_Unman"]), expected)

    def test_invalid_input_leaves_saved_and_running_settings_unchanged(self):
        """拒绝非法模式、NaN、无穷及越界气压，防止保存错误导致运行态或文件被部分更新。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feedback.json"
            feedback = VehicleFeedback({}, path)
            feedback.update({"mode": "auto", "air_pressure_1": 0.8})
            saved = json.loads(path.read_text(encoding="utf-8"))
            for payload in ({"mode": "bad"}, {"mode": []}, [], {"air_pressure_1": True},
                            {"air_pressure_1": "NaN"}, {"air_pressure_1": float("inf")},
                            {"air_pressure_1": -0.01}, {"air_pressure_2": 8.01}):
                with self.assertRaises(ValueError):
                    feedback.update(payload)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), saved)
                self.assertEqual(feedback.payload()["mode"], "auto")


if __name__ == "__main__":
    unittest.main()
