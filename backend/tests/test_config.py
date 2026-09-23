"""S1-T02 tests: duration parsing, limb-map parsing, defaults without .env."""

from __future__ import annotations

from datetime import timedelta

import pytest

from common.config import Settings
from common.durations import format_duration, parse_duration, parse_duration_list
from common import redis_keys


# --- durations -----------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5s", timedelta(seconds=5)),
        ("5m", timedelta(minutes=5)),
        ("30m", timedelta(minutes=30)),
        ("2h", timedelta(hours=2)),
        ("30d", timedelta(days=30)),
        ("1w", timedelta(weeks=1)),
        (" 10m ", timedelta(minutes=10)),  # tolerant of surrounding whitespace
    ],
)
def test_parse_duration_valid(text: str, expected: timedelta) -> None:
    assert parse_duration(text) == expected


@pytest.mark.parametrize("junk", ["", "5", "m", "5x", "m5", "-5m", "5.5m", "5 m", "5mm", "h2"])
def test_parse_duration_junk_raises(junk: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(junk)


def test_parse_duration_non_string_raises() -> None:
    with pytest.raises(ValueError):
        parse_duration(5)  # type: ignore[arg-type]


def test_parse_duration_list() -> None:
    assert parse_duration_list("5m,30m,2h") == [
        timedelta(minutes=5),
        timedelta(minutes=30),
        timedelta(hours=2),
    ]
    assert parse_duration_list("10m, 30m , 1h") == [
        timedelta(minutes=10),
        timedelta(minutes=30),
        timedelta(hours=1),
    ]


@pytest.mark.parametrize("junk", ["", ",", "5m,junk", "5m;30m"])
def test_parse_duration_list_junk_raises(junk: str) -> None:
    with pytest.raises(ValueError):
        parse_duration_list(junk)


@pytest.mark.parametrize(
    ("td", "expected"),
    [
        (timedelta(minutes=5), "5m"),
        (timedelta(minutes=90), "90m"),
        (timedelta(hours=2), "2h"),
        (timedelta(days=30), "30d"),
        (timedelta(weeks=1), "1w"),
        (timedelta(seconds=45), "45s"),
    ],
)
def test_format_duration(td: timedelta, expected: str) -> None:
    assert format_duration(td) == expected
    assert parse_duration(format_duration(td)) == td


def test_format_duration_rejects_subsecond_and_nonpositive() -> None:
    with pytest.raises(ValueError):
        format_duration(timedelta(milliseconds=500))
    with pytest.raises(ValueError):
        format_duration(timedelta(0))


# --- settings ------------------------------------------------------------------

@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Strip every TRD §7 env var so tests see pure defaults."""
    for key in (
        "DOMAIN", "UDP_PORT", "API_PORT", "POSTGRES_HOST", "POSTGRES_PORT",
        "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "REDIS_URL",
        "JWT_SECRET", "JWT_EXPIRE_HOURS", "SEED_USERS", "EXPECTED_INPUT_HZ",
        "OUTPUT_HZ", "LIMB_MAP", "JITTER_BUFFER_MS", "OFFLINE_AFTER_S",
        "PAST_WINDOWS", "FUTURE_HORIZONS", "PREDICT_INTERVAL_S",
        "PREDICT_TRAIN_WINDOW", "INSIGHT_INTERVAL_S", "INSIGHT_COOLDOWN_S",
        "METRICS_RETENTION", "UNILATERAL_SENSOR_MAP", "UNILATERAL_ACCEL_FS_G",
        "UNILATERAL_GYRO_FS_DPS",
    ):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_defaults_load_without_env_file(clean_env: pytest.MonkeyPatch) -> None:
    s = Settings(_env_file=None)
    assert s.domain == "dash.example.com"
    assert s.udp_port == 5005
    assert s.api_port == 8000
    assert s.redis_url == "redis://redis:6379/0"
    assert s.expected_input_hz == 640
    assert s.output_hz == 60
    assert s.jitter_buffer_ms == 50
    assert s.offline_after_s == 2
    assert s.limb_map == {
        (0, 1): "left_shin",
        (0, 2): "left_thigh",
        (1, 1): "right_thigh",
        (1, 2): "right_shin",
    }
    assert s.past_windows == [timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2)]
    assert s.future_horizons == [timedelta(minutes=10), timedelta(minutes=30), timedelta(hours=1)]
    assert s.predict_train_window == timedelta(hours=2)
    assert s.metrics_retention == timedelta(days=30)
    # Was 300 with no bootstrap, which put the first forecast 15-20 min into a
    # session: 10 one-minute cagg buckets (~10 min) + 1-2 min materialization lag
    # + up to one 5-minute job interval (docs/ANALYTICS.md §3.4).
    assert s.predict_interval_s == 60
    assert s.predict_bootstrap_bucket_s == 15
    assert s.predict_bootstrap_window == timedelta(minutes=15)
    assert s.predict_bootstrap_horizons == [
        timedelta(minutes=1), timedelta(minutes=2), timedelta(minutes=5)]
    # MIN_BUCKETS(10) x 15s = 2.5 min to the first forecast, vs 10 min of
    # aggregate buckets before
    assert s.predict_bootstrap_bucket_s * 10 <= 150
    # Insight cadence, retuned 2026-08-04 (docs/ANALYTICS.md "Insight cadence").
    # Was 60/600 with detection on the 5m window, which made the first insight of
    # a session take 5-9 min to appear: a 5-minute MEAN needs ~1.5-2 min of new
    # data to cross a threshold, plus up to 60 s of job interval. Detection now
    # runs on a 30s live window and the four numbers are tuned as a set.
    assert s.insight_live_window == timedelta(seconds=30)
    assert s.insight_interval_s == 15
    assert s.insight_cooldown_s == 120
    # hold MUST exceed cooldown or a still-true condition drops off the panel
    # for one tick before re-firing, which reads as flicker
    assert s.insight_hold_s == 150
    assert s.insight_hold_s > s.insight_cooldown_s
    assert s.insight_max_actions == 3
    assert s.jwt_expire_hours == 24
    assert s.seed_users == "trainer:changeme"
    # Unilateral knee sleeve (2026-09-23): the firmware fixes sensor 1 = thigh,
    # 2 = shin and defaults to +-32 g / +-4000 dps; the side is never on the wire.
    assert s.unilateral_sensor_map == {1: "thigh", 2: "shin"}
    assert s.unilateral_accel_fs_g == 32
    assert s.unilateral_gyro_fs_dps == 4000


def test_unilateral_settings_parse_from_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("UNILATERAL_SENSOR_MAP", '{"1": "femur", "2": "tibia"}')
    clean_env.setenv("UNILATERAL_ACCEL_FS_G", "16")
    clean_env.setenv("UNILATERAL_GYRO_FS_DPS", "2000")
    s = Settings(_env_file=None)
    assert s.unilateral_sensor_map == {1: "femur", 2: "tibia"}
    assert s.unilateral_accel_fs_g == 16
    assert s.unilateral_gyro_fs_dps == 2000


@pytest.mark.parametrize(("key", "value", "match"), [
    ("UNILATERAL_ACCEL_FS_G", "12", "one of"),
    ("UNILATERAL_GYRO_FS_DPS", "3000", "one of"),
    ("UNILATERAL_SENSOR_MAP", '{"1": "shin", "2": "shin"}', "distinct"),
    ("UNILATERAL_SENSOR_MAP", '{"1": "left_thigh", "2": "shin"}', "no side"),
    ("UNILATERAL_SENSOR_MAP", '{"3": "thigh"}', "1 or 2"),
])
def test_bad_unilateral_settings_fail_at_load(
    clean_env: pytest.MonkeyPatch, key: str, value: str, match: str,
) -> None:
    clean_env.setenv(key, value)
    with pytest.raises(ValueError, match=match):
        Settings(_env_file=None)


def test_limb_map_parsed_from_json_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv(
        "LIMB_MAP",
        '{"0,1": "left_thigh", "0,2": "left_shin", "1,1": "right_shin", "1,2": "right_thigh"}',
    )
    s = Settings(_env_file=None)
    assert s.limb_map == {
        (0, 1): "left_thigh",
        (0, 2): "left_shin",
        (1, 1): "right_shin",
        (1, 2): "right_thigh",
    }


def test_duplicate_limb_names_are_rejected(clean_env: pytest.MonkeyPatch) -> None:
    """Two sensors on one limb name silently wipes the biomech session.

    The ticker keys `frames` by limb name, so a duplicate makes one sensor
    overwrite the other's frame and leaves ingest's `limbs` tuple shorter than
    `frames.keys()` — which rebuilds biomech's session state every tick. It
    renders as a permanently flat athlete, not as a config error, so it has to
    fail at load.
    """
    clean_env.setenv(
        "LIMB_MAP",
        '{"0,1": "left_shin", "0,2": "left_shin", "1,1": "right_thigh", "1,2": "right_shin"}',
    )
    with pytest.raises(ValueError, match="unique"):
        Settings(_env_file=None)


def test_env_overrides_apply(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("UDP_PORT", "6001")
    clean_env.setenv("PAST_WINDOWS", "1h,1d,3d")
    s = Settings(_env_file=None)
    assert s.udp_port == 6001
    assert s.past_windows == [timedelta(hours=1), timedelta(days=1), timedelta(days=3)]


def test_bad_duration_in_env_raises(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("PAST_WINDOWS", "5m,nonsense")
    s = Settings(_env_file=None)
    with pytest.raises(ValueError):
        _ = s.past_windows


# --- redis keys (BACKEND_SCHEMA §4) --------------------------------------------

def test_redis_keys_match_schema() -> None:
    assert redis_keys.TICKS_CHANNEL == "ticks"
    assert redis_keys.INGEST_STATS == "ingest:stats"
    assert redis_keys.last_seen_dev("30") == "last_seen:dev:30"
    assert redis_keys.last_seen_sensor("30", 0, 1) == "last_seen:sensor:30:0:1"
    # sleeve unit ids flow through unchanged (no ':' inside them, by design)
    assert redis_keys.last_seen_dev("u30-0") == "last_seen:dev:u30-0"
    assert redis_keys.unit_cfg("u30-0") == "unit:cfg:u30-0"
    assert redis_keys.UNIT_CFG_CHANNEL == "unit_cfg"
    assert redis_keys.UNIT_CFG_PATTERN == "unit:cfg:*"
