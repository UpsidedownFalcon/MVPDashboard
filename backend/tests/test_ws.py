"""S1-T12 tests: WS fan-out throughput (>=55 msg/s/device over 5s) + tick schema.

Needs a reachable Redis on 127.0.0.1:6379 — start it with:
    docker compose --profile debug up -d redis redis-debug
Skips (with that message) if Redis is not reachable.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
import redis as sync_redis
from starlette.testclient import TestClient

TEST_REDIS_URL = "redis://127.0.0.1:6379/0"
DEVICES = ("30", "31")
DURATION_S = 5.0
RATE_HZ = 60.0


def _redis_or_skip() -> sync_redis.Redis:
    r = sync_redis.from_url(TEST_REDIS_URL, socket_connect_timeout=1)
    try:
        r.ping()
    except Exception:
        pytest.skip("Redis not reachable on 127.0.0.1:6379 — "
                    "run: docker compose --profile debug up -d redis redis-debug")
    return r


class _Publisher(threading.Thread):
    """Publishes schema-correct ticks at 60Hz per device until stopped."""

    def __init__(self, r: sync_redis.Redis) -> None:
        super().__init__(daemon=True)
        self._r = r
        self.stop_flag = threading.Event()

    def run(self) -> None:
        period = 1.0 / RATE_HZ
        k = 0
        start = time.perf_counter()
        while not self.stop_flag.is_set():
            k += 1
            now = time.time()
            iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + \
                f".{int(now * 1000) % 1000:03d}Z"
            for dev in DEVICES:
                self._r.publish("ticks", json.dumps({
                    "type": "tick", "t": iso, "dev": dev,
                    "m": [0.1, 0.2, 0.3, 0.4, 0.5], "c": 0.42, "q": 0.98,
                }))
            sleep_for = start + k * period - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)


@pytest.fixture()
def app_client(monkeypatch: pytest.MonkeyPatch):
    _redis_or_skip()
    monkeypatch.setenv("REDIS_URL", TEST_REDIS_URL)
    from common.config import get_settings
    get_settings.cache_clear()
    from api.main import create_app
    with TestClient(create_app()) as client:
        yield client
    get_settings.cache_clear()


def test_ws_throughput_and_schema(app_client: TestClient) -> None:
    r = _redis_or_skip()
    publisher = _Publisher(r)

    counts = {dev: 0 for dev in DEVICES}
    first: dict | None = None
    with app_client.websocket_connect("/ws/live") as ws:
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

    # schema: exactly the BACKEND_SCHEMA §2 fields
    assert first is not None
    assert set(first) == {"type", "t", "dev", "m", "c", "q"}
    assert isinstance(first["m"], list) and len(first["m"]) == 5
    assert isinstance(first["c"], (int, float))
    assert 0.0 <= first["q"] <= 1.0
    assert first["t"].endswith("Z")

    for dev, n in counts.items():
        assert n / DURATION_S >= 55.0, f"device {dev}: only {n / DURATION_S:.1f} msg/s"


def test_ws_device_filter(app_client: TestClient) -> None:
    r = _redis_or_skip()
    publisher = _Publisher(r)
    seen: set[str] = set()
    with app_client.websocket_connect("/ws/live?devices=31") as ws:
        publisher.start()
        deadline = time.perf_counter() + 1.5
        while time.perf_counter() < deadline:
            msg = json.loads(ws.receive_text())
            if msg["type"] == "tick":
                seen.add(msg["dev"])
        publisher.stop_flag.set()
    publisher.join(timeout=2)
    assert seen == {"31"}
