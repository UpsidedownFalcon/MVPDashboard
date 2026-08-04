"""Single typed config object for the whole backend, loaded from the root `.env`.

Every key from TRD §7 lives here; nothing else reads env vars directly.
"""

from __future__ import annotations

import json
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.durations import parse_duration, parse_duration_list

# Repo root when running from a checkout (backend/common/config.py -> repo root).
# In containers the file is absent and config comes from process env vars.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

_DEFAULT_LIMB_MAP = {
    (0, 1): "left_shin",
    (0, 2): "left_thigh",
    (1, 1): "right_thigh",
    (1, 2): "right_shin",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    domain: str = "dash.example.com"
    udp_port: int = 5005
    api_port: int = 8000

    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_db: str = "mvpdash"
    postgres_user: str = "mvpdash"
    postgres_password: str = "changeme"

    redis_url: str = "redis://redis:6379/0"

    jwt_secret: str = ""
    jwt_expire_hours: int = 24
    seed_users: str = "trainer:changeme"

    expected_input_hz: float = 640.0   # measured device rate (TRD §3); was an
                                       # unmeasured 600 estimate until 2026-08-02
    output_hz: int = 60
    limb_map: dict[tuple[int, int], str] = Field(default_factory=lambda: dict(_DEFAULT_LIMB_MAP))
    jitter_buffer_ms: int = 50
    offline_after_s: float = 2.0
    reset_offset_jump_s: float = 5.0
    # Gap after which biomech drops accumulated load and learned baselines.
    # Deliberately >> offline_after_s so a brief dropout or a rest between sets
    # does not wipe a session's dose (docs/biomech/SPEC.md §7).
    session_gap_s: float = 300.0
    # Hard cap on concurrently tracked devices; extras are dropped and counted.
    max_devices: int = 5

    past_windows_raw: str = Field("5m,30m,2h", validation_alias="PAST_WINDOWS")
    future_horizons_raw: str = Field("10m,30m,1h", validation_alias="FUTURE_HORIZONS")
    predict_interval_s: int = 300
    predict_train_window_raw: str = Field("2h", validation_alias="PREDICT_TRAIN_WINDOW")
    # --- insight cadence ------------------------------------------------------
    # These four numbers together decide how fast an action appears, how often it
    # is re-affirmed, and how long it lingers once the condition clears. They are
    # tuned as a SET (docs/ANALYTICS.md "Insight cadence"); changing one alone
    # reintroduces either latency or flicker.
    #
    # Detection used to run off the shortest PAST_WINDOWS entry (5m). A 5-minute
    # MEAN cannot move quickly by construction: a step change needs ~1.5-2 min of
    # new data before it shifts the average past a threshold, which is why the
    # first insight of a session took 5-9 minutes to appear. INSIGHT_LIVE_WINDOW
    # is a separate, much shorter window read from the raw 60Hz `metrics` table
    # (never the continuous aggregate, which lags 1-2 min), used ONLY by the rule
    # engine as the "now" side of every comparison. The 5m/30m/2h windows still
    # supply the baseline, the spread and the trend.
    #
    # 30s holds ~30 independent m1/m2 peak-holds (both are 1 s maxima) and ~1800
    # composite samples, so the mean is stable enough to threshold while still
    # responding within one window.
    insight_live_window_raw: str = Field("30s", validation_alias="INSIGHT_LIVE_WINDOW")
    insight_interval_s: int = 15
    insight_cooldown_s: int = 120
    # How long an action stays on /api/insights/current after the rule behind it
    # last fired. MUST exceed INSIGHT_COOLDOWN_S: at hold == cooldown a still-true
    # condition drops off the panel for one job tick before it re-fires, which
    # reads as flicker. The excess (30 s = 2 ticks) is the overlap that prevents
    # it, and it also bounds how long a cleared condition keeps advising.
    insight_hold_s: int = 150
    # Hard cap on simultaneous actions. The panel is a decision aid, not a list;
    # beyond ~3 imperatives at once nobody acts on any of them.
    insight_max_actions: int = 3
    # Insight rule thresholds on the composite's 0-100 scale (S2-T05; the task
    # sheet's 0.7/0.85 predate the SPEC's 0-100 rescale — TRD §7). Raised from
    # 70/85 after the composite rescale (biomech SPEC §6.1): a measured hard
    # interval session reads ~77, so 70 would have warned during ordinary hard
    # training. At 85 acute effort alone does not fire and it takes accumulated
    # dose (m3 raising the floor) to reach warning — the intended semantics.
    insight_warn_threshold: float = 85.0
    insight_alert_threshold: float = 92.0
    metrics_retention_raw: str = Field("30d", validation_alias="METRICS_RETENTION")

    @field_validator("limb_map", mode="before")
    @classmethod
    def _parse_limb_map(cls, value: object) -> object:
        if isinstance(value, str):
            value = json.loads(value)
        if isinstance(value, dict):
            parsed: dict[tuple[int, int], str] = {}
            for key, limb in value.items():
                if isinstance(key, str):
                    source_id, sensor_id = (int(part) for part in key.split(","))
                    key = (source_id, sensor_id)
                parsed[key] = limb
            return parsed
        return value

    @field_validator("limb_map", mode="after")
    @classmethod
    def _limb_names_must_be_unique(cls, value: dict[tuple[int, int], str]) -> dict[tuple[int, int], str]:
        """Two sensors mapping to the same limb name silently wipes biomech.

        The ticker builds `frames` keyed by limb name, so a duplicate makes one
        sensor overwrite the other's frame; ingest's `limbs` tuple (built from
        the same values) is then shorter than `frames.keys()`, which makes
        biomech rebuild its session state every tick — zeroing dose, R_base and
        the L/R accumulators 60 times a second. It looks like a flat athlete,
        not like a config error, so reject it at load.
        """
        seen: dict[str, tuple[int, int]] = {}
        for key, limb in value.items():
            first = seen.get(limb)
            if first is not None:
                raise ValueError(
                    f"LIMB_MAP limb names must be unique: {limb!r} is mapped by "
                    f"both (source={first[0]}, sensor={first[1]}) and "
                    f"(source={key[0]}, sensor={key[1]})"
                )
            seen[limb] = key
        return value

    @property
    def past_windows(self) -> list[timedelta]:
        return parse_duration_list(self.past_windows_raw)

    @property
    def insight_live_window(self) -> timedelta:
        return parse_duration(self.insight_live_window_raw)

    @property
    def future_horizons(self) -> list[timedelta]:
        return parse_duration_list(self.future_horizons_raw)

    @property
    def predict_train_window(self) -> timedelta:
        return parse_duration(self.predict_train_window_raw)

    @property
    def metrics_retention(self) -> timedelta:
        return parse_duration(self.metrics_retention_raw)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
