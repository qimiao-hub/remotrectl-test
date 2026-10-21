from __future__ import annotations

import json
import re
import struct
import time
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal


START = b"##"
RESP_SUCCESS = 0x01
RESP_ERROR = 0x02
RESP_COMMAND = 0xFE
ENCRYPT_NONE = 0x01
CLOUD_DEVICE_COCKPIT = 0x05

LengthEndian = Literal["auto", "big", "little"]


@dataclass
class Packet:
    """解析后的 TCP 帧。payload 保持原始字节，业务层按命令号再解。"""

    kind: Literal["cloud", "vehicle"]
    command_id: int
    response_flag: int
    unique_id: str
    encryption: int
    payload: bytes
    raw: bytes
    device_type: int | None = None
    timestamp_ms: int | None = None
    length_endian: Literal["big", "little"] = "big"
    data_length: int = 0
    bcc_valid: bool = True
    payload_schema: dict[str, Any] | None = None
    payload_name: str = ""
    decoded_payload: dict[str, Any] | None = None
    real_payload: dict[str, Any] | None = None
    parse_warnings: list[str] | None = None


def xor_bcc(data: bytes) -> int:
    """BCC 为逐字节异或；协议要求先加密后校验、先校验后解密。"""

    value = 0
    for byte in data:
        value ^= byte
    return value


def to_hex(data: bytes) -> str:
    return "-".join(f"{byte:02X}" for byte in data)


def u16(value: int) -> bytes:
    return int(value).to_bytes(2, "big", signed=False)


def u32(value: int) -> bytes:
    return int(value).to_bytes(4, "big", signed=False)


def fixed_ascii(value: object, length: int, pad: bytes = b"\x00") -> bytes:
    """协议里的 STRING 都是定长 ASCII；过长截断，不足用 0 补齐。"""

    raw = str(value or "").encode("ascii", errors="ignore")
    if len(raw) >= length:
        return raw[:length]
    return raw + pad * (length - len(raw))


