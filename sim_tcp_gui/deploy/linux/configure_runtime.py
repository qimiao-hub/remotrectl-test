"""为 Linux 部署生成独立运行配置和 systemd 单元，不修改源码中的默认示例配置。"""
import argparse
import json
import re
from pathlib import Path


def main():
    """读取部署目录和驾驶舱地址，生成桥/舱云配置与两个服务单元；所选车辆 YAML 由云端会话生成。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--deploy-root", type=Path, required=True)
    parser.add_argument("--cockpit-address", required=True)
    parser.add_argument("--user", default="qi")
    parser.add_argument("--public-host", default="192.168.48.128")
    args = parser.parse_args()
    root = args.deploy_root.resolve()
    run = root / "run"
    run.mkdir(parents=True, exist_ok=True)
    config = json.loads((root / "source/sim_tcp_gui/bridge_config.json").read_text())
    config["hil"]["enabled"] = False
    config["vehicle_output"]["enabled"] = False
    config["dds_output"].update(executable=str(root / "build/bin/sim-dds-bridge"),
                                cockpit_address=args.cockpit_address,
                                topics_config=str(root / "source/remotectl/config/dds_topics.json"))
    (run / "bridge_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    cloud = json.loads((root / "source/test_server/simulator.json").read_text())
    host, port = args.cockpit_address.rsplit(":", 1)
    cloud["cockpit"].update(ip=host, port=int(port))
    cloud["vehicle_backend"] = "remotectl-dds"
    cloud["native_vehicle"] = {
        "executable": str(root / "build/bin/syzk-remotectl"),
        "dds_config": str(root / "source/remotectl/config/dds_topics.json"),
        "remote_config": str(root / "source/remotectl/config/remote_config.sim.ini"),
        "runtime_dir": str(run / "native-vehicles"), "vehicle_type": config["dds_output"]["vehicle_type"],
        "log_mode": "journal",
    }
    cloud["map"]["base_url"] = f"http://{args.public_host}:{cloud['http']['port']}/static/map"
    header = (root / "source/remotectl/include/software_version.h").read_text()
    version = re.search(r'kSoftwareVersion\[\]\s*=\s*"([^"]+)"', header)
    if not version:
        raise ValueError("cannot read native software version")
    for vehicle_item in cloud.get("vehicles", []):
        vehicle_item["software_version"] = version.group(1)
    cloud_path = run / "cloud_config.json"
    cloud_path.write_text(json.dumps(cloud, ensure_ascii=False, indent=2) + "\n")
    cloud_path.chmod(0o600)  # 云端测试账户只允许部署用户读取，不复制 SSH 登录凭据。
    units = {
        "remotectl-simulator": (
            "Remotectl vehicle dynamics and native DDS bridge",
            f"Environment=SIM_BRIDGE_CONFIG={run}/bridge_config.json\n",
            f"{root}/venv/bin/python {root}/source/sim_tcp_gui/simulator.py",
            ""),
        "remotectl-cloud": (
            "Remotectl HTTP and cockpit cloud TCP with native vehicle supervisor",
            "",
            f"{root}/venv/bin/python {root}/source/test_server/run_simulator.py --config {run}/cloud_config.json --log-dir {root}/logs/cloud --vehicle-length-endian big",
            "After=remotectl-simulator.service\nWants=remotectl-simulator.service\n"),
    }
    for name, (description, environment, executable, dependencies) in units.items():
        content = f"""[Unit]
Description={description}
After=network-online.target
Wants=network-online.target
{dependencies}
[Service]
Type=simple
User={args.user}
WorkingDirectory={root}/source/sim_tcp_gui
Environment=PYTHONUNBUFFERED=1
{environment}ExecStart={executable}
Restart=on-failure
RestartSec=3
KillMode=control-group
TimeoutStopSec=8
StandardOutput=journal
StandardError=journal
LogRateLimitIntervalSec=30s
LogRateLimitBurst=200

[Install]
WantedBy=multi-user.target
"""
        (run / (name + ".service")).write_text(content)
    print("runtime configuration and service units generated:", run)


if __name__ == "__main__":
    main()
