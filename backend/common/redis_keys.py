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


# --- unilateral sleeve configuration (api -> ingest) ---------------------------
# The FIRST keys the api writes and ingest reads. Ingest has no DB access, so
# the dashboard-owned settings of each sleeve (pairing, side, full-scale;
# table sleeve_units) are mirrored here as one JSON document per unit with NO
# TTL, re-mirrored by the api on start and every minute so a Redis restart
# self-heals. Ingest loads them all at start and follows UNIT_CFG_CHANNEL.

UNIT_CFG_CHANNEL = "unit_cfg"   # payload: the unit id whose key changed
UNIT_CFG_PATTERN = "unit:cfg:*"


def unit_cfg(unit_id: str) -> str:
    """common.kinds.UnitConfig JSON for one sleeve unit ("u30-0")."""
    return f"unit:cfg:{unit_id}"
