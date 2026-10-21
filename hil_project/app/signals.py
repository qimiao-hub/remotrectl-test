from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SignalDefinition:
    name: str
    description: str
    start_byte: int
    bit_length: int
    data_type: str
    bit_number: int | None
    minimum: int
    maximum: int
    initial: int = 0
    unit: str = "/"

    @property
    def is_bit(self) -> bool:
        return self.bit_number is not None


HEADER_DEFINITIONS: tuple[SignalDefinition, ...] = (
    SignalDefinition("Version", "版本号", 0, 8, "unsigned", None, 0, 255, 0x0C),
    SignalDefinition("MessageType", "报文类型", 1, 8, "unsigned", None, 0, 255, 0),
    SignalDefinition("MessageId", "报文 ID", 2, 8, "unsigned", None, 0, 255, 0),
    SignalDefinition("MessageLength", "报文长度", 3, 8, "unsigned", None, 0, 255, 0),
)


DATA_DEFINITIONS: tuple[SignalDefinition, ...] = (
    SignalDefinition("S2-N", "空挡", 4, 1, "unsigned", 0, 0, 1),
    SignalDefinition("S2-R", "后退挡", 4, 1, "unsigned", 1, 0, 1),
    SignalDefinition("S2-D", "前进挡", 4, 1, "unsigned", 2, 0, 1),
    SignalDefinition("A1", "自复位按钮", 4, 1, "unsigned", 3, 0, 1),
    SignalDefinition("A2", "自锁按钮", 4, 1, "unsigned", 4, 0, 1),
    SignalDefinition("A3", "自锁按钮", 4, 1, "unsigned", 5, 0, 1),
    SignalDefinition("B1", "自锁拨杆", 4, 1, "unsigned", 6, 0, 1),
    SignalDefinition("B2", "自锁拨杆", 4, 1, "unsigned", 7, 0, 1),
    SignalDefinition("B3", "自锁拨杆", 5, 1, "unsigned", 0, 0, 1),
    SignalDefinition("B4", "自锁拨杆", 5, 1, "unsigned", 1, 0, 1),
    SignalDefinition("B5", "自锁拨杆", 5, 1, "unsigned", 2, 0, 1),
    SignalDefinition("Q1", "自锁翘板", 5, 1, "unsigned", 3, 0, 1),
    SignalDefinition("Q2", "自锁翘板", 5, 1, "unsigned", 4, 0, 1),
    SignalDefinition("Q3", "自锁翘板", 5, 1, "unsigned", 5, 0, 1),
    SignalDefinition("Q4", "自锁翘板", 5, 1, "unsigned", 6, 0, 1),
    SignalDefinition("Q5", "自锁翘板", 5, 1, "unsigned", 7, 0, 1),
    SignalDefinition("Q6", "自锁翘板", 6, 1, "unsigned", 0, 0, 1),
    SignalDefinition("Q7-1", "自锁翘板", 6, 1, "unsigned", 1, 0, 1),
    SignalDefinition("Q7-2", "自锁翘板", 6, 1, "unsigned", 2, 0, 1),
    SignalDefinition("Q8-1", "自锁翘板", 6, 1, "unsigned", 3, 0, 1),
    SignalDefinition("Q8-2", "自锁翘板", 6, 1, "unsigned", 4, 0, 1),
    SignalDefinition("J1", "急停开关", 6, 1, "unsigned", 5, 0, 1),
    SignalDefinition("X1", "点火", 7, 1, "unsigned", 0, 0, 1),
    SignalDefinition("X2", "喇叭", 7, 1, "unsigned", 1, 0, 1),
    SignalDefinition("X3-1", "示宽灯", 7, 1, "unsigned", 2, 0, 1),
    SignalDefinition("X3-2", "近光灯", 7, 1, "unsigned", 3, 0, 1),
    SignalDefinition("X3-3", "远光灯", 7, 1, "unsigned", 4, 0, 1),
    SignalDefinition("X4", "雾灯", 7, 1, "unsigned", 5, 0, 1),
    SignalDefinition("X5-1", "左转向", 7, 1, "unsigned", 6, 0, 1),
    SignalDefinition("X5-2", "右转向", 7, 1, "unsigned", 7, 0, 1),
    SignalDefinition("X6-1", "雨刮 INT", 8, 1, "unsigned", 0, 0, 1),
    SignalDefinition("X6-2", "雨刮 LO", 8, 1, "unsigned", 1, 0, 1),
    SignalDefinition("X6-3", "雨刮 HI", 8, 1, "unsigned", 2, 0, 1),
    SignalDefinition("Steering", "方向盘转角", 9, 16, "signed", None, -450, 450),
    SignalDefinition("Throttle", "油门踏板", 11, 8, "unsigned", None, 0, 100, unit="%"),
    SignalDefinition("Brake", "刹车踏板", 12, 8, "unsigned", None, 0, 100, unit="%"),
    SignalDefinition("按键面板掉线故障", "按键面板掉线故障", 13, 1, "unsigned", 0, 0, 1),
    SignalDefinition("方向盘组件掉线故障", "方向盘组件掉线故障", 13, 1, "unsigned", 1, 0, 1),
    SignalDefinition("电机掉线故障", "电机掉线故障", 13, 1, "unsigned", 2, 0, 1),
    SignalDefinition("预留_Bit3", "预留", 13, 1, "unsigned", 3, 0, 1),
    SignalDefinition("预留_Bit4", "预留", 13, 1, "unsigned", 4, 0, 1),
    SignalDefinition("预留_Bit5", "预留", 13, 1, "unsigned", 5, 0, 1),
    SignalDefinition("预留_Bit6", "预留", 13, 1, "unsigned", 6, 0, 1),
    SignalDefinition("预留_Bit7", "预留", 13, 1, "unsigned", 7, 0, 1),
)

DATA_DEFINITION_BY_NAME = {definition.name: definition for definition in DATA_DEFINITIONS}
ALL_DEFINITIONS = HEADER_DEFINITIONS + DATA_DEFINITIONS


def signal_group(definition: SignalDefinition) -> str:
    name = definition.name
    description = definition.description
    if name in {"S2-N", "S2-R", "S2-D"}:
        return "档位"
    if name in {"Steering", "Throttle", "Brake"}:
        return "数值信号"
    if "故障" in name or "故障" in description or name.startswith("预留"):
        return "故障状态"
    if name.startswith("X3") or name.startswith("X4") or name.startswith("X5"):
        return "灯光"
    if name.startswith("X6"):
        return "雨刮"
    if name.startswith(("A", "B", "Q", "J")) or name in {"X1", "X2"}:
        return "按钮/拨杆"
    return "其他"


def data_signal_metadata() -> list[dict[str, object]]:
    return [
        {
            "name": definition.name,
            "description": definition.description,
            "group": signal_group(definition),
            "start_byte": definition.start_byte,
            "bit_length": definition.bit_length,
            "data_type": definition.data_type,
            "bit_number": definition.bit_number,
            "minimum": definition.minimum,
            "maximum": definition.maximum,
            "initial": definition.initial,
            "unit": definition.unit,
            "is_bit": definition.is_bit,
        }
        for definition in DATA_DEFINITIONS
    ]
