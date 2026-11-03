"""在 Linux 部署目录运行真实 DDS/TCP 闭环，驾驶舱测试端仅绑定回环地址。"""
import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from state_manager import StateManager
from vehicle_protocol import protocol_time, xor_bcc

VIN = b"SYZKRMCAB25090501"


def frame(command, payload, ack=0xFE):
    """按车舱双向大端长度构造驾驶舱报文，并在末尾追加 XOR 校验字节。"""
    data = b"##" + bytes((command, ack)) + VIN + b"\x01" + len(payload).to_bytes(2, "big") + payload
    return data + bytes((xor_bcc(data[2:]),))


class Cockpit:
    """接受车端连接并执行登录、接管、连续控车及心跳，不连接实际车辆或驾驶舱。"""

    def __init__(self):
        """监听随机回环端口，初始化可在运行时切换的控车阶段与接收统计。"""
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.counts = Counter()
        self.last_report = b""
        self.report_times = []
        self.last_surround = b""
        self.error = None
        self.phase = "brake"
        self.collection = 0
        self.emergency = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        """处理 TCP 拆粘包并记录 0x02 到达时间，验证 XOR、大端长度及周期控制。"""
        try:
            while not self.stop.is_set():
                try:
                    connection, _ = self.listener.accept()
                    break
                except socket.timeout:
                    continue
            else:
                return
            with connection:
                connection.settimeout(0.01)
                buffer = bytearray()
                logged_in = False
                next_send = 0
                sequence = 0
                while not self.stop.is_set():
                    try:
                        data = connection.recv(4096)
                        if not data:
                            return
                        buffer.extend(data)
                    except socket.timeout:
                        pass
                    while len(buffer) >= 25:
                        if buffer[:2] != b"##":
                            raise AssertionError("invalid TCP header")
                        length = 25 + int.from_bytes(buffer[22:24], "big")
                        if length > 1024:
                            raise AssertionError("TCP packet exceeds slot capacity")
                        if len(buffer) < length:
                            break
                        packet = bytes(buffer[:length])
                        del buffer[:length]
                        if xor_bcc(packet[2:-1]) != packet[-1]:
                            raise AssertionError("vehicle TCP XOR mismatch")
                        command, ack, payload = packet[2], packet[3], packet[24:-1]
                        with self.lock:
                            self.counts[f"{command:02x}/{ack:02x}"] += 1
                            if command == 2:
                                self.last_report = payload
                                self.report_times.append(time.monotonic())
                            elif command == 7:
                                self.last_surround = payload
                        if command == 1 and ack == 0xFE:
                            # 文档的登录应答为 925 字节，保留 915 字节密钥区，当前无加密。
                            response = protocol_time() + payload[8:9] + b"\x00" + bytes(915)
                            connection.sendall(frame(1, response, 1))
                            connection.sendall(frame(3, protocol_time() + b"\x01\x01"))
                            logged_in = True
                    now = time.monotonic()
                    if logged_in and now >= next_send:
                        sequence = (sequence + 1) % 256
                        with self.lock:
                            phase, collection, emergency = self.phase, self.collection, self.emergency
                            self.emergency = None
                        throttle, brake = (0, 80) if phase == "brake" else (35, 0)
                        if phase != "silence":
                            payload = protocol_time() + bytes((sequence,)) + (450).to_bytes(2, "big")
                            payload += bytes((throttle, brake, 3, collection << 6, 0, 0, 0, 0, 0))
                            connection.sendall(frame(4, payload))
                        connection.sendall(frame(5, protocol_time() + sequence.to_bytes(2, "big")))
                        if emergency is not None:
                            connection.sendall(frame(6, protocol_time() + bytes((sequence, emergency))))
                        next_send = now + 0.05
        except Exception as error:
            if not self.stop.is_set():
                self.error = str(error)

    def close(self):
        """停止测试端并释放监听 Socket，最多等待一秒处理线程退出。"""
        self.stop.set()
        self.listener.close()
        self.thread.join(timeout=1)


