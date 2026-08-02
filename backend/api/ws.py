"""WS fan-out hub (S1-T12): Redis `ticks` -> all connected /ws/live clients.

Per-client bounded asyncio.Queue(maxsize=120), drop-oldest + ws_dropped counter
(backpressure golden rule: a slow browser never stalls the hub). A 1s status
watcher scans last_seen:dev:* keys and pushes online/offline transitions.
No auth until stage 3.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import orjson
import redis.asyncio as aioredis
from fastapi import WebSocket, WebSocketDisconnect

from common import redis_keys
from common.config import Settings

log = logging.getLogger("api.ws")

CLIENT_QUEUE_MAX = 120


@dataclass
class _Client:
    queue: asyncio.Queue[str]
    devices: set[str] | None = None   # None = all devices


@dataclass
class Hub:
    settings: Settings
    clients: dict[WebSocket, _Client] = field(default_factory=dict)
    ws_dropped: int = 0
    # Extra synchronous consumers of the one Redis subscription (S2-T02: the DB
    # writer). Called with the parsed tick dict; must never block or raise.
    tick_listeners: list = field(default_factory=list)
    _tasks: list[asyncio.Task] = field(default_factory=list)
    _online: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._redis = aioredis.from_url(self.settings.redis_url)

    @property
    def redis(self) -> aioredis.Redis:
        """Shared client for read-side consumers (queries.py, health)."""
        return self._redis

    # --- lifecycle --------------------------------------------------------------

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._subscribe_loop(), name="hub-subscribe"),
            asyncio.create_task(self._status_loop(), name="hub-status"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._redis.aclose()

    async def redis_ok(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:  # noqa: BLE001
            return False

    async def ingest_stats(self) -> dict[str, str]:
        try:
            raw = await self._redis.hgetall(redis_keys.INGEST_STATS)
            return {k.decode(): v.decode() for k, v in raw.items()}
        except Exception:  # noqa: BLE001
            return {}

    async def biomech_diag(self) -> dict[str, dict[str, str]]:
        """Per-device biomech diagnostics (biomech SPEC §9.2), keyed by device."""
        out: dict[str, dict[str, str]] = {}
        try:
            async for key in self._redis.scan_iter(match="biomech:diag:*"):
                device_id = key.decode().rsplit(":", 1)[1]
                raw = await self._redis.hgetall(key)
                out[device_id] = {k.decode(): v.decode() for k, v in raw.items()}
        except Exception:  # noqa: BLE001
            return out
        return out

    # --- fan-out ----------------------------------------------------------------

    def _enqueue(self, client: _Client, payload: str) -> None:
        q = client.queue
        if q.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
                self.ws_dropped += 1
        q.put_nowait(payload)

    def _broadcast(self, payload: str, device: str | None) -> None:
        for client in self.clients.values():
            if device is None or client.devices is None or device in client.devices:
                self._enqueue(client, payload)

    async def _subscribe_loop(self) -> None:
        while True:
            try:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(redis_keys.TICKS_CHANNEL)
                log.info("subscribed to '%s'", redis_keys.TICKS_CHANNEL)
                async for msg in pubsub.listen():
                    if msg["type"] != "message":
                        continue
                    payload: bytes = msg["data"]
                    try:
                        obj = orjson.loads(payload)
                    except orjson.JSONDecodeError:
                        continue
                    device = obj.get("dev")
                    if obj.get("type") == "tick":
                        for listener in self.tick_listeners:
                            try:
                                listener(obj)
                            except Exception:  # noqa: BLE001 — a bad consumer
                                log.exception("tick listener failed")
                    # browsers want text frames; decode once per message
                    self._broadcast(payload.decode(), device)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("ticks subscription lost (%s) — retrying in 1s", exc)
                await asyncio.sleep(1)

    # --- status watcher ---------------------------------------------------------

    async def _status_loop(self) -> None:
        threshold_ms = self.settings.offline_after_s * 1000.0
        while True:
            await asyncio.sleep(1)
            try:
                keys = await self._redis.keys("last_seen:dev:*")
                now_ms = time.time() * 1000.0
                for key in keys:
                    device = key.decode().rsplit(":", 1)[-1]
                    value = await self._redis.get(key)
                    if value is None:
                        continue
                    last_seen_ms = float(value)
                    online = (now_ms - last_seen_ms) <= threshold_ms
                    if self._online.get(device) != online:
                        self._online[device] = online
                        iso = datetime.fromtimestamp(
                            last_seen_ms / 1000.0, tz=timezone.utc
                        ).strftime("%Y-%m-%dT%H:%M:%SZ")
                        payload = orjson.dumps({
                            "type": "status", "dev": device,
                            "online": online, "last_seen": iso,
                        }).decode()
                        self._broadcast(payload, None)  # status goes to everyone
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("status scan failed: %s", exc)

    # --- endpoint ---------------------------------------------------------------

    async def handle_client(self, ws: WebSocket) -> None:
        devices_param = ws.query_params.get("devices")
        devices = (
            {d.strip() for d in devices_param.split(",") if d.strip()}
            if devices_param else None
        )
        await ws.accept()
        client: _Client = _Client(
            queue=asyncio.Queue(maxsize=CLIENT_QUEUE_MAX), devices=devices
        )
        self.clients[ws] = client
        log.info("ws client connected (devices=%s, total=%d)",
                 devices or "all", len(self.clients))

        async def reader() -> None:
            # we never expect client messages; this just surfaces disconnects
            while True:
                await ws.receive_text()

        reader_task = asyncio.create_task(reader())
        try:
            while True:
                payload = await client.queue.get()
                await ws.send_text(payload)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            reader_task.cancel()
            await asyncio.gather(reader_task, return_exceptions=True)
            self.clients.pop(ws, None)
            log.info("ws client gone (total=%d)", len(self.clients))
