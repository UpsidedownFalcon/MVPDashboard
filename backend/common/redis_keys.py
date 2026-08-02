"""Redis key/channel names — must match BACKEND_SCHEMA.md §4 exactly."""

from __future__ import annotations

TICKS_CHANNEL = "ticks"
INGEST_STATS = "ingest:stats"


def last_seen_dev(device_id: int | str) -> str:
    return f"last_seen:dev:{device_id}"


def last_seen_sensor(device_id: int | str, source_id: int, sensor_id: int) -> str:
    return f"last_seen:sensor:{device_id}:{source_id}:{sensor_id}"