def protocol_time(now: datetime | None = None) -> bytes:
    """表 6 的 8 字节时间：年/月/日/时/分/秒/毫秒，毫秒为 WORD 大端。"""

    current = now or datetime.now()
    return bytes(
        [
            current.year % 100,
            current.month,
            current.day,
            current.hour,
            current.minute,
            current.second,
        ]
    ) + u16(current.microsecond // 1000)


def unix_ms() -> int:
    return int(time.time() * 1000)


def parse_u16(payload: bytes, offset: int, default: int = 0) -> int:
    if offset + 2 > len(payload):
        return default
    return int.from_bytes(payload[offset : offset + 2], "big", signed=False)


@lru_cache(maxsize=8)
def load_protocol_schema(path: str = "protocol_packet_schema.json") -> dict[str, Any]:
    """加载 JSON Schema。缓存是为了避免 TCP 高频解析时反复读文件。"""

    schema_path = Path(path)
    if not schema_path.exists() and not schema_path.is_absolute():
        schema_path = Path(__file__).with_name(path)
    with open(schema_path, "r", encoding="utf-8") as file:
        return json.load(file)


def _command_id_value(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text, 16) if text.lower().startswith("0x") else int(text)


def _command_schemas(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    commands = schema.get("$defs", {}).get("commands", {})
    return {name: spec for name, spec in commands.items() if not name.endswith("Payload")}


def _schema_direction_matches_frame(direction: str, frame_kind: str) -> bool:
    if frame_kind == "cloud":
        return "cloud" in direction
    if frame_kind == "vehicle":
        return "vehicle" in direction
    return False


def _is_response_schema(name: str, spec: dict[str, Any]) -> bool:
    title = str(spec.get("title", ""))
    return "Response" in name or "response" in title.lower() or "响应" in title


def resolve_command_schema(
    schema: dict[str, Any],
    frame_kind: str,
    command_id: int,
    response_flag: int,
    *,
    direction: str | None = None,
    payload_length: int | None = None,
    warnings: list[str] | None = None,
) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    """按帧类型、命令号、方向/应答标识选择 payload schema。无法唯一匹配时会给 warning。"""

    notes = warnings if warnings is not None else []
    candidates: list[tuple[str, dict[str, Any]]] = []
    for name, spec in _command_schemas(schema).items():
        if _command_id_value(spec.get("x-commandId")) != command_id:
            continue
        schema_direction = str(spec.get("x-direction", ""))
        if direction and schema_direction != direction:
            continue
        if not direction and schema_direction and not _schema_direction_matches_frame(schema_direction, frame_kind):
            continue
        candidates.append((name, spec))

    if not candidates:
        notes.append(f"未知命令或未找到 schema: frame={frame_kind} cmd=0x{command_id:02X}")
        return None, None

    fixed = [
        item
        for item in candidates
        if payload_length is not None
        and item[1].get("x-fixedPayloadLength") is not None
        and int(item[1]["x-fixedPayloadLength"]) == payload_length
    ]
    if fixed:
        candidates = fixed

    if response_flag == RESP_COMMAND:
        command_candidates = [item for item in candidates if not _is_response_schema(*item)]
        if command_candidates:
            candidates = command_candidates
    else:
        response_candidates = [item for item in candidates if _is_response_schema(*item)]
        if response_candidates:
            candidates = response_candidates

    if len(candidates) > 1 and payload_length is not None:
        empty_match = [
            item for item in candidates if int(item[1].get("x-payloadLength", -1)) == payload_length
        ]
        if empty_match:
            candidates = empty_match

    if len(candidates) > 1:
        names = ", ".join(name for name, _ in candidates)
        notes.append(f"payload schema 多候选，使用第一个: {names}")
    return candidates[0]


def _resolve_ref(schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"只支持本地 schema ref: {ref}")
    node: Any = schema
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _eval_expr(expr: Any, variables: dict[str, int]) -> int:
    if isinstance(expr, int):
        return expr
    if expr is None:
        return 0
    text = str(expr).strip()
    if text.isdigit():
        return int(text)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[+\-*/() ]+", text):
        # 上面的正则对混合表达式不够直观，下面再做一次安全白名单。
        if not re.fullmatch(r"[A-Za-z0-9_+\-*/() ]+", text):
            raise ValueError(f"不支持的表达式: {expr}")
    return int(eval(text, {"__builtins__": {}}, variables))  # noqa: S307 - 输入来自本地 schema，且经过白名单限制。


def _field_size(field: dict[str, Any], payload_length: int, variables: dict[str, int]) -> int:
    if "size" in field and field["size"] not in (None, ""):
        return int(field["size"])
    if "sizeFrom" in field:
        return _eval_expr(field["sizeFrom"], {**variables, "dataLength": payload_length})
    field_type = str(field.get("type", ""))
    match = re.fullmatch(r"BYTE\[(\d+)]", field_type)
    if match:
        return int(match.group(1))
    return 0


def _decode_time8(raw: bytes) -> dict[str, int]:
    raw = raw.ljust(8, b"\x00")
    return {
        "year": raw[0],
        "month": raw[1],
        "day": raw[2],
        "hour": raw[3],
        "minute": raw[4],
        "second": raw[5],
        "millisecond": int.from_bytes(raw[6:8], "big"),
    }


def _decode_scalar(raw: bytes, field: dict[str, Any], schema: dict[str, Any]) -> Any:
    field_type = str(field.get("type", "BYTE"))
    if field.get("ref") == "#/$defs/primitives/time8":
        return _decode_time8(raw)
    if field_type == "BYTE":
        return raw[0] if raw else 0
    if field_type == "WORD":
        return int.from_bytes(raw.ljust(2, b"\x00")[:2], "big", signed=bool(field.get("signed")))
    if field_type == "DWORD":
        return int.from_bytes(raw.ljust(4, b"\x00")[:4], "big", signed=bool(field.get("signed")))
    if field_type == "DOUBLE":
        return struct.unpack(">d", raw.ljust(8, b"\x00")[:8])[0]
    if field_type == "STRING":
        return raw.decode("ascii", errors="ignore").rstrip("\x00")
    if field_type.startswith("BYTE["):
        text = raw.decode("ascii", errors="ignore")
        return text.rstrip("\x00") if text and all(32 <= ord(ch) < 127 for ch in text.rstrip("\x00")) else raw.hex()
    return raw.hex()


def _decode_fields(
    payload: bytes,
    fields: list[dict[str, Any]],
    schema: dict[str, Any],
    *,
    base_offset: int = 0,
    payload_length: int | None = None,
    warnings: list[str] | None = None,
) -> tuple[dict[str, Any], int]:
    decoded: dict[str, Any] = {}
    variables: dict[str, int] = {"dataLength": len(payload) if payload_length is None else payload_length}
    max_consumed = 0
    notes = warnings if warnings is not None else []

    for field in fields:
        name = field.get("name")
        offset = _eval_expr(field.get("offset", max_consumed), variables) - base_offset
        if field.get("countField"):
            count = int(decoded.get(field["countField"], 0) or 0)
            item_schema = _resolve_ref(schema, field["itemSchema"])
            item_size = int(field.get("itemSize", item_schema.get("x-itemSize", 0)))
            items = []
            for index in range(count):
                item_offset = offset + index * item_size
                item_raw = payload[item_offset : item_offset + item_size]
                if len(item_raw) < item_size:
                    notes.append(f"{name}[{index}] 字段越界")
                item, _ = _decode_fields(
                    item_raw,
                    item_schema.get("x-fields", []),
                    schema,
                    payload_length=item_size,
                    warnings=notes,
                )
                items.append(item)
            decoded[name] = items
            consumed = max(0, offset) + count * item_size
            variables[name] = count
            max_consumed = max(max_consumed, consumed)
            continue

        size = _field_size(field, len(payload), variables)
        if offset < 0:
            notes.append(f"{name} offset 为负数: {offset}")
            offset = 0
        raw = payload[offset : offset + size]
        if len(raw) < size:
            notes.append(f"{name} 字段越界: offset={offset} size={size} payload={len(payload)}")
        decoded[name] = _decode_scalar(raw, field, schema)
        if isinstance(decoded[name], int):
            variables[name] = decoded[name]
        elif isinstance(decoded[name], list):
            variables[name] = len(decoded[name])
        max_consumed = max(max_consumed, offset + size)

    if max_consumed < len(payload):
        notes.append(f"payload 剩余未解析字节: {len(payload) - max_consumed}")
    return decoded, max_consumed


def _enum_label_from_ref(schema: dict[str, Any], ref: str, value: Any) -> str | None:
    enum_schema = _resolve_ref(schema, ref)
    for item in enum_schema.get("oneOf", []):
        if item.get("const") == value:
            return item.get("title")
    return None


def _enum_label(inline_enum: dict[str, Any], value: Any) -> str | None:
    for key, label in inline_enum.items():
        try:
            expected = int(key, 16) if str(key).lower().startswith("0x") else int(key)
        except ValueError:
            continue
        if expected == value:
            return str(label)
    return None


def _special_status(field: dict[str, Any], value: Any) -> str | None:
    for key, label in field.get("specialValues", {}).items():
        try:
            expected = int(key, 16) if str(key).lower().startswith("0x") else int(key)
        except ValueError:
            continue
        if expected == value:
            return str(label)
    return None


def _apply_transform(transform: str, raw: int | float) -> tuple[float, str | None]:
    """支持 schema 当前出现的简单 transform：raw * a - b unit、raw - b unit。"""

    text = transform.strip()
    unit = None
    if text.endswith(" deg"):
        unit = "deg"
        text = text[:-4]
    elif text.endswith(" C"):
        unit = "C"
        text = text[:-2]
    if not re.fullmatch(r"raw|[0-9.]+|[+\-*/() ]+", text):
        if not re.fullmatch(r"[raw0-9.+\-*/() ]+", text):
            return float(raw), unit
    value = eval(text, {"__builtins__": {}}, {"raw": raw})  # noqa: S307 - transform 来自本地 schema 白名单。
    return float(value), unit


def _bits(value: int, width: int = 8) -> dict[str, bool]:
    return {f"bit{i}": bool(value & (1 << i)) for i in range(width)}


def _nice_float(value: float) -> float:
    rounded = round(value, 7)
    return int(rounded) if rounded.is_integer() else rounded


def _time_text(value: dict[str, Any]) -> str:
    year = int(value.get("year", 0))
    full_year = 2000 + year if year < 100 else year
    return (
        f"{full_year:04d}-{int(value.get('month', 0)):02d}-{int(value.get('day', 0)):02d} "
        f"{int(value.get('hour', 0)):02d}:{int(value.get('minute', 0)):02d}:"
        f"{int(value.get('second', 0)):02d}.{int(value.get('millisecond', 0)):03d}"
    )


def _real_scalar(raw_value: Any, field: dict[str, Any], schema: dict[str, Any]) -> Any:
    """把原始字段值解释成业务值：枚举标签、物理量、经纬度、bit 位等。"""

    if field.get("ref") == "#/$defs/primitives/time8" and isinstance(raw_value, dict):
        return {"raw": raw_value, "text": _time_text(raw_value)}

    if isinstance(raw_value, (int, float)):
        special = _special_status(field, raw_value)
        if special:
            return {"raw": raw_value, "status": special}

        label = None
        if field.get("enumRef"):
            label = _enum_label_from_ref(schema, field["enumRef"], raw_value)
        elif field.get("enum"):
            label = _enum_label(field["enum"], raw_value)
        if label is not None:
            return {"raw": raw_value, "label": label}

        if field.get("bitField"):
            return {"raw": raw_value, "bits": _bits(int(raw_value))}

        if field.get("transform"):
            value, unit = _apply_transform(str(field["transform"]), raw_value)
            result: dict[str, Any] = {"raw": raw_value, "value": _nice_float(value)}
            if unit:
                result["unit"] = unit
            return result

        if "scale" in field or "offsetDegrees" in field:
            value = float(raw_value) * float(field.get("scale", 1))
            if "offsetDegrees" in field:
                value += float(field["offsetDegrees"])
            result = {"raw": raw_value, "value": _nice_float(value)}
            if field.get("unit"):
                result["unit"] = field["unit"]
            elif "offsetDegrees" in field:
                result["unit"] = "deg"
            return result

    return raw_value


def _real_fields(decoded: dict[str, Any], fields: list[dict[str, Any]], schema: dict[str, Any]) -> dict[str, Any]:
    real: dict[str, Any] = {}
    field_by_name = {field.get("name"): field for field in fields}
    for name, value in decoded.items():
        field = field_by_name.get(name, {})
        if field.get("countField") and isinstance(value, list):
            item_schema = _resolve_ref(schema, field["itemSchema"])
            real[name] = [
                _real_fields(item, item_schema.get("x-fields", []), schema) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            real[name] = _real_scalar(value, field, schema)
    return real


def decode_packet_payload(
    packet: Packet,
    schema: dict[str, Any] | None = None,
    *,
    direction: str | None = None,
) -> Packet:
    """对已解析的通用帧补充 payload_name 与 decoded_payload。"""

    protocol_schema = schema or load_protocol_schema()
    warnings = list(packet.parse_warnings or [])
    name, command_schema = resolve_command_schema(
        protocol_schema,
        packet.kind,
        packet.command_id,
        packet.response_flag,
        direction=direction,
        payload_length=len(packet.payload),
        warnings=warnings,
    )
    packet.payload_schema = command_schema
    packet.payload_name = name or ""
    if command_schema:
        decoded, _ = _decode_fields(
            packet.payload,
            command_schema.get("x-fields", []),
            protocol_schema,
            payload_length=len(packet.payload),
            warnings=warnings,
        )
        packet.decoded_payload = decoded
        packet.real_payload = _real_fields(decoded, command_schema.get("x-fields", []), protocol_schema)
    packet.parse_warnings = warnings
    return packet


def _encode_time8(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).ljust(8, b"\x00")[:8]
    if isinstance(value, dict):
        return bytes(
            [
                int(value.get("year", 0)) & 0xFF,
                int(value.get("month", 1)) & 0xFF,
                int(value.get("day", 1)) & 0xFF,
                int(value.get("hour", 0)) & 0xFF,
                int(value.get("minute", 0)) & 0xFF,
                int(value.get("second", 0)) & 0xFF,
            ]
        ) + u16(int(value.get("millisecond", 0)))
    return protocol_time()


def _encode_scalar(value: Any, field: dict[str, Any], size: int) -> bytes:
    field_type = str(field.get("type", "BYTE"))
    if field.get("ref") == "#/$defs/primitives/time8":
        return _encode_time8(value)
    if field_type == "BYTE":
        return bytes([int(value or 0) & 0xFF])
    if field_type == "WORD":
        return u16(int(value or 0))
    if field_type == "DWORD":
        return u32(int(value or 0))
    if field_type == "DOUBLE":
        return struct.pack(">d", float(value or 0.0))
    if field_type == "STRING":
        return fixed_ascii(value, size)
    if field_type.startswith("BYTE["):
        if isinstance(value, (bytes, bytearray)):
            return bytes(value).ljust(size, b"\x00")[:size]
        return fixed_ascii(value, size)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).ljust(size, b"\x00")[:size]
    return bytes(size)


def _put_at(buffer: bytearray, offset: int, data: bytes) -> None:
    if offset < 0:
        raise ValueError(f"offset 不能为负数: {offset}")
    if len(buffer) < offset:
        buffer.extend(b"\x00" * (offset - len(buffer)))
    end = offset + len(data)
    if len(buffer) < end:
        buffer.extend(b"\x00" * (end - len(buffer)))
    buffer[offset:end] = data


def _default_scalar(field: dict[str, Any]) -> Any:
    field_type = str(field.get("type", "BYTE"))
    if field.get("ref") == "#/$defs/primitives/time8":
        return {"year": 26, "month": 1, "day": 1, "hour": 0, "minute": 0, "second": 0, "millisecond": 0}
    if field_type == "STRING" or field_type.startswith("BYTE["):
        return ""
    return 0


def _sample_scalar(field: dict[str, Any]) -> Any:
    name = str(field.get("name", ""))
    field_type = str(field.get("type", "BYTE"))
    if field.get("ref") == "#/$defs/primitives/time8":
        return {"year": 26, "month": 5, "day": 10, "hour": 12, "minute": 0, "second": 0, "millisecond": 123}
    if field_type == "STRING":
        size = int(field.get("size", 8) or 8)
        if "vin" in name.lower() or "unique" in name.lower():
            return "31X141304S0KE0274"[:size]
        if "ip" in name.lower():
            return "127.0.0.1"
        if "token" in name.lower():
            return "Bearer sim-access-token"
        if "version" in name.lower():
            return "0101"
        return ("SIM" + name)[:size]
    if field_type.startswith("BYTE["):
        size = _field_size(field, 0, {})
        return ("0101" if size == 4 else "").encode().ljust(size, b"\x00")
    if name.endswith("Count") or name in {"vehicleCount", "obstacleCount", "v2vVehicleCount", "pointCount"}:
        return 1
    if "Serial" in name or "serial" in name:
        return 1
    if "Status" in name or "status" in name or name in {"planningSuccess", "vehicleControllable", "tokenStatus"}:
        return 1
    if field_type == "DOUBLE":
        return 1.0
    return 1


def _array_fields(fields: list[dict[str, Any]]) -> dict[str, str]:
    return {field["countField"]: field["name"] for field in fields if field.get("countField")}


def _build_item_payload(item_schema: dict[str, Any], values: dict[str, Any], schema: dict[str, Any], *, sample: bool = False) -> bytes:
    return build_payload(item_schema, values, schema=schema, sample=sample)


def build_payload(
    command_schema: dict[str, Any],
    values: dict[str, Any] | None = None,
    *,
    schema: dict[str, Any] | None = None,
    sample: bool = False,
) -> bytes:
    """根据 command/item schema 构建 payload。缺失字段填安全默认值，sample=True 时填最小非空样例。"""

    protocol_schema = schema or load_protocol_schema()
    provided = values or {}
    fields = command_schema.get("x-fields", [])
    count_to_array = _array_fields(fields)
    variables: dict[str, int] = {"dataLength": 0}
    buffer = bytearray()

    for field in fields:
        name = field.get("name")
        offset = _eval_expr(field.get("offset", len(buffer)), variables)
        if field.get("countField"):
            item_schema = _resolve_ref(protocol_schema, field["itemSchema"])
            array_values = provided.get(name)
            if array_values is None:
                array_values = [{}] if sample else []
            item_bytes = bytearray()
            for item_value in array_values:
                item_bytes += _build_item_payload(item_schema, item_value or {}, protocol_schema, sample=sample)
            _put_at(buffer, offset, bytes(item_bytes))
            variables[name] = len(array_values)
            continue

        if name in provided:
            value = provided[name]
        elif name in count_to_array and count_to_array[name] in provided:
            value = len(provided.get(count_to_array[name]) or [])
        else:
            value = _sample_scalar(field) if sample else _default_scalar(field)

        # 变长字符串字段没有固定 size，例如 Bearer token；按输入长度写入。
        if "sizeFrom" in field and "size" not in field:
            if value in (None, ""):
                data = b""
            elif isinstance(value, (bytes, bytearray)):
                data = bytes(value)
            else:
                data = str(value).encode("ascii", errors="ignore")
        else:
            size = _field_size(field, len(buffer), variables)
            data = _encode_scalar(value, field, size)
        _put_at(buffer, offset, data)
        if isinstance(value, int):
            variables[name] = int(value)
        elif name in count_to_array:
            variables[name] = int(value or 0)
        variables["dataLength"] = len(buffer)
    return bytes(buffer)


def build_sample_payload(command_schema: dict[str, Any], *, schema: dict[str, Any] | None = None) -> bytes:
    return build_payload(command_schema, {}, schema=schema, sample=True)


def _response_flag_for_schema(name: str, spec: dict[str, Any]) -> int:
    explicit = spec.get("x-responseFlag")
    if explicit:
        text = str(explicit).split()[0]
        return _command_id_value(text) or RESP_COMMAND
    return RESP_SUCCESS if _is_response_schema(name, spec) else RESP_COMMAND


def build_packet_from_schema(
    frame_kind: Literal["cloud", "vehicle"],
    command_id: int,
    response_flag: int,
    unique_id: str,
    values: dict[str, Any] | None,
    schema: dict[str, Any] | None = None,
    *,
    direction: str | None = None,
    encryption: int = ENCRYPT_NONE,
) -> bytes:
    """按 schema 选择命令 payload 并构建完整 TCP 帧。"""

    protocol_schema = schema or load_protocol_schema()
    warnings: list[str] = []
    _, command_schema = resolve_command_schema(
        protocol_schema,
        frame_kind,
        command_id,
        response_flag,
        direction=direction,
        warnings=warnings,
    )
    if not command_schema:
        raise ValueError("; ".join(warnings) or f"未找到命令 schema: 0x{command_id:02X}")
    payload = build_payload(command_schema, values or {}, schema=protocol_schema)
    if frame_kind == "cloud":
        return build_cloud_frame(command_id, response_flag, unique_id, payload, encryption=encryption)
    return build_vehicle_frame(command_id, response_flag, unique_id, payload, encryption=encryption)


def build_sample_packet(
    frame_kind: Literal["cloud", "vehicle"],
    command_name: str,
    schema: dict[str, Any] | None = None,
    *,
    unique_id: str | None = None,
) -> bytes:
    """按命令 schema 生成最小非空样例帧，适合测试和现场临时报文生成。"""

    protocol_schema = schema or load_protocol_schema()
    command_schema = protocol_schema["$defs"]["commands"][command_name]
    command_id = _command_id_value(command_schema.get("x-commandId"))
    if command_id is None:
        raise ValueError(f"{command_name} 缺少 x-commandId")
    response_flag = _response_flag_for_schema(command_name, command_schema)
    payload = build_sample_payload(command_schema, schema=protocol_schema)
    if frame_kind == "cloud":
        return build_cloud_frame(command_id, response_flag, unique_id or "SYZKRMCAB20231001", payload)
    return build_vehicle_frame(command_id, response_flag, unique_id or "31X141304S0KE0274", payload)


def build_cloud_frame(
    command_id: int,
    response_flag: int,
    unique_id: str,
    payload: bytes = b"",
    *,
    device_type: int = CLOUD_DEVICE_COCKPIT,
    encryption: int = ENCRYPT_NONE,
    timestamp_ms: int | None = None,
) -> bytes:
    """云端↔驾驶舱端帧：## + 设备类型 + 命令单元 + 舱 ID + 加密 + 8 字节时间戳 + 长度 + payload + BCC。"""

    header = bytearray()
    header += START
    header.append(device_type)
    header.append(command_id & 0xFF)
    header.append(response_flag & 0xFF)
    header += fixed_ascii(unique_id, 17)
    header.append(encryption & 0xFF)
    header += int(timestamp_ms if timestamp_ms is not None else unix_ms()).to_bytes(8, "big")
    header += u16(len(payload))
    frame = bytes(header) + payload
    # 表 3 的“命令单元第一个字节”对应 offset=3，即不包含设备类型。
    return frame + bytes([xor_bcc(frame[3:])])


def build_vehicle_frame(
    command_id: int,
    response_flag: int,
    unique_id: str,
    payload: bytes = b"",
    *,
    encryption: int = ENCRYPT_NONE,
) -> bytes:
    """车端↔驾驶舱端帧：## + 命令单元 + VIN + 加密 + 长度 + payload + BCC。"""

    header = bytearray()
    header += START
    header.append(command_id & 0xFF)
    header.append(response_flag & 0xFF)
    header += fixed_ascii(unique_id, 17)
    header.append(encryption & 0xFF)
    header += u16(len(payload))
    frame = bytes(header) + payload
    return frame + bytes([xor_bcc(frame[2:])])


def _drop_until_start(buffer: bytearray) -> None:
    index = buffer.find(START)
    if index < 0:
        buffer.clear()
    elif index > 0:
        del buffer[:index]


def parse_cloud_frames(
    buffer: bytearray,
    *,
    decode_payload: bool = True,
    schema: dict[str, Any] | None = None,
    direction: str | None = None,
) -> list[Packet]:
    """从 TCP 缓冲区解析云端帧，支持半包和粘包。未完整的数据保留在 buffer。"""

    packets: list[Packet] = []
    protocol_schema = schema if decode_payload else None
    while True:
        _drop_until_start(buffer)
        if len(buffer) < 33:
            break
        length = int.from_bytes(buffer[31:33], "big", signed=False)
        total = 33 + length + 1
        if len(buffer) < total:
            break
        raw = bytes(buffer[:total])
        del buffer[:total]
        expected = xor_bcc(raw[3:-1])
        if expected != raw[-1]:
            raise ValueError(f"cloud BCC mismatch expected={expected:02X} actual={raw[-1]:02X}")
        packet = Packet(
            kind="cloud",
            command_id=raw[3],
            response_flag=raw[4],
            unique_id=raw[5:22].decode("ascii", errors="ignore").rstrip("\x00"),
            encryption=raw[22],
            timestamp_ms=int.from_bytes(raw[23:31], "big", signed=False),
            payload=raw[33:-1],
            raw=raw,
            device_type=raw[2],
            length_endian="big",
            data_length=length,
            bcc_valid=True,
        )
        if decode_payload:
            packet = decode_packet_payload(packet, protocol_schema, direction=direction)
        packets.append(packet)
    return packets


def _vehicle_length(buffer: bytearray, mode: LengthEndian) -> tuple[int, Literal["big", "little"]] | None:
    big = int.from_bytes(buffer[22:24], "big", signed=False)
    little = int.from_bytes(buffer[22:24], "little", signed=False)
    if mode == "big":
        return big, "big"
    if mode == "little":
        return little, "little"

    # 日志中车端链路混有小端长度字段；auto 优先协议大端，若大端不完整而小端完整则兼容小端。
    big_total = 24 + big + 1
    little_total = 24 + little + 1
    if len(buffer) >= big_total:
        return big, "big"
    if len(buffer) >= little_total:
        return little, "little"
    return None


def parse_vehicle_frames(
    buffer: bytearray,
    mode: LengthEndian = "auto",
    *,
    decode_payload: bool = True,
    schema: dict[str, Any] | None = None,
    direction: str | None = None,
) -> list[Packet]:
    """从 TCP 缓冲区解析车端帧；入站长度可按 auto/big/little 处理。"""

    packets: list[Packet] = []
    protocol_schema = schema if decode_payload else None
    while True:
        _drop_until_start(buffer)
        if len(buffer) < 24:
            break
        length_info = _vehicle_length(buffer, mode)
        if length_info is None:
            break
        length, endian = length_info
        total = 24 + length + 1
        if len(buffer) < total:
            break
        raw = bytes(buffer[:total])
        del buffer[:total]
        expected = xor_bcc(raw[2:-1])
        if expected != raw[-1]:
            raise ValueError(f"vehicle BCC mismatch expected={expected:02X} actual={raw[-1]:02X}")
        warnings = []
        if mode == "auto" and endian == "little":
            warnings.append("车端帧长度字段按小端兼容解析")
        packet = Packet(
            kind="vehicle",
            command_id=raw[2],
            response_flag=raw[3],
            unique_id=raw[4:21].decode("ascii", errors="ignore").rstrip("\x00"),
            encryption=raw[21],
            payload=raw[24:-1],
            raw=raw,
            length_endian=endian,
            data_length=length,
            bcc_valid=True,
            parse_warnings=warnings,
        )
        if decode_payload:
            packet = decode_packet_payload(packet, protocol_schema, direction=direction)
        packets.append(packet)
    return packets


def pack_vehicle_items(vehicles: Iterable[dict]) -> bytes:
    """云端车辆列表条目：VIN(17) + 紧急度 + 默认标记 + 车号 + 类型 + 工况 + 状态 + 故障等级。"""

    data = bytearray()
    vehicle_list = list(vehicles)
    data += u16(len(vehicle_list))
    for vehicle in vehicle_list:
        data += fixed_ascii(vehicle.get("vin"), 17)
        data.append(int(vehicle.get("urgency", 0)) & 0xFF)
        data.append(1 if vehicle.get("default") else 0)
        data += u16(int(vehicle.get("vehicle_no", 0)))
        data.append(int(vehicle.get("vehicle_type", 0)) & 0xFF)
        data.append(int(vehicle.get("work_mode", 1)) & 0xFF)
        data.append(int(vehicle.get("status", 1)) & 0xFF)
        data.append(int(vehicle.get("fault_level", 0)) & 0xFF)
    return bytes(data)


def pack_cloud_map_versions(config: dict) -> bytes:
    map_config = config.get("map", {})
    return fixed_ascii(map_config.get("road_version", ""), 32) + fixed_ascii(
        map_config.get("area_version", ""), 32
    )
