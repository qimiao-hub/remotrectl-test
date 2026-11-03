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


@pytest.mark.asyncio
async def test_gear_priority_rejects_entire_switch():
    """验证低优先级切挡整体被拒绝，其他普通信号仍可写入。"""
    store = StateStore(EventBus())
    await store.apply_updates("api", {"S2-N": 1})
    result = await store.apply_updates("serial", {"S2-D": 1, "Throttle": 20})
    assert result.ignored == {"S2-N": 0, "S2-R": 0, "S2-D": 1}
    assert result.applied == {"Throttle": 20}
    assert store.snapshot()["S2-N"] == 1
    assert store.snapshot()["S2-D"] == 0


@pytest.mark.asyncio
async def test_reject_multiple_active_gears_without_changing_state():
    """验证同时使能多个挡位会报错且不修改原状态。"""
    store = StateStore(EventBus())
    before = store.snapshot()
    with pytest.raises(SignalRangeError):
        await store.apply_updates("api", {"S2-N": 1, "S2-D": 1})
    assert store.snapshot() == before


@pytest.mark.asyncio
async def test_force_replace_keeps_gears_exclusive():
    """验证强制替换挡位时同样清除原挡位使能。"""
    store = StateStore(EventBus())
    await store.force_replace("api", {"S2-R": 1})
    await store.force_replace("api", {"S2-D": 1})
    assert {name: store.snapshot()[name] for name in ("S2-N", "S2-R", "S2-D")} == {
        "S2-N": 0, "S2-R": 0, "S2-D": 1,
    }
