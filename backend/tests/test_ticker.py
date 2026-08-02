"""S1-T09 tests: cadence exactness, framing, quality, hold, suspend/resume."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from common import packet
from common.config import Settings
from ingest.state import Registry
from ingest.ticker import DeviceTicker, TickInput


class FakeClock:
    """Deterministic clock: sleep() advances time instantly (yields once)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    async def sleep(self, d: float) -> None:
        self.t += max(d, 0.0)
        await asyncio.sleep(0)


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None)


def _route_samples(registry: Registry, recv_time: float, device_id=30,
                   ts0=0, n=10, period_us=1666):
    """Push n samples per sensor (all 4) into the registry at recv_time."""
    payloads = []
    for src, sen in ((0, 1), (0, 2), (1, 1), (1, 2)):
        for i in range(n):
            payloads.append(packet.encode(device_id, src, sen, ts0 + i * period_us,
                                          [i, -i, 100, -100, 50, -50]))
    registry.route(packet.decode(payloads), recv_time=recv_time)


async def _run_ticks(ticker: DeviceTicker, clock: FakeClock, ticks: list,
                     n_ticks: int, max_iter: int = 5000) -> None:
    task = asyncio.create_task(ticker.run())
    for _ in range(max_iter):
        if len(ticks) >= n_ticks:
            break
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_cadence_exact_and_frames_delivered(settings: Settings) -> None:
    clock = FakeClock(start=1000.0)
    registry = Registry()
    ticks: list[TickInput] = []
    device = registry.device(30)
    ticker = DeviceTicker(device, settings, ticks.append,
                          now_fn=clock.now, sleep_fn=clock.sleep)

    # feed fresh samples right before starting; keep last_seen current via routing
    async def feeder() -> None:
        ts0 = 0
        while True:
            _route_samples(registry, recv_time=clock.t, ts0=ts0, n=10)
            ts0 += 10 * 1666
            await asyncio.sleep(0)

    feed_task = asyncio.create_task(feeder())
    await _run_ticks(ticker, clock, ticks, n_ticks=120)
    feed_task.cancel()
    await asyncio.gather(feed_task, return_exceptions=True)

    assert len(ticks) >= 120
    # absolute-scheduled cadence: t_server exactly start + k/60
    period = 1.0 / settings.output_hz
    for k, tick in enumerate(ticks, start=1):
        assert tick.t_server == pytest.approx(1000.0 + k * period, abs=1e-9)
    # after the jitter window (50ms = 3 ticks), frames flow with all 4 limbs
    late = ticks[10:]
    assert any(not t.held for t in late)
    flowing = [t for t in late if not t.held]
    for t in flowing[:5]:
        assert set(t.frames) == {"left_shin", "left_thigh", "right_thigh", "right_shin"}
        for arr in t.frames.values():
            assert arr.dtype == np.float32
            assert arr.shape[1] == 6


async def test_quality_reflects_sample_share(settings: Settings) -> None:
    clock = FakeClock(start=1000.0)
    registry = Registry()
    ticks: list[TickInput] = []
    device = registry.device(30)
    ticker = DeviceTicker(device, settings, ticks.append,
                          now_fn=clock.now, sleep_fn=clock.sleep)

    async def feeder() -> None:
        ts0 = 0
        while True:
            _route_samples(registry, recv_time=clock.t, ts0=ts0, n=10)
            ts0 += 10 * 1666
            await asyncio.sleep(0)

    feed_task = asyncio.create_task(feeder())
    await _run_ticks(ticker, clock, ticks, n_ticks=120)
    feed_task.cancel()
    await asyncio.gather(feed_task, return_exceptions=True)

    # steady state: 10 samples/sensor per 1666us*10 = 60Hz worth => quality ~1
    steady = [t.quality for t in ticks[30:] if not t.held]
    assert steady, "expected flowing ticks"
    assert np.mean(steady) == pytest.approx(1.0, abs=0.15)


async def test_hold_when_no_data_then_suspend(settings: Settings) -> None:
    clock = FakeClock(start=1000.0)
    registry = Registry()
    ticks: list[TickInput] = []
    device = registry.device(30)
    ticker = DeviceTicker(device, settings, ticks.append,
                          now_fn=clock.now, sleep_fn=clock.sleep)

    # one burst of samples, then silence; run well past the offline threshold
    _route_samples(registry, recv_time=clock.t, ts0=0, n=10)
    await _run_ticks(ticker, clock, ticks, n_ticks=10_000, max_iter=400)

    # some early ticks emitted (held or not); after OFFLINE_AFTER_S (2s = 120
    # ticks) the ticker suspends, so far fewer than max_iter ticks arrive
    assert 0 < len(ticks) <= 125
    assert ticker.suspended
    held_ticks = [t for t in ticks if t.held]
    assert held_ticks, "silence within the offline window must emit held ticks"
    assert all(t.quality == 0.0 for t in held_ticks)


def test_pending_and_jitter_drops_compose(settings: Settings) -> None:
    """Overflow in BOTH bounded buffers must show up in buf_drop (TRD §4).

    The ticker mirrors the jitter buffer's running total onto the sensor stats.
    It used to assign that onto the same field the pending queue increments, so
    the pending-overflow count was erased on the next tick — under exactly the
    load that produces it. The two counters are independent and both real, so
    buf_drop reports their sum.
    """
    from ingest.state import PENDING_MAXCHUNKS, Registry

    clock = FakeClock(start=1000.0)
    registry = Registry()
    device = registry.device(30)
    ticker = DeviceTicker(device, settings, lambda tick: None,
                          now_fn=clock.now, sleep_fn=clock.sleep)

    # One sample per route() call, on one sensor, well past the pending cap.
    for i in range(PENDING_MAXCHUNKS + 200):
        payload = packet.encode(30, 0, 1, i * 1666, [i % 100, 0, 100, -100, 50, -50])
        registry.route(packet.decode([payload]), recv_time=1000.0 + i * 1e-3)

    stats = device.sensors[(0, 1)].stats
    pending_only = stats.pending_drop
    assert pending_only > 0, "fixture did not overflow the pending queue"
    assert stats.jitter_drop == 0

    ticker._process_pending()
    assert stats.jitter_drop > 0, "fixture did not overflow the jitter buffer"
    assert stats.pending_drop == pending_only, "pending overflow was clobbered"
    assert stats.buf_drop == stats.pending_drop + stats.jitter_drop


async def test_resume_after_offline_resets_and_ticks_again(settings: Settings) -> None:
    clock = FakeClock(start=1000.0)
    registry = Registry()
    ticks: list[TickInput] = []
    device = registry.device(30)
    ticker = DeviceTicker(device, settings, ticks.append,
                          now_fn=clock.now, sleep_fn=clock.sleep)

    _route_samples(registry, recv_time=clock.t, ts0=0, n=10)
    task = asyncio.create_task(ticker.run())
    for _ in range(400):
        await asyncio.sleep(0)
    assert ticker.suspended
    n_before = len(ticks)

    # traffic returns (fresh recv_time + fresh device timestamps)
    async def feeder() -> None:
        ts0 = 0
        while True:
            _route_samples(registry, recv_time=clock.t, ts0=ts0, n=10)
            ts0 += 10 * 1666
            await asyncio.sleep(0)

    feed_task = asyncio.create_task(feeder())
    for _ in range(300):
        if len(ticks) > n_before + 30 and not ticker.suspended:
            break
        await asyncio.sleep(0)
    task.cancel()
    feed_task.cancel()
    await asyncio.gather(task, feed_task, return_exceptions=True)

    assert not ticker.suspended
    assert len(ticks) > n_before + 30
