"""Redis key/channel names — must match BACKEND_SCHEMA.md §4 exactly."""

from __future__ import annotations

TICKS_CHANNEL = "ticks"
INGEST_STATS = "ingest:stats"


def last_seen_dev(device_id: int | str) -> str:
    return f"last_seen:dev:{device_id}"


def last_seen_sensor(device_id: int | str, source_id: int, sensor_id: int) -> str:
    return f"last_seen:sensor:{device_id}:{source_id}:{sensor_id}"


def biomech_diag(device_id: int | str) -> str:
    """Pre-normalisation biomech diagnostics, for tuning the provisional
    reference bounds against real trial data (biomech SPEC §9.2)."""
    return f"biomech:diag:{device_id}"


def biomech_state(device_id: int | str) -> str:
    """Warm-restart snapshot: ingest -> ingest only (biomech SPEC §7.4)."""
    return f"biomech:state:{device_id}"


def biomech_cal(device_id: int | str) -> str:
    """Last-known-good calibration, carried BETWEEN sessions (SPEC §3.8).

    Deliberately not the §7.4 snapshot: that one is discarded after
    SESSION_GAP_S, which is exactly the case this key exists for — the athlete
    coming back the next day. TTL is days, and the payload is keyed on limb
    name, never on slot index.
    """
    return f"biomech:cal:{device_id}"
