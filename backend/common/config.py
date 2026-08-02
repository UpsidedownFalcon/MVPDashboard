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

    expected_input_hz: float = 600.0
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
    insight_interval_s: int = 60
    insight_cooldown_s: int = 600
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

    @property
    def past_windows(self) -> list[timedelta]:
        return parse_duration_list(self.past_windows_raw)

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
