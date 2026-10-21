# HIL Interface Platform

Python/FastAPI interface platform for the TCP protocol in `TCP??????.md`.

## Features

- TCP server for the 14-byte protocol frame.
- REST API for reading and updating signal state.
- WebSocket stream for state and hardware events.
- Serial raw passthrough adapter.
- CAN adapter with DBC decoding.
- Xbox adapter with fully configurable mappings.
- Built-in Web GUI for signal, fault, frame, and gamepad debugging.

## Install

```powershell
python -m pip install -r requirements.txt
```

## Run From Source

```powershell
copy config.example.yaml config.yaml
python run_server.py
```

The HTTP API runs on port `8000`. The platform starts the TCP server from config, defaulting to `0.0.0.0:2000`.

You can still run with uvicorn directly:

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Build EXE

Build a Windows executable distribution:

```powershell
.\scriptsuild_exe.ps1
```

Output:

```text
dist\HILInterfacePlatform\HILInterfacePlatform.exe
dist\HILInterfacePlatform\config.yaml
```

Edit `dist\HILInterfacePlatform\config.yaml` to change HTTP API, TCP, serial, and CAN settings. The packaged exe uses the browser Gamepad API in the Web GUI for Xbox debugging and binding; the optional backend pygame Xbox adapter is intended for source runs. Then start:

```powershell
cd dist\HILInterfacePlatform
.\HILInterfacePlatform.exe
```

The executable also supports overrides:

```powershell
.\HILInterfacePlatform.exe --config D:\path	o\config.yaml --host 0.0.0.0 --port 8000
```

## GUI

Open the built-in web console after the service starts:

```text
http://127.0.0.1:8000/
```

The GUI connects to the FastAPI HTTP/WebSocket service, not the TCP protocol port. Use `127.0.0.1` and `8000` for local service testing, then click `??`.

The console shows grouped flag-bit states, fault states, `Steering`/`Throttle`/`Brake` controls, TCP frame preview, and a live event log. Clicking a flag, fault button, or changing a numeric control writes through `PATCH /api/v1/state` with `source: "api"`.

## API

- `GET /health`
- `GET /api/v1/signals`
- `GET /api/v1/state`
- `PATCH /api/v1/state`
- `POST /api/v1/serial/send`
- `POST /api/v1/can/send`
- `WS /ws/v1/state`

Example state update:

```json
{
  "source": "api",
  "signals": {
    "Throttle": 50,
    "Brake": 0,
    "Steering": -120
  }
}
```

## TCP Protocol

Frame length is 14 bytes:

- byte 0: version, default `0x0C`
- byte 1: message type, `0x00` read or `0xFF` write
- byte 2: message id
- byte 3: message length
- bytes 4-13: packed signal data

Read requests return the current complete frame. Write requests parse the 10-byte data payload, update state, then return the current complete frame.
