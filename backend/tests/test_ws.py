"""S1-T12 tests: WS fan-out throughput (>=55 msg/s/device over 5s) + tick schema.

Needs a reachable Redis on 127.0.0.1:6379 — start it with:
    docker compose --profile debug up -d redis redis-debug
Skips (with that message) if Redis is not reachable.
"""

from __future__ import annotations

import json
import threading
import time

import numpy as np
import pytest
import redis as sync_redis
from starlette.testclient import TestClient

from ingest import biomech
from ingest.publish import tick_to_json
from ingest.ticker import TickInput
from tests.conftest import LIMBS, make_tick

TEST_REDIS_URL = "redis://127.0.0.1:6379/0"
DEVICES = ("30", "31")
DURATION_S = 5.0
RATE_HZ = 60.0
NS = 10
FS = 600.0

# SPEC §10 item 3 — the only strings that may appear in a tick's "f" field.
FLAG_VOCABULARY = {
    "warming_up", "partial", "no_shank", "saturated", "degraded_sensors",
    "uncalibrated", "cal_failed", "carried_over", "unvalidated",
}


def _redis_or_skip() -> sync_redis.Redis:
    r = sync_redis.from_url(TEST_REDIS_URL, socket_connect_timeout=1)
    try:
        r.ping()
    except Exception:
        pytest.skip("Redis not reachable on 127.0.0.1:6379 — "
                    "run: docker compose --profile debug up -d redis redis-debug")
    return r


def _real_payloads() -> dict[str, bytes]:
    """One serialised tick per device, built by the REAL producer.

    Hand-writing the JSON here would make this test pass no matter what
    ingest actually publishes — which is how the `f` field went undocumented.
    So the frames go through biomech.compute() and the bytes come from
    ingest.publish.tick_to_json(), the same function the ticker calls. The
    signal is chosen to move m1..m3 while leaving m4/m5 inside their warm-up,
    so the payload exercises the nullable-entry path too.
    """
    payloads: dict[str, bytes] = {}
    for dev in DEVICES:
        state: dict = {}
        metrics = None
        for k in range(120):
            t = (k * NS + np.arange(NS)) / FS
            a = np.stack([np.zeros(NS),
                          9.81 + 4.0 * np.sin(2 * np.pi * 9 * t),
                          np.zeros(NS)], 1)
            w = np.tile([120.0, 0.0, 0.0], (NS, 1))
            frames, times = make_tick(a, w, limbs=LIMBS, t0=k * NS / FS)
            metrics = biomech.compute(frames, state, times)
        tick = TickInput(
            device_id=int(dev),
            t_server=time.time(),
            frames={},
            times={},
            quality=0.98,
            held=False,
        )
        payloads[dev] = tick_to_json(tick, metrics)
    return payloads


class _Publisher(threading.Thread):
    """Republishes real ingest payloads at 60Hz per device until stopped."""

    def __init__(self, r: sync_redis.Redis) -> None:
        super().__init__(daemon=True)
        self._r = r
        self._payloads = _real_payloads()
        self.stop_flag = threading.Event()

    def run(self) -> None:
        period = 1.0 / RATE_HZ
        k = 0
        start = time.perf_counter()
        while not self.stop_flag.is_set():
            k += 1
            for payload in self._payloads.values():
                self._r.publish("ticks", payload)
            sleep_for = start + k * period - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)


@pytest.fixture()
def app_client(monkeypatch: pytest.MonkeyPatch):
    _redis_or_skip()
    monkeypatch.setenv("REDIS_URL", TEST_REDIS_URL)
    # S3-T05: create_app refuses to start without a secret, and /ws/live
    # closes 4401 without a valid session cookie (see _cookie_headers)
    monkeypatch.setenv("JWT_SECRET", "test-ws-secret-0123456789abcdef")
    from common.config import get_settings
    get_settings.cache_clear()
    from api.main import create_app
    with TestClient(create_app()) as client:
        yield client
    get_settings.cache_clear()


def _cookie_headers() -> dict[str, str]:
    """Valid session cookie for the WS handshake (auth is S3-T05)."""
    from api.auth import mint_token
    from common.config import get_settings
    token, _ = mint_token(get_settings(), "tester", "trainer")
    return {"cookie": f"session={token}"}


def test_ws_throughput_and_schema(app_client: TestClient) -> None:
    r = _redis_or_skip()
    publisher = _Publisher(r)

    counts = {dev: 0 for dev in DEVICES}
    first: dict | None = None
    with app_client.websocket_connect("/ws/live", headers=_cookie_headers()) as ws:
        publisher.start()
        deadline = time.perf_counter() + DURATION_S
        while time.perf_counter() < deadline:
            msg = json.loads(ws.receive_text())
            if msg["type"] != "tick":
                continue
            if first is None:
                first = msg
            if msg["dev"] in counts:
                counts[msg["dev"]] += 1
        publisher.stop_flag.set()
    publisher.join(timeout=2)

    # schema: exactly the BACKEND_SCHEMA §2 fields, as the real producer emits them
    assert first is not None
    assert set(first) == {"type", "t", "dev", "m", "c", "q", "f", "cal"}
    assert isinstance(first["m"], list) and len(first["m"]) == 5
    # 0..100, not the pre-SPEC stub's 0..1; entries may be null (§2)
    for v in first["m"]:
        assert v is None or 0.0 <= v <= 100.0
    assert isinstance(first["c"], (int, float)) and 0.0 <= first["c"] <= 100.0
    assert 0.0 <= first["q"] <= 1.0
    assert first["t"].endswith("Z")
    assert first["f"] is None or set(first["f"]) <= FLAG_VOCABULARY
    # `cal` is the stand-still countdown: seconds remaining, or null when
    # nothing is calibrating. Never negative — the UI renders it verbatim.
    assert first["cal"] is None or first["cal"] >= 0.0

    for dev, n in counts.items():
        assert n / DURATION_S >= 55.0, f"device {dev}: only {n / DURATION_S:.1f} msg/s"


def test_ws_device_filter(app_client: TestClient) -> None:
    r = _redis_or_skip()
    publisher = _Publisher(r)
    seen: set[str] = set()
    with app_client.websocket_connect("/ws/live?devices=31", headers=_cookie_headers()) as ws:
        publisher.start()
        deadline = time.perf_counter() + 1.5
        while time.perf_counter() < deadline:
            msg = json.loads(ws.receive_text())
            if msg["type"] == "tick":
                seen.add(msg["dev"])
        publisher.stop_flag.set()
    publisher.join(timeout=2)
    assert seen == {"31"}
