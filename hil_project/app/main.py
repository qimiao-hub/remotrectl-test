from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.adapters.can_adapter import CanAdapter
from app.adapters.serial_adapter import SerialAdapter
from app.adapters.xbox_adapter import XboxAdapter
from app.config import Settings, load_settings
from app.events import EventBus
from app.protocol import encode_state
from app.signals import data_signal_metadata
from app.state import SignalRangeError, StateStore, UnknownSignalError
from app.tcp_server import TcpProtocolServer

LOGGER = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


class StatePatchRequest(BaseModel):
    signals: dict[str, int | float | bool]
    source: str = "api"


class SerialSendRequest(BaseModel):
    hex: str | None = None
    text: str | None = None


class CanSendRequest(BaseModel):
    arbitration_id: int
    data_hex: str = Field(default="")
    is_extended_id: bool = False


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    components = [
        app.state.tcp_server,
        app.state.serial_adapter,
        app.state.can_adapter,
        app.state.xbox_adapter,
    ]
    started: list[Any] = []
    try:
        for component in components:
            try:
                await component.start()
                started.append(component)
            except Exception as exc:
                LOGGER.error("Failed to start %s: %s", component.__class__.__name__, exc)
        yield
    finally:
        for component in reversed(started):
            with contextlib.suppress(Exception):
                await component.stop()


def create_app(settings: Settings | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    settings = settings or load_settings()
    event_bus = EventBus()
    state_store = StateStore(event_bus, settings.state.source_priority)

    app = FastAPI(title="HIL Interface Platform", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.event_bus = event_bus
    app.state.state_store = state_store
    app.state.tcp_server = TcpProtocolServer(settings.tcp, state_store)
    app.state.serial_adapter = SerialAdapter(settings.serial, event_bus)
    app.state.can_adapter = CanAdapter(settings.can, event_bus, state_store)
    app.state.xbox_adapter = XboxAdapter(settings.xbox, event_bus, state_store)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def gui() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "tcp_enabled": settings.tcp.enabled,
            "serial_enabled": settings.serial.enabled,
            "can_enabled": settings.can.enabled,
            "xbox_enabled": settings.xbox.enabled,
        }

    @app.get("/api/v1/state")
    async def get_state() -> dict[str, Any]:
        return state_store.as_response()

    @app.get("/api/v1/signals")
    async def get_signals() -> dict[str, Any]:
        signals = data_signal_metadata()
        groups = list(dict.fromkeys(signal["group"] for signal in signals))
        return {"signals": signals, "groups": groups, "count": len(signals)}

    @app.patch("/api/v1/state")
    async def patch_state(request: StatePatchRequest) -> dict[str, Any]:
        try:
            result = await state_store.apply_updates(request.source, request.signals)
        except (UnknownSignalError, SignalRangeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"applied": result.applied, "ignored": result.ignored, **state_store.as_response()}

    @app.get("/api/v1/state/frame")
    async def get_state_frame() -> dict[str, str]:
        frame = encode_state(
            state_store.snapshot(),
            version=settings.tcp.version,
            steering_endian=settings.tcp.steering_endian,
        )
        return {"hex": frame.hex()}

    @app.post("/api/v1/serial/send")
    async def serial_send(request: SerialSendRequest) -> dict[str, Any]:
        data = _serial_payload(request)
        try:
            written = await app.state.serial_adapter.send(data)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"bytes": written, "hex": data.hex()}

    @app.post("/api/v1/can/send")
    async def can_send(request: CanSendRequest) -> dict[str, Any]:
        try:
            data = bytes.fromhex(request.data_hex)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="data_hex must be valid hexadecimal bytes.") from exc
        try:
            await app.state.can_adapter.send(request.arbitration_id, data, request.is_extended_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"sent": True, "arbitration_id": request.arbitration_id, "data_hex": data.hex()}

    @app.websocket("/ws/v1/state")
    async def state_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"type": "state.snapshot", "payload": state_store.as_response()})
        try:
            async with event_bus.subscribe() as queue:
                while True:
                    event = await queue.get()
                    await websocket.send_json(event)
        except WebSocketDisconnect:
            return

    return app


def _serial_payload(request: SerialSendRequest) -> bytes:
    if request.hex is not None:
        try:
            return bytes.fromhex(request.hex)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="hex must be valid hexadecimal bytes.") from exc
    if request.text is not None:
        return request.text.encode("utf-8")
    raise HTTPException(status_code=400, detail="Provide either hex or text.")


app = create_app()
