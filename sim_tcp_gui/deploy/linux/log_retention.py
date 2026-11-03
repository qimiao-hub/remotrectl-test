"""配置 remotectl 专用日志保留策略，不清理系统其他服务日志。"""
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path("/home/qi/remotectl-integration")


def clear_logs():
    """清空部署目录内的应用日志文件；保留文件 inode，避免运行进程继续写入已删除文件。"""
    log_root = ROOT / "logs"
    paths = list(log_root.rglob("*.log")) + list(log_root.rglob("*.jsonl"))
    paths.append(ROOT / "source/sim_tcp_gui/dds_bridge.log")
    for path in paths:
        try:
            # 仅处理普通文件，拒绝符号链接，防止清理范围越过应用部署目录。
            if not stat.S_ISREG(path.lstat().st_mode) or not path.resolve().is_relative_to(ROOT):
                continue
            with path.open("r+") as output:
                output.truncate(0)
        except FileNotFoundError:
            continue


def install():
    """安装五分钟清空定时器及独立 journal 命名空间配置，随后使服务使用新日志策略。"""
    files = {
        "/etc/systemd/journald@remotectl.conf": "[Journal]\nStorage=volatile\nMaxRetentionSec=5min\nMaxFileSec=1min\nRuntimeMaxUse=32M\n",
        "/etc/systemd/system/remotectl-log-clear.service": (
            "[Unit]\nDescription=Clear remotectl application logs\n[Service]\nType=oneshot\n"
            f"ExecStart=/usr/bin/python3 {ROOT}/run/log_retention.py\n"
            "ExecStartPost=/usr/bin/journalctl --namespace=remotectl --rotate --vacuum-time=1s\n"),
        "/etc/systemd/system/remotectl-log-clear.timer": (
            "[Unit]\nDescription=Clear remotectl logs every five minutes\n[Timer]\n"
            "OnActiveSec=5min\nOnUnitActiveSec=5min\nAccuracySec=1s\n"
            "[Install]\nWantedBy=timers.target\n"),
    }
    for name in ("remotectl-cloud", "remotectl-simulator"):
        files[f"/etc/systemd/system/{name}.service.d/log-retention.conf"] = "[Service]\nLogNamespace=remotectl\n"
    for filename, content in files.items():
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "remotectl-log-clear.timer"], check=True)
    subprocess.run(["systemctl", "restart", "remotectl-simulator", "remotectl-cloud"], check=True)


if __name__ == "__main__":
    if "--install" in sys.argv:
        install()
    else:
        clear_logs()