def wait_for(manager, cockpit, kind, timeout=12):
    """等待实际进程状态满足指定业务条件；超时携带 DDS 状态与最近控制返回错误。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cockpit.error:
            raise AssertionError(cockpit.error)
        state = manager.snapshot()
        dds, vehicle = state["dds"], state["vehicle"]
        with cockpit.lock:
            report, surround, counts = cockpit.last_report, cockpit.last_surround, dict(cockpit.counts)
        passed = {
            "matched": dds["matched_wire"] >= 1 and dds["matched_address"] >= 1 and dds["address_ack"] == 1,
            "login": counts.get("02/fe", 0) >= 2 and counts.get("03/01", 0) >= 1,
            "brake_ready": len(report) == 40 and bool(report[32] & 0x20),
            "moving": vehicle["speed_kph"] > 1 and state["controls"]["throttle"] > 0
                and state["controls"]["brake"] == 0 and len(report) == 40
                and int.from_bytes(report[8:10], "big") > 0,
            "surround": len(surround) == 51 and int.from_bytes(surround[18:20], "big") == 2,
            "collected": len(report) == 40 and (report[31] >> 6) == 2,
            "emergency": counts.get("06/01", 0) >= 1 and state["controls"]["brake"] == 100,
            "timeout": state["controls"]["brake"] == 100 and state["controls"]["parking_brake"],
        }[kind]
        if passed:
            return {"dds": dds, "vehicle": vehicle, "controls": state["controls"], "tcp_counts": counts}
        time.sleep(0.05)
    raise AssertionError(f"timeout waiting for {kind}: {manager.dds_bridge.status()}")


def main():
    """构建使用既有内联环境夹具的独立 Domain 闭环并统计 0x02 周期；退出清理测试进程。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--deploy-root", type=Path, required=True)
    parser.add_argument("--with-cloud", action="store_true")
    args = parser.parse_args()
    root = args.deploy_root.resolve()
    test_dir = root / "run" / "smoke"
    test_dir.mkdir(parents=True, exist_ok=True)
    topics = json.loads((root / "source/remotectl/config/dds_topics.json").read_text())
    topics["domain_id"] = 73
    topics_path = test_dir / "dds_topics.json"
    topics_path.write_text(json.dumps(topics))
    config = json.loads((root / "source/sim_tcp_gui/bridge_config.json").read_text())
    # 此脚本使用固定内联列表校验旧场景，独立环境文件的验收不覆盖这些夹具。
    config["dds_output"].pop("environment_config_file", None)
    config["hil"]["enabled"] = False
    config["vehicle_output"]["enabled"] = False
    cockpit = Cockpit()
    config["dds_output"].update(executable=str(root / "build/bin/sim-dds-bridge"),
                                topics_config=str(topics_path), cockpit_address=f"127.0.0.1:{cockpit.port}")
    config["dds_output"]["front_obstacles"] = [
        {"type": 0, "box": {"object_type": 1, "center": [1, 15, 0], "object_width": 3, "object_height": 4}},
        {"type": 1, "scatter": {"center": [-2, 12, 0]}}]
    config["dds_output"]["round_vehicles"] = [{"device_type": 1, "longitude": 116, "latitude": 40, "heading": 90}]
    env = dict(os.environ, REMOTECTL_DDS_CONFIG=str(topics_path),
               REMOTECTL_VEHICLE_CONFIG=str(root / "source/remotectl/config/runSetup.example.yaml"),
               REMOTECTL_REMOTE_CONFIG=str(root / "source/remotectl/config/remote_config.sim.ini"))
    manager = None
    process = None
    cloud_process = None
    cloud_connection = None
    cloud_log = None
    result = {"started": datetime.now().isoformat(), "checks": {}, "success": False}
    log = (root / "logs/smoke-remotectl.log").open("w")
    try:
        manager = StateManager(bridge_config=config)
        if args.with_cloud:
            # 使用独立回环端口和测试账户验证舱云触发链路，不占用正式部署的 18080/25300。
            sys.path.insert(0, str(root / "source/test_server"))
            from sim_protocol import build_cloud_frame, parse_cloud_frames
            cloud_config = {
                "http": {"host": "127.0.0.1", "port": 18082},
                "tcp": {"host": "127.0.0.1", "port": 25302},
                "cockpit": {"id": "SYZKRMCAB20231001", "ip": "127.0.0.1", "port": cockpit.port},
                "map": {}, "accounts": [{"mobile": "integration", "password": "local-test", "token": "test-token"}],
                "vehicles": [{"vin": VIN.decode(), "iccid": "1234567890123456789", "default": True}],
                "vehicle_backend": "remotectl-dds",
                "native_vehicle": {"executable": str(root / "build/bin/syzk-remotectl"),
                    "dds_config": str(topics_path),
                    "remote_config": str(root / "source/remotectl/config/remote_config.sim.ini"),
                    "runtime_dir": str(test_dir / "cloud-vehicles")},
            }
            cloud_path = test_dir / "cloud_config.json"
            cloud_path.write_text(json.dumps(cloud_config))
            cloud_log = (root / "logs/smoke-cloud.log").open("w")
            cloud_process = subprocess.Popen([sys.executable, str(root / "source/test_server/run_simulator.py"),
                "--config", str(cloud_path), "--log-dir", str(root / "logs/smoke-cloud")],
                stdout=cloud_log, stderr=cloud_log, env=dict(os.environ, PYTHONUNBUFFERED="1"))
            deadline = time.monotonic() + 10
            while True:
                try:
                    cloud_connection = socket.create_connection(("127.0.0.1", 25302), timeout=0.5)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise AssertionError("cloud TCP service did not start")
                    time.sleep(0.1)
            request = urllib.request.Request("http://127.0.0.1:18082/api/user/account/login",
                data=json.dumps({"mobile": "integration", "password": "local-test"}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=3) as response:
                if response.status != 200 or not json.load(response)["data"]["access_token"]:
                    raise AssertionError("cloud HTTP login failed")
            result["checks"]["cloud_http"] = {"login_status": 200}
            print("PASS cloud_http", flush=True)
            identity = "SYZKRMCAB20231001"
            cloud_connection.sendall(build_cloud_frame(9, 0xFE, identity, b"\x00\x01"))
            cloud_connection.sendall(build_cloud_frame(10, 0xFE, identity))
            cloud_connection.sendall(build_cloud_frame(11, 0xFE, identity, VIN + b"\x00\x02"))
            cloud_buffer = bytearray()
            responses = set()
            while not {9, 10, 11}.issubset(responses):
                cloud_buffer.extend(cloud_connection.recv(4096))
                for packet in parse_cloud_frames(cloud_buffer):
                    if packet.response_flag == 1 and packet.bcc_valid:
                        responses.add(packet.command_id)
            result["checks"]["cloud_tcp"] = {"response_commands": sorted(responses)}
            print("PASS cloud_tcp", flush=True)
        else:
            process = subprocess.Popen([str(root / "build/bin/syzk-remotectl")], env=env, stdout=log, stderr=log)
        for kind in ("matched", "login", "brake_ready"):
            result["checks"][kind] = wait_for(manager, cockpit, kind)
            print("PASS", kind, flush=True)
        with cockpit.lock:
            cockpit.phase = "drive"
        for kind in ("moving", "surround"):
            result["checks"][kind] = wait_for(manager, cockpit, kind)
            print("PASS", kind, flush=True)
        with cockpit.lock:
            cockpit.collection = 1
        time.sleep(1)
        with cockpit.lock:
            cockpit.collection = 2
        result["checks"]["collected"] = wait_for(manager, cockpit, "collected")
        print("PASS collected", flush=True)
        with cockpit.lock:
            cockpit.emergency = 1
        result["checks"]["emergency"] = wait_for(manager, cockpit, "emergency")
        print("PASS emergency", flush=True)
        with cockpit.lock:
            cockpit.emergency = 0
        result["checks"]["recovered"] = wait_for(manager, cockpit, "moving")
        print("PASS recovered", flush=True)
        if cloud_connection:
            # 0x12 的 1 是 ended，必须回收原生车端，不能把它误当成重新触发控车。
            cloud_connection.sendall(build_cloud_frame(0x12, 0xFE, identity, b"\x01"))
        else:
            process.terminate()
            process.wait(timeout=5)
        result["checks"]["timeout"] = wait_for(manager, cockpit, "timeout", timeout=4)
        print("PASS timeout", flush=True)
        result["success"] = True
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        if cloud_connection:
            cloud_connection.close()
        if cloud_process and cloud_process.poll() is None:
            cloud_process.terminate()
            cloud_process.wait(timeout=8)
        if cloud_log:
            cloud_log.close()
        if manager:
            manager.close()
        cockpit.close()
        log.close()
        intervals = [later - earlier for earlier, later in zip(cockpit.report_times, cockpit.report_times[1:])]
        if intervals:
            ordered = sorted(intervals)
            result["realtime_timing"] = {"samples": len(cockpit.report_times),
                "median_ms": statistics.median(intervals) * 1000,
                "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] * 1000,
                "mean_hz": len(intervals) / sum(intervals)}
        filename = "cloud-e2e-report.json" if args.with_cloud else "e2e-report.json"
        (root / "artifacts" / filename).write_text(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
