from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "1")

import uvicorn


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resolve_config_path(config_arg: str | None) -> Path:
    base_dir = application_dir()
    if config_arg:
        return Path(config_arg).expanduser().resolve()
    if os.environ.get("HIL_CONFIG"):
        return Path(os.environ["HIL_CONFIG"]).expanduser().resolve()
    return base_dir / "config.yaml"


def ensure_config_file(config_path: Path) -> None:
    if config_path.exists():
        return

    example_path = application_dir() / "config.example.yaml"
    if not example_path.exists():
        print(f"Config file not found: {config_path}")
        print("Create config.yaml next to the executable or pass --config <path>.")
        return

    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example_path, config_path)
    print(f"Created default config: {config_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the HIL Interface Platform service.")
    parser.add_argument("--config", help="Path to config.yaml. Defaults to the executable directory.")
    parser.add_argument("--host", help="Override the HTTP API host from config.yaml.")
    parser.add_argument("--port", type=int, help="Override the HTTP API port from config.yaml.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = resolve_config_path(args.config)
    ensure_config_file(config_path)
    os.environ["HIL_CONFIG"] = str(config_path)

    from app.main import app

    settings = app.state.settings
    host = args.host or settings.api.host
    port = args.port or settings.api.port

    print("HIL Interface Platform")
    print(f"Config: {config_path}")
    print(f"HTTP GUI/API: http://127.0.0.1:{port}/")
    print(f"HTTP bind: {host}:{port}")
    if settings.tcp.enabled:
        print(f"TCP bind: {settings.tcp.host}:{settings.tcp.port}")

    uvicorn.run(app, host=host, port=port, log_level="info", loop="asyncio", http="h11", ws="websockets")


if __name__ == "__main__":
    main()
