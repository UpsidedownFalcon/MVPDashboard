# Backend Schema — Database DDL, REST/WS API, Redis contract

| | |
|---|---|
| Status | Set in stone for the MVP build. Metric *names* (`m1..m5`, `composite`) are stable column/field IDs (**5 primitives + 1 composite — confirmed**); their meanings are TBD (stage-1 biomech session) — display names live only in the frontend. Staged activation: §1 DDL + REST routes from stage 2 (`users` table used from stage 3); §2 tick format and §4 Redis contract from stage 1; auth on routes from stage 3 (until then all routes open — TRD §1.1). |
| Related | [TRD.md](TRD.md) · [APPFLOW.md](APPFLOW.md) |

## 1. Database DDL (TimescaleDB) — `backend/migrations/001_init.sql`

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Preset login accounts (seeded by seed_users.py from SEED_USERS env)
CREATE TABLE users (
    user_id        SERIAL PRIMARY KEY,
    username       TEXT UNIQUE NOT NULL,
    password_hash  TEXT NOT NULL,                -- bcrypt
    role           TEXT NOT NULL DEFAULT 'trainer',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Device registry: auto-registered on first packet; display_name renameable in UI
CREATE TABLE devices (
    device_id      TEXT PRIMARY KEY,             -- str(device_id byte), e.g. '30'
    display_name   TEXT NOT NULL,                -- defaults to device_id
    first_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes          TEXT
);

-- 60Hz processed metrics (hot table). No FK to devices on purpose (hot path,
-- and auto-registration races) — devices row is upserted by the api on first tick.
CREATE TABLE metrics (
    time        TIMESTAMPTZ NOT NULL,            -- server-aligned tick time
    device_id   TEXT        NOT NULL,
    m1 REAL, m2 REAL, m3 REAL, m4 REAL, m5 REAL, -- primitives (defs TBD)
    composite   REAL NOT NULL,                   -- risk index (def TBD)
    quality     REAL NOT NULL                    -- 0..1 share of expected samples
);
SELECT create_hypertable('metrics', 'time', chunk_time_interval => INTERVAL '1 day');
CREATE INDEX ON metrics (device_id, time DESC);
-- retention duration comes from METRICS_RETENTION (migration runner substitutes)
SELECT add_retention_policy('metrics', INTERVAL '30 days');

-- 1-minute rollups, kept forever; all past-window queries read this
CREATE MATERIALIZED VIEW metrics_1m
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 minute', time) AS bucket,
       device_id,
       avg(m1) AS m1, avg(m2) AS m2, avg(m3) AS m3, avg(m4) AS m4, avg(m5) AS m5,
       avg(composite) AS composite,
       min(composite) AS composite_min,
       max(composite) AS composite_max,
       avg(quality)   AS quality,
       count(*)       AS n
FROM metrics
GROUP BY bucket, device_id
WITH NO DATA;
SELECT add_continuous_aggregate_policy('metrics_1m',
    start_offset      => INTERVAL '2 hours',
    end_offset        => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute');

-- Forecasts: one row per (prediction run, device, horizon); composite only (PRD)
CREATE TABLE forecasts (
    made_at        TIMESTAMPTZ NOT NULL,
    device_id      TEXT        NOT NULL,
    horizon        INTERVAL    NOT NULL,          -- parsed from FUTURE_HORIZONS
    target_time    TIMESTAMPTZ NOT NULL,          -- made_at + horizon
    composite_pred REAL NOT NULL,
    ci_low  REAL,
    ci_high REAL,
    model_version  TEXT NOT NULL DEFAULT 'linreg-stub-1',
    PRIMARY KEY (made_at, device_id, horizon)
);
CREATE INDEX ON forecasts (device_id, made_at DESC);

-- Insight feed
CREATE TABLE insights (
    insight_id  BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    device_id   TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('info','warning','alert')),
    rule_id     TEXT NOT NULL,
    message     TEXT NOT NULL,                    -- plain language for the trainer
    context     JSONB                             -- evidence: values that fired it
);
CREATE INDEX ON insights (device_id, created_at DESC);
```

Notes: window aggregates and forecasts are **not** columns on `metrics` (different
cadence/keys/retention — TRD §6). If sub-minute test windows ever need finer
resolution than `metrics_1m`, query `metrics` directly for windows < 5 min (the
`queries.py` helper picks the source table by window size).

## 2. Tick message (Redis `ticks` channel AND WebSocket payload) — one JSON object

```jsonc
{
  "type": "tick",
  "t":   "2026-08-02T21:14:03.250Z",  // server-aligned tick time (ISO, UTC)
  "dev": "30",
  "m":   [0.12, 0.34, 0.56, 0.41, 0.22],  // m1..m5
  "c":   0.42,                             // composite
  "q":   0.98                              // quality 0..1
}
```

Status event (WS only, emitted by api on transitions):

```jsonc
{ "type": "status", "dev": "30", "online": false, "last_seen": "2026-08-02T21:13:59Z" }
```

## 3. HTTP API (api service, proxied by Caddy under `/api` and `/ws`)

All routes require the auth cookie except `POST /api/auth/login` and liveness
`GET /api/health/live`. Errors: `{"detail": "..."}` with 4xx/5xx.

| Method & path | Request | Response (200) |
|---|---|---|
| POST `/api/auth/login` | `{"username","password"}` | `{"username","role"}` + Set-Cookie (401 on bad creds; simple rate limit) |
| POST `/api/auth/logout` | — | `{}` + cookie cleared |
| GET `/api/auth/me` | — | `{"username","role"}` |
| GET `/api/devices` | — | `[{"device_id","display_name","online":bool,"last_seen":ts\|null,"quality":num\|null,"sensors":[{"source_id","sensor_id","limb","rate_hz","last_seen"}]}]` |
| PATCH `/api/devices/{id}` | `{"display_name"}` | updated device object |
| GET `/api/metrics/recent` | `?device=30&seconds=30` | `{"device_id","t0",…,"rows":[[t_offset_ms,m1..m5,c,q],…]}` (compact arrays for chart backfill) |
| GET `/api/metrics/windows` | `?device=30` | `{"windows":[{"window":"5m","from":ts,"m":[…5 avgs],"composite":{"avg","min","max"},"quality":num,"trend":"up\|down\|flat"},…]}` — one entry per `PAST_WINDOWS`, `trend` vs the preceding equal-length window |
| GET `/api/forecasts/latest` | `?device=30` | `{"made_at":ts,"model_version","points":[{"horizon":"10m","target_time":ts,"pred","ci_low","ci_high"},…]}` (404-shaped empty if no run yet) |
| GET `/api/insights` | `?device=30&limit=20` (device optional) | `[{"insight_id","created_at","device_id","severity","rule_id","message","context"},…]` newest first |
| GET `/api/health` | — | `{"status","db":bool,"redis":bool,"ingest":{per-device/sensor rates, last_seen, drop counters},"api":{"ws_clients","ws_dropped","db_buffer","db_dropped"}}` |
| GET `/api/health/live` | — | `{"status":"ok"}` (unauthenticated liveness) |
| **WS** `/ws/live` | `?devices=30,31` (omit = all) — cookie-authed handshake | server→client stream of `tick` and `status` messages; closes 4401 on auth expiry |

## 4. Redis contract

| Key / channel | Type | Writer → reader | Content |
|---|---|---|---|
| `ticks` | pub/sub channel | ingest → api | tick JSON (§2), all devices on one channel |
| `last_seen:dev:{device_id}` | string (unix ms) | ingest → api | refreshed ≤1s while packets flow |
| `last_seen:sensor:{dev}:{src}:{sen}` | string (unix ms) | ingest → api | per-sensor liveness (detects one dead leg) |
| `ingest:stats` | hash, rewritten 1s | ingest → api (`/api/health`) | per-sensor `rate_hz`, counters: `recv, crc_fail, late_drop, buf_drop, ticks_out` |

No Redis persistence needed (`appendonly no`); everything in Redis is reconstructible.

## 5. Stable code interfaces (drop-in points for later sessions)

```python
# backend/ingest/biomech.py — REPLACED by the real algorithm later
def compute(frames: dict[str, np.ndarray], state: DeviceState) -> Metrics:
    """frames: limb name -> float32[n_samples, 6] (ax..gz, raw counts) since last
    tick, already time-aligned across limbs. Called at OUTPUT_HZ per device.
    Returns Metrics(m1..m5, composite). `state` persists across calls per device
    (for filters/calibration)."""

# backend/api/jobs/predict.py — REPLACED by the real model later
def fit(history: pd.DataFrame, horizons: list[timedelta]) -> dict[timedelta, Forecast]:
    """history: metrics_1m rows (bucket, composite, …) over PREDICT_TRAIN_WINDOW.
    Returns per-horizon Forecast(pred, ci_low, ci_high)."""

# backend/api/jobs/insights.py — rule list is the extension point
RULES: list[Rule]  # Rule(rule_id, severity, predicate(WindowData, ForecastData) -> Evidence | None, message_fn)
```
