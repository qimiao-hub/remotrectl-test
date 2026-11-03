"""只启用独立环境文件路径，保留 Linux 既有网络、反馈及 DDS 运行配置。"""

import argparse
import json
import os
from pathlib import Path
import tempfile


def update_config(path, environment_file):
    """原子更新一个 JSON 的环境文件设置，保留其他字段与原权限；失败清理临时文件。"""
    mode = path.stat().st_mode & 0o777
    data = json.loads(path.read_text(encoding="utf-8"))
    data["dds_output"]["environment_config_file"] = environment_file
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="environment-enable-", delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main():
    """为既有部署启用已上传的演示文件；不编译、不测试，也不自动重启服务。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--deploy-root", required=True, type=Path)
    args = parser.parse_args()
    root = args.deploy_root.resolve()
    source = root / "source/sim_tcp_gui"
    environment = source / "environment_config.json"
    if not environment.is_file():
        raise FileNotFoundError(environment)
    update_config(source / "bridge_config.json", "environment_config.json")
    update_config(root / "run/bridge_config.json", str(environment))


if __name__ == "__main__":
    main()
