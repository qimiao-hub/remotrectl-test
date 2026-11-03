# HIL Interface Platform

Python/FastAPI interface platform for the TCP protocol in `TCP??????.md`.

## Features

- TCP server with runtime output selection: T1 (14 bytes) and T1-3 (15 bytes).
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

Default output is **T1**, 14 bytes. The **TCP 输出协议** dropdown in the TCP frame panel switches the running server between **T1** and **T1-3**. Connect the webpage first; subsequent TCP responses use the selected format without reconnecting the TCP client. Existing client software must support the selected response length.

- byte 0: version, default `0x0C`
- byte 1: message type, `0x00` read or `0xFF` write
- byte 2: message id
- byte 3: message length
- bytes 4-13: packed signal data

**T1-3** keeps the existing fields and appends an independent `Brake2` byte at offset **14** (the fifteenth byte). Like `Brake`, it is unsigned, 0–100%, precision 1, initially 0. It does not copy or change `Brake` at offset 12; the fault byte remains at offset 13. Total length is 15 (`0x0F`), payload length 11 (`0x0B`); default T1 remains total 14 (`0x0E`), payload 10 (`0x0A`). Header version remains `0x0C`.

Choosing T1-3 shows the **扩展通道（T1-3） / Brake2** slider and numeric input, including optional gamepad binding. Returning to T1 hides this control and omits its byte while preserving its value for the next switch. Frame preview uses the same encoder and selected protocol as real TCP responses. A switch is global to the server, and connected webpages receive a WebSocket event to stay synchronized.

Read requests return the current complete selected output frame. Incoming T1 and T1-3 write frames are accepted independently of the selected output protocol; T1 writes do not reset `Brake2`. Both total-length and payload-length request conventions remain supported; responses retain the request's convention and adjust its length to the selected output protocol. There is no unsolicited TCP push: selection changes the next response to a client request.

`GET /api/v1/tcp/protocol` returns the current protocol and frame lengths. Switch with `PATCH /api/v1/tcp/protocol`:

```json
{"protocol":"T1-3"}
```

Write independent channel values with the existing `PATCH /api/v1/state`:

```json
{"source":"api","signals":{"Brake":20,"Brake2":70}}
```

Runtime selection is not written back to YAML. Configure the startup choice using `tcp.protocol: T1` (default) or `tcp.protocol: T1-3`. Restart a source-run HIL service to load these code changes; prebuilt executables need a separate rebuild and are not updated by editing source files.
