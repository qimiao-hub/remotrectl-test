from types import SimpleNamespace

import pytest

from app.adapters.can_adapter import CanAdapter
from app.adapters.serial_adapter import SerialAdapter
from app.adapters.xbox_adapter import XboxAdapter
from app.config import CanConfig, SerialConfig, XboxConfig, XboxMapping
from app.events import EventBus
from app.state import StateStore


class FakeSerial:
    def __init__(self) -> None:
        self.written = b""

    def write(self, data: bytes) -> int:
        self.written += data
        return len(data)


@pytest.mark.asyncio
async def test_serial_send_with_mock_port():
    adapter = SerialAdapter(SerialConfig(enabled=False), EventBus())
    fake = FakeSerial()
    adapter._serial = fake

    written = await adapter.send(bytes.fromhex("0102"))

    assert written == 2
    assert fake.written == b"\x01\x02"


@pytest.mark.asyncio
async def test_can_decode_uses_alias_mapping():
    store = StateStore(EventBus())
    adapter = CanAdapter(
        CanConfig(enabled=False, signal_aliases={"VehicleThrottle": "Throttle"}),
        EventBus(),
        store,
    )
    adapter._database = SimpleNamespace(decode_message=lambda arbitration_id, data: {"VehicleThrottle": 42})
    message = SimpleNamespace(arbitration_id=0x123, data=b"\x00" * 8)

    await adapter._decode_message(message)

    assert store.snapshot()["Throttle"] == 42


@pytest.mark.asyncio
async def test_xbox_axis_mapping_updates_signal():
    store = StateStore(EventBus())
    adapter = XboxAdapter(
        XboxConfig(
            enabled=False,
            mappings=[
                XboxMapping(
                    kind="axis",
                    index=0,
                    signal="Steering",
                    in_min=-1,
                    in_max=1,
                    out_min=-450,
                    out_max=450,
                )
            ],
        ),
        EventBus(),
        store,
    )
    adapter._pygame = SimpleNamespace(JOYAXISMOTION=1, JOYBUTTONDOWN=2, JOYBUTTONUP=3)
    event = SimpleNamespace(type=1, axis=0, value=1.0, dict={"axis": 0, "value": 1.0})

    await adapter._handle_event(event)

    assert store.snapshot()["Steering"] == 450


@pytest.mark.asyncio
async def test_xbox_hat_mapping_updates_signal():
    store = StateStore(EventBus())
    adapter = XboxAdapter(
        XboxConfig(
            enabled=False,
            mappings=[
                XboxMapping(
                    kind="hat",
                    index=0,
                    signal="S2-N",
                    hat_y=1,
                    pressed_value=1,
                    released_value=0,
                )
            ],
        ),
        EventBus(),
        store,
    )
    adapter._pygame = SimpleNamespace(JOYAXISMOTION=1, JOYBUTTONDOWN=2, JOYBUTTONUP=3, JOYHATMOTION=4)
    event = SimpleNamespace(type=4, hat=0, value=(0, 1), dict={"hat": 0, "value": (0, 1)})

    await adapter._handle_event(event)

    assert store.snapshot()["S2-N"] == 1
