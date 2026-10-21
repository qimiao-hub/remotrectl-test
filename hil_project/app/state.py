from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.events import EventBus
from app.signals import DATA_DEFINITION_BY_NAME, DATA_DEFINITIONS


class UnknownSignalError(ValueError):
    pass


class SignalRangeError(ValueError):
    pass


@dataclass(frozen=True)
class SignalUpdateResult:
    applied: dict[str, int]
    ignored: dict[str, int]


class StateStore:
    def __init__(self, event_bus: EventBus, source_priority: list[str] | None = None) -> None:
        self._event_bus = event_bus
        self._lock = asyncio.Lock()
        self._values = {definition.name: definition.initial for definition in DATA_DEFINITIONS}
        self._owners = {definition.name: "default" for definition in DATA_DEFINITIONS}
        self._priority = source_priority or ["api", "xbox", "can", "serial"]
        self._priority_rank = {source: index for index, source in enumerate(self._priority)}

    def snapshot(self) -> dict[str, int]:
        return dict(self._values)

    def owners(self) -> dict[str, str]:
        return dict(self._owners)

    async def apply_updates(self, source: str, signals: dict[str, int | float | bool]) -> SignalUpdateResult:
        updates = self._validate_updates(signals)
        applied: dict[str, int] = {}
        ignored: dict[str, int] = {}

        async with self._lock:
            for name, value in updates.items():
                if self._can_update(name, source):
                    self._values[name] = value
                    self._owners[name] = source
                    applied[name] = value
                else:
                    ignored[name] = value

        if applied:
            await self._event_bus.publish(
                "state.updated",
                {"source": source, "signals": applied, "ignored": ignored, "state": self.snapshot()},
            )
        return SignalUpdateResult(applied=applied, ignored=ignored)

    async def force_replace(self, source: str, signals: dict[str, int]) -> None:
        updates = self._validate_updates(signals)
        async with self._lock:
            for name, value in updates.items():
                self._values[name] = value
                self._owners[name] = source
        await self._event_bus.publish("state.replaced", {"source": source, "signals": updates, "state": self.snapshot()})

    def _validate_updates(self, signals: dict[str, int | float | bool]) -> dict[str, int]:
        updates: dict[str, int] = {}
        for name, raw_value in signals.items():
            definition = DATA_DEFINITION_BY_NAME.get(name)
            if definition is None:
                raise UnknownSignalError(f"Unknown signal: {name}")
            value = int(raw_value)
            if value < definition.minimum or value > definition.maximum:
                raise SignalRangeError(
                    f"{name}={value} out of range [{definition.minimum}, {definition.maximum}]"
                )
            updates[name] = value
        return updates

    def _can_update(self, signal_name: str, source: str) -> bool:
        current_owner = self._owners.get(signal_name, "default")
        incoming_rank = self._rank(source)
        current_rank = self._rank(current_owner)
        return source == current_owner or incoming_rank <= current_rank

    def _rank(self, source: str) -> int:
        if source == "default":
            return len(self._priority_rank) + 1
        return self._priority_rank.get(source, len(self._priority_rank))

    def as_response(self) -> dict[str, Any]:
        return {
            "signals": self.snapshot(),
            "owners": self.owners(),
            "source_priority": list(self._priority),
        }
