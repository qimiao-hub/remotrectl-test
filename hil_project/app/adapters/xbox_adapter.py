from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from app.adapters.base import Adapter
from app.config import XboxConfig, XboxMapping
from app.events import EventBus
from app.state import StateStore

LOGGER = logging.getLogger(__name__)


class XboxAdapter(Adapter):
    def __init__(self, config: XboxConfig, event_bus: EventBus, state_store: StateStore) -> None:
        self._config = config
        self._event_bus = event_bus
        self._state_store = state_store
        self._pygame: Any = None
        self._joystick: Any = None
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if not self._config.enabled:
            return
        try:
            import pygame
        except ImportError as exc:
            raise RuntimeError("pygame is required for Xbox adapter. Install requirements.txt.") from exc

        self._pygame = pygame
        pygame.init()
        pygame.joystick.init()
        if pygame.joystick.get_count() < 1:
            raise RuntimeError("Xbox adapter enabled, but no joystick/gamepad was found.")
        self._joystick = pygame.joystick.Joystick(0)
        self._joystick.init()
        self._stop_event.clear()
        self._task = asyncio.create_task(self._poll_loop())
        LOGGER.info("Xbox adapter opened joystick: %s", self._joystick.get_name())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._pygame is not None:
            self._pygame.quit()
            self._pygame = None

    async def _poll_loop(self) -> None:
        assert self._pygame is not None
        while not self._stop_event.is_set():
            for event in self._pygame.event.get():
                await self._handle_event(event)
            await asyncio.sleep(self._config.poll_interval)

    async def _handle_event(self, event: Any) -> None:
        await self._event_bus.publish("xbox.event", {"type": int(event.type), "dict": dict(getattr(event, "dict", {}))})
        updates: dict[str, int] = {}
        for mapping in self._config.mappings:
            value = self._value_from_event(mapping, event)
            if value is not None:
                updates[mapping.signal] = value
        if updates:
            await self._state_store.apply_updates("xbox", updates)

    def _value_from_event(self, mapping: XboxMapping, event: Any) -> int | None:
        assert self._pygame is not None
        if mapping.kind == "axis" and event.type == self._pygame.JOYAXISMOTION and event.axis == mapping.index:
            raw = float(event.value)
            ratio = (raw - mapping.in_min) / (mapping.in_max - mapping.in_min)
            value = mapping.out_min + ratio * (mapping.out_max - mapping.out_min)
            return int(round(value))
        if mapping.kind == "button" and event.type in {self._pygame.JOYBUTTONDOWN, self._pygame.JOYBUTTONUP}:
            if event.button != mapping.index:
                return None
            return mapping.pressed_value if event.type == self._pygame.JOYBUTTONDOWN else mapping.released_value
        if mapping.kind == "hat" and event.type == self._pygame.JOYHATMOTION and event.hat == mapping.index:
            x_value, y_value = event.value
            if mapping.hat_x is not None and int(x_value) == mapping.hat_x:
                return mapping.pressed_value
            if mapping.hat_y is not None and int(y_value) == mapping.hat_y:
                return mapping.pressed_value
            return mapping.released_value
        return None
