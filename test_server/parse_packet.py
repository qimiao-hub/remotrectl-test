from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from sim_protocol import load_protocol_schema, parse_cloud_frames, parse_vehicle_frames, to_hex


def normalize_hex(text: str) -> bytes:
    """兼容 23-23-xx、23 23 xx、2323xx 三种手工输入格式。"""

    cleaned = re.sub(r"[^0-9A-Fa-f]", "", text)
    if not cleaned:
        raise ValueError("没有读取到十六进制内容")
    if len(cleaned) % 2:
        raise ValueError("十六进制字符数量必须为偶数")
    return bytes.fromhex(cleaned)


def packet_to_dict(packet) -> dict[str, Any]:
    return {
        "frame_kind": packet.kind,
        "command_id": f"0x{packet.command_id:02X}",
        "response_flag": f"0x{packet.response_flag:02X}",
        "unique_id": packet.unique_id,
        "device_type": packet.device_type,
        "encryption": f"0x{packet.encryption:02X}",
        "timestamp_ms": packet.timestamp_ms,
        "data_length": packet.data_length,
        "length_endian": packet.length_endian,
        "bcc_valid": packet.bcc_valid,
        "payload_name": packet.payload_name,
        "decoded_payload": packet.decoded_payload,
        "real_payload": packet.real_payload,
        "parse_warnings": packet.parse_warnings or [],
        "raw_hex": to_hex(packet.raw),
    }


def try_parse(data: bytes, schema: dict[str, Any]) -> tuple[str, list[Any], list[str]]:
    """同一段输入分别尝试云端帧和车端帧；谁能完整消费输入就采用谁。"""

    attempts: list[tuple[str, list[Any], bytearray, str | None]] = []
    for name, parser in (
        ("cloud", parse_cloud_frames),
        ("vehicle", parse_vehicle_frames),
    ):
        buffer = bytearray(data)
        try:
            packets = parser(buffer, schema=schema)
            attempts.append((name, packets, buffer, None))
        except Exception as exc:  # noqa: BLE001 - 手工工具要把解析失败原因展示出来。
            attempts.append((name, [], bytearray(data), str(exc)))

    complete = [(name, packets) for name, packets, rest, err in attempts if packets and not rest and err is None]
    if len(complete) == 1:
        return complete[0][0], complete[0][1], []
    if len(complete) > 1:
        # 理论上极少同时匹配；保留两个结果中的第一个，并提醒用户可看 raw 信息确认。
        return complete[0][0], complete[0][1], [f"输入同时匹配多个帧类型: {', '.join(name for name, _ in complete)}"]

    errors = []
    for name, packets, rest, err in attempts:
        if err:
            errors.append(f"{name}: {err}")
        elif packets and rest:
            errors.append(f"{name}: 已解析 {len(packets)} 帧，但剩余 {len(rest)} 字节未消费")
        else:
            errors.append(f"{name}: 未解析到完整帧")
    raise ValueError("; ".join(errors))


def parse_text(text: str, schema_path: str) -> dict[str, Any]:
    schema = load_protocol_schema(schema_path)
    data = normalize_hex(text)
    frame_kind, packets, warnings = try_parse(data, schema)
    return {
        "input_size": len(data),
        "detected_frame_kind": frame_kind,
        "warnings": warnings,
        "packets": [packet_to_dict(packet) for packet in packets],
    }


def print_result(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def interactive(schema_path: str) -> int:
    print("请输入报文十六进制，例如：23-23-05-0C ... 或 23 23 05 0C ...")
    print("输入空行或 exit 退出。")
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line or line.lower() in {"exit", "quit", "q"}:
            break
        try:
            print_result(parse_text(line, schema_path))
        except Exception as exc:  # noqa: BLE001 - 交互工具直接显示错误，方便现场继续输入。
            print(f"解析失败：{exc}", file=sys.stderr)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="手动解析云端/车端 TCP 报文，输出真实业务数据")
    parser.add_argument("hex", nargs="*", help="报文十六进制，支持 23-23-xx、23 23 xx、2323xx")
    parser.add_argument("--schema", default="protocol_packet_schema.json", help="协议 schema 路径")
    parser.add_argument("--file", help="从文件读取报文十六进制")
    parser.add_argument("--interactive", "-i", action="store_true", help="进入交互输入模式")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.interactive or (not args.hex and not args.file):
        return interactive(args.schema)
    if args.file:
        with open(args.file, "r", encoding="utf-8") as file:
            text = file.read()
    else:
        text = " ".join(args.hex)
    print_result(parse_text(text, args.schema))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
