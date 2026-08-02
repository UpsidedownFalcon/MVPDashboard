-- 001_init — full stage-2 schema, exactly docs/BACKEND_SCHEMA.md §1.
-- {{METRICS_RETENTION}} is substituted by migrate.py from the METRICS_RETENTION
-- config key. Statements after a `-- NOTRANSACTION` line run outside the
-- migration transaction (Timescale forbids continuous-aggregate DDL inside one).

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Preset login accounts (seeded by seed_users.py from SEED_USERS env; stage 3)
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
    m1 REAL, m2 REAL, m3 REAL, m4 REAL, m5 REAL, -- primitives, 0-100, nullable (SPEC §8)
    composite   REAL NOT NULL,                   -- injury risk 0-100
    quality     REAL NOT NULL                    -- 0..1 share of expected samples
);
SELECT create_hypertable('metrics', 'time', chunk_time_interval => INTERVAL '1 day');
CREATE INDEX ON metrics (device_id, time DESC);
SELECT add_retention_policy('metrics', INTERVAL '{{METRICS_RETENTION}}');

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

-- NOTRANSACTION
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

-- NOTRANSACTION
SELECT add_continuous_aggregate_policy('metrics_1m',
    start_offset      => INTERVAL '2 hours',
    end_offset        => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute');
