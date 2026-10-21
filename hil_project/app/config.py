from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class TcpConfig(BaseModel):
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 2000
    version: int = 0x0C
    steering_endian: Literal["little", "big"] = "little"
    log_frames: bool = True


class ApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class StateConfig(BaseModel):
    source_priority: list[str] = Field(default_factory=lambda: ["api", "xbox", "can", "serial"])


class SerialConfig(BaseModel):
    enabled: bool = False
    port: str = "COM1"
    baudrate: int = 115200
    timeout: float = 0.05


class CanConfig(BaseModel):
    enabled: bool = False
    interface: str = "vector"
    channel: str | int = 0
    bitrate: int = 500000
    dbc_path: str = "config/can.dbc"
    signal_aliases: dict[str, str] = Field(default_factory=dict)


class XboxMapping(BaseModel):
    kind: Literal["axis", "button", "hat"]
    index: int
    signal: str
    in_min: float = -1.0
    in_max: float = 1.0
    out_min: float = 0.0
    out_max: float = 1.0
    pressed_value: int = 1
    released_value: int = 0
    hat_x: int | None = None
    hat_y: int | None = None


class XboxConfig(BaseModel):
    enabled: bool = False
    poll_interval: float = 0.02
    mappings: list[XboxMapping] = Field(default_factory=list)


class Settings(BaseModel):
    tcp: TcpConfig = Field(default_factory=TcpConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    serial: SerialConfig = Field(default_factory=SerialConfig)
    can: CanConfig = Field(default_factory=CanConfig)
    xbox: XboxConfig = Field(default_factory=XboxConfig)


def load_settings(path: str | Path | None = None) -> Settings:
    config_path = Path(path or os.environ.get("HIL_CONFIG", "config.yaml"))
    if not config_path.exists():
        return Settings()

    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load YAML config files. Install requirements.txt.") from exc

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return Settings.model_validate(_normalize_hex_values(data))


def _normalize_hex_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_hex_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_hex_values(item) for item in value]
    if isinstance(value, str) and value.lower().startswith("0x"):
        return int(value, 16)
    return value
