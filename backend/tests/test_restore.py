"""Warm-restart snapshot restore, per device (biomech SPEC §7.4).

The snapshot is not just a process-restart convenience: devices are released
from the registry while a session is still live — evicted after SESSION_GAP_S,
or displaced by a new device once MAX_DEVICES is reached, which needs only
OFFLINE_AFTER_S of silence. Restoring only at process start left that case
resetting an athlete's accumulated load to zero with the snapshot still sitting
in Redis, so these tests drive the reconnect path specifically.
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
import orjson
import pytest

from common import packet
from common.config import Settings
from ingest import biomech
from ingest.main import BiomechRestorer
from ingest.state import Registry
from tests.conftest import LIMBS, make_tick

NS = 10
FS = 600.0


class FakeRedis:
    """Just enough of redis.asyncio for the restorer: get() and aclose()."""

    def __init__(self, values: dict[str, bytes] | None = None) -> None:
        self.values = values or {}
        self.gets: list[str] = []
        self.closed = False

    async def get(self, key: str):
        self.gets.append(key)
        return self.values.get(key)

    async def aclose(self) -> None:
        self.closed = True


def _snapshot_with_dose() -> dict:
    """Run enough movement through biomech to accumulate a real dose."""
    state: dict = {}
    for k in range(60 * 20):
        t = (k * NS + np.arange(NS)) / FS
        a = np.stack([np.zeros(NS),
                      9.81 + 4.0 * np.sin(2 * np.pi * 9 * t),
                      np.zeros(NS)], 1)
        w = np.tile([120.0, 0.0, 0.0], (NS, 1))
        frames, times = make_tick(a, w, t0=k * NS / FS)
        biomech.compute(frames, state, times)
    snap = biomech.snapshot(state)
    assert snap["dose"] > 0.0
    return snap


def _route(registry: Registry, device_id: int, t: float, n: int = 4) -> None:
    payloads = [
        packet.encode(device_id, src, sen, i * 1666, [i, -i, 100, -100, 50, -50])
        for src, sen in ((0, 1), (0, 2), (1, 1), (1, 2))
        for i in range(n)
    ]
    registry.route(packet.decode(payloads), recv_time=t)


async def _settle() -> None:
    """Let the restorer's fetch task run to completion."""
    for _ in range(20):
        await asyncio.sleep(0)


async def test_snapshot_restores_on_reconnect_not_just_at_startup() -> None:
    """A displaced device that comes back must get its session back.

    This is the case a startup-only load cannot cover: device 30 is displaced
    by device 31 (MAX_DEVICES=1, 30 silent for more than OFFLINE_AFTER_S), then
    reconnects well inside SESSION_GAP_S with its snapshot still in Redis.
    """
    settings = Settings(_env_file=None)
    snap = _snapshot_with_dose()
    snap["last_tick_t"] = time.time()
    fake = FakeRedis({"biomech:state:30": orjson.dumps(snap)})

    registry = Registry(max_devices=1)
    registry.offline_after_s = settings.offline_after_s
    restorer = BiomechRestorer(settings, LIMBS, client=fake)
    registry.on_new_device = restorer.device_added

    _route(registry, 30, t=1000.0)
    await _settle()
    first = registry.devices[30].user_state["_biomech"]
    assert first.dose == pytest.approx(snap["dose"], rel=1e-3)
    assert restorer.restored == 1

    # 30 goes quiet; 31 takes the only slot and displaces it
    _route(registry, 31, t=1010.0)
    await _settle()
    assert 30 not in registry.devices

    # 30 comes back — inside SESSION_GAP_S, snapshot still in Redis
    snap["last_tick_t"] = time.time()
    fake.values["biomech:state:30"] = orjson.dumps(snap)
    _route(registry, 30, t=1030.0)
    await _settle()

    assert restorer.restored == 2, (
        "a reconnecting device never restored — snapshots are being loaded "
        "once at startup instead of per device appearance"
    )
    second = registry.devices[30].user_state["_biomech"]
    assert second is not first, "expected a fresh DeviceState after displacement"
    assert second.dose == pytest.approx(snap["dose"], rel=1e-3)


async def test_snapshot_older_than_the_session_gap_is_discarded() -> None:
    """The gap rule still applies on reconnect (SPEC §7.4)."""
    settings = Settings(_env_file=None)
    snap = _snapshot_with_dose()
    snap["last_tick_t"] = time.time() - (settings.session_gap_s + 60.0)
    fake = FakeRedis({"biomech:state:30": orjson.dumps(snap)})

    registry = Registry(max_devices=5)
    restorer = BiomechRestorer(settings, LIMBS, client=fake)
    registry.on_new_device = restorer.device_added

    _route(registry, 30, t=1000.0)
    await _settle()
    assert restorer.restored == 0
    assert registry.devices[30].user_state.get("_biomech") is None or \
        registry.devices[30].user_state["_biomech"].dose == 0.0


async def test_missing_snapshot_and_redis_failure_are_survivable() -> None:
    """No snapshot, or no Redis, must just mean a fresh session."""
    settings = Settings(_env_file=None)
    registry = Registry(max_devices=5)
    restorer = BiomechRestorer(settings, LIMBS, client=FakeRedis({}))
    registry.on_new_device = restorer.device_added
    _route(registry, 30, t=1000.0)
    await _settle()
    assert restorer.restored == 0

    class Broken(FakeRedis):
        async def get(self, key: str):
            raise ConnectionError("redis down")

    registry2 = Registry(max_devices=5)
    restorer2 = BiomechRestorer(settings, LIMBS, client=Broken())
    registry2.on_new_device = restorer2.device_added
    _route(registry2, 31, t=1000.0)
    await _settle()
    assert restorer2.restored == 0
    assert 31 in registry2.devices, "a Redis failure must not lose the device"
