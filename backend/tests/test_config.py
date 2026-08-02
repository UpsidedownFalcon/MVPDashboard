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
        "METRICS_RETENTION",
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
    assert s.predict_interval_s == 300
    assert s.insight_interval_s == 60
    assert s.insight_cooldown_s == 600
    assert s.jwt_expire_hours == 24
    assert s.seed_users == "trainer:changeme"


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
