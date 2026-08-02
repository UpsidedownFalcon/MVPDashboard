"""Batched DB writer + device auto-registration (S2-T02).

Second consumer of the Hub's single Redis subscription: `on_tick(dict)` is a
synchronous append (never blocks the fan-out); a 1s loop flushes the buffer to
the `metrics` hypertable with one asyncpg COPY. Backpressure golden rule: the
buffer is bounded (~60s of ticks); beyond that we drop oldest and count. A DB
outage costs history rows, never live latency — WS streaming is unaffected.

Ticks are the BACKEND_SCHEMA §2 JSON; unknown keys (e.g. the biomech flags
field `"f"`) are deliberately ignored.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime

import asyncpg

from common.config import Settings

log = logging.getLogger("api.writer")

METRICS_COLUMNS = (
    "time", "device_id", "m1", "m2", "m3", "m4", "m5", "composite", "quality",
)


class Writer:
    def __init__(self, settings: Settings, pool: asyncpg.Pool) -> None:
        self._pool = pool
        # ~60s of ticks at full fleet: 60 × OUTPUT_HZ × MAX_DEVICES
        self._cap = 60 * settings.output_hz * settings.max_devices
        self._buf: deque[tuple] = deque()
        self._known_devices: set[str] = set()
        self._devices_loaded = False
        self._db_ok: bool | None = None   # None = unknown; log only on change
        self._task: asyncio.Task | None = None
        self.rows_written = 0
        self.db_dropped = 0
        self.bad_ticks = 0

    # --- counters for /api/health -------------------------------------------

    @property
    def db_buffer(self) -> int:
        return len(self._buf)

    @property
    def db_ok(self) -> bool | None:
        return self._db_ok

    # --- hot path (called from the hub's subscribe loop) ---------------------

    def on_tick(self, tick: dict) -> None:
        """Parse a §2 tick dict into a metrics record and buffer it."""
        try:
            m = tick["m"]
            record = (
                datetime.fromisoformat(tick["t"]),
                str(tick["dev"]),
                m[0], m[1], m[2], m[3], m[4],
                float(tick["c"]),
                float(tick["q"]),
            )
        except (KeyError, IndexError, TypeError, ValueError):
            self.bad_ticks += 1
            return
        self._buf.append(record)
        while len(self._buf) > self._cap:
            self._buf.popleft()
            self.db_dropped += 1

    # --- flush loop -----------------------------------------------------------

    async def start(self) -> None:
        self._task = asyncio.create_task(self._flush_loop(), name="db-writer")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self.flush()   # best-effort final drain

    async def _flush_loop(self, interval: float = 1.0) -> None:
        while True:
            await asyncio.sleep(interval)
            await self.flush()

    async def flush(self) -> None:
        """One COPY batch; on failure keep the rows and retry next second."""
        if not self._buf:
            return
        batch = list(self._buf)
        try:
            async with self._pool.acquire() as conn:
                if not self._devices_loaded:
                    rows = await conn.fetch("SELECT device_id FROM devices")
                    self._known_devices = {r["device_id"] for r in rows}
                    self._devices_loaded = True
                await self._register_devices(conn, batch)
                await conn.copy_records_to_table(
                    "metrics", records=batch, columns=METRICS_COLUMNS
                )
        except (OSError, asyncio.TimeoutError, asyncpg.PostgresError) as exc:
            if self._db_ok is not False:
                log.warning("db flush failed (%s) — buffering, will retry", exc)
            self._db_ok = False
            return
        # drop exactly what we copied; on_tick may have appended meanwhile
        for _ in batch:
            self._buf.popleft()
        self.rows_written += len(batch)
        if self._db_ok is not True:
            log.info("db writer healthy (%d rows buffered)", len(self._buf))
        self._db_ok = True

    async def _register_devices(self, conn: asyncpg.Connection, batch: list[tuple]) -> None:
        """Auto-register unseen device_ids (display_name defaults to the id)."""
        unseen = {r[1] for r in batch} - self._known_devices
        for device_id in sorted(unseen):
            await conn.execute(
                "INSERT INTO devices (device_id, display_name) VALUES ($1, $1) "
                "ON CONFLICT DO NOTHING",
                device_id,
            )
            self._known_devices.add(device_id)
