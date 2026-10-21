import pytest

from app.events import EventBus
from app.state import SignalRangeError, StateStore, UnknownSignalError


@pytest.mark.asyncio
async def test_state_applies_higher_priority_and_ignores_lower_priority():
    store = StateStore(EventBus(), ["api", "xbox", "can", "serial"])

    await store.apply_updates("api", {"Throttle": 50})
    result = await store.apply_updates("serial", {"Throttle": 10})

    assert result.ignored == {"Throttle": 10}
    assert store.snapshot()["Throttle"] == 50


@pytest.mark.asyncio
async def test_state_allows_higher_priority_to_override_lower_priority():
    store = StateStore(EventBus(), ["api", "xbox", "can", "serial"])

    await store.apply_updates("serial", {"Brake": 10})
    result = await store.apply_updates("api", {"Brake": 90})

    assert result.applied == {"Brake": 90}
    assert store.snapshot()["Brake"] == 90


@pytest.mark.asyncio
async def test_state_rejects_unknown_signal():
    store = StateStore(EventBus())

    with pytest.raises(UnknownSignalError):
        await store.apply_updates("api", {"NoSuchSignal": 1})


@pytest.mark.asyncio
async def test_state_rejects_out_of_range_value():
    store = StateStore(EventBus())

    with pytest.raises(SignalRangeError):
        await store.apply_updates("api", {"Throttle": 101})
