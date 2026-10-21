from __future__ import annotations

import asyncio
import contextlib
import logging

from app.adapters.base import Adapter
from app.config import SerialConfig
from app.events import EventBus

LOGGER = logging.getLogger(__name__)


class SerialAdapter(Adapter):
    def __init__(self, config: SerialConfig, event_bus: EventBus) -> None:
        self._config = config
        self._event_bus = event_bus
        self._serial = None
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if not self._config.enabled:
            return
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial is required for serial adapter. Install requirements.txt.") from exc

        self._serial = serial.Serial(
            port=self._config.port,
            baudrate=self._config.baudrate,
            timeout=self._config.timeout,
        )
        self._stop_event.clear()
        self._task = asyncio.create_task(self._read_loop())
        LOGGER.info("Serial adapter opened %s at %s baud", self._config.port, self._config.baudrate)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._serial is not None:
            await asyncio.to_thread(self._serial.close)
            self._serial = None

    async def send(self, data: bytes) -> int:
        if self._serial is None:
            raise RuntimeError("Serial adapter is not enabled or not started.")
        written = await asyncio.to_thread(self._serial.write, data)
        await self._event_bus.publish("serial.tx", {"hex": data.hex(), "bytes": written})
        return int(written)

    async def _read_loop(self) -> None:
        assert self._serial is not None
        while not self._stop_event.is_set():
            data = await asyncio.to_thread(self._serial.read, 1024)
            if data:
                await self._event_bus.publish("serial.rx", {"hex": data.hex(), "bytes": len(data)})
