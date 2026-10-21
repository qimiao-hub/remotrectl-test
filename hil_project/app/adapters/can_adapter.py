from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from app.adapters.base import Adapter
from app.config import CanConfig
from app.events import EventBus
from app.state import StateStore

LOGGER = logging.getLogger(__name__)


class CanAdapter(Adapter):
    def __init__(self, config: CanConfig, event_bus: EventBus, state_store: StateStore) -> None:
        self._config = config
        self._event_bus = event_bus
        self._state_store = state_store
        self._bus: Any = None
        self._database: Any = None
        self._can_module: Any = None
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if not self._config.enabled:
            return
        try:
            import can
            import cantools
        except ImportError as exc:
            raise RuntimeError("python-can and cantools are required for CAN adapter. Install requirements.txt.") from exc

        self._can_module = can
        self._database = cantools.database.load_file(self._config.dbc_path)
        self._bus = can.interface.Bus(
            interface=self._config.interface,
            channel=self._config.channel,
            bitrate=self._config.bitrate,
        )
        self._stop_event.clear()
        self._task = asyncio.create_task(self._read_loop())
        LOGGER.info("CAN adapter opened interface=%s channel=%s", self._config.interface, self._config.channel)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._bus is not None:
            await asyncio.to_thread(self._bus.shutdown)
            self._bus = None

    async def send(self, arbitration_id: int, data: bytes, is_extended_id: bool = False) -> None:
        if self._bus is None or self._can_module is None:
            raise RuntimeError("CAN adapter is not enabled or not started.")
        message = self._can_module.Message(
            arbitration_id=arbitration_id,
            data=data,
            is_extended_id=is_extended_id,
        )
        await asyncio.to_thread(self._bus.send, message)
        await self._event_bus.publish(
            "can.tx",
            {"arbitration_id": arbitration_id, "data_hex": data.hex(), "is_extended_id": is_extended_id},
        )

    async def _read_loop(self) -> None:
        assert self._bus is not None
        while not self._stop_event.is_set():
            message = await asyncio.to_thread(self._bus.recv, 0.1)
            if message is None:
                continue
            raw_event = {
                "arbitration_id": message.arbitration_id,
                "data_hex": bytes(message.data).hex(),
                "is_extended_id": message.is_extended_id,
            }
            await self._event_bus.publish("can.rx", raw_event)
            await self._decode_message(message)

    async def _decode_message(self, message: Any) -> None:
        if self._database is None:
            return
        try:
            decoded = self._database.decode_message(message.arbitration_id, bytes(message.data))
        except Exception as exc:
            await self._event_bus.publish("can.decode_error", {"arbitration_id": message.arbitration_id, "error": str(exc)})
            return

        mapped: dict[str, int] = {}
        for signal_name, value in decoded.items():
            platform_name = self._config.signal_aliases.get(signal_name, signal_name)
            mapped[platform_name] = int(value)
        if mapped:
            result = await self._state_store.apply_updates("can", mapped)
            await self._event_bus.publish(
                "can.decoded",
                {"arbitration_id": message.arbitration_id, "signals": mapped, "applied": result.applied, "ignored": result.ignored},
            )
