# Backend Schema — Database DDL, REST/WS API, Redis contract

| | |
|---|---|
| Status | Set in stone for the MVP build. Metric *names* (`m1..m5`, `composite`) are stable column/field IDs (**5 primitives + 1 composite — confirmed**). Their meanings are now **DECIDED** — [biomech/SPEC.md](biomech/SPEC.md) (S1-T14): `m1` Impact, `m2` Loading Rate, `m3` Accumulated Load, `m4` Movement Control, `m5` L/R Balance, `composite` Injury Risk; all **0–100**, nullable except `composite`. Display names live only in the frontend (SPEC §10). Staged activation: §1 DDL + REST routes from stage 2 (`users` table used from stage 3); §2 tick format and §4 Redis contract from stage 1; auth on routes from stage 3 (until then all routes open — TRD §1.1). |
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
    context     JSONB,                            -- evidence: values that fired it
    action      TEXT,                             -- migration 002: short imperative
    rationale   TEXT                              -- migration 002: the why, from metrics
);
CREATE INDEX ON insights (device_id, created_at DESC);
```

**Migration `002_insight_actions.sql`** (stage 3, S3-T01 decision 2026-08-03): adds
nullable `action` (short imperative, e.g. "Reduce landing volume") and `rationale`
(plain-language why, grounded in the measured metrics) to `insights`. The UI renders
`action` as the card headline and `rationale` beneath it, falling back to `message`
when null (rows from pre-refinement rules). Rules SHOULD populate both; `message`
remains required as the self-contained plain-language summary.

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
  "m":   [42.2, 48.3, 24.2, 0.0, -37.0],  // m1..m4 are 0..100 or null;
                                          // m5 is SIGNED -100..100 (see below)
  "c":   45.3,                             // composite 0..100
  "q":   0.98,                             // quality 0..1
  "f":   ["unvalidated", "warming_up"]     // active biomech flags, sorted; null when none
}
```

**`f` (flags)** carries the sorted `Metrics.flags` set from the biomech model, or `null`
when no flag is active. The vocabulary is fixed by [biomech/SPEC.md](biomech/SPEC.md) §10:
`warming_up`, `partial`, `no_shank`, `saturated`, `degraded_sensors`, `uncalibrated`,
`cal_failed`, `carried_over`, `unvalidated`. It is how the UI distinguishes "no data" from
"zero", marks the uncalibrated→calibrated step change, and shows that `m4`/`m5` are
`unvalidated`. Flags are display/diagnostic state only — they are **not** written to the
`metrics` table (the full diagnostic set goes to `biomech:diag:{device_id}`, §4).

⚠️ **`m5` is SIGNED, −100..+100** (changed 2026-08-03). **Positive = left-dominant, negative =
right.** The magnitude to display is `|m5|`; the sign is which side is carrying more load *in this
session*. It must never be presented as "this leg is weaker" or compared across sessions — limb
dominance does not reproduce (κ = −0.14 to 0.60) and greater asymmetry has not been shown to
predict injury. Anything treating m5 as a severity must take `abs()`. **Frontend: show `|m5|` as
the value and the sign as a side label.**

**Ranges (set by [biomech/SPEC.md](biomech/SPEC.md) §5, S1-T14):** `m1..m4` and `c` are
**0–100** arbitrary units (the pre-SPEC stub emitted 0–1 — frontend axis bounds must match
the model). `q` remains 0–1.

**`m` entries may be `null`** when a primitive is unavailable — the device streams fewer than 4
sensors (SPEC §8), the metric is still warming up (`m4` needs 60 s of movement, `m5` needs 30 s),
a required sensor went inactive mid-session (`partial` — SPEC §5.4/§5.5), or `m4`'s current
intensity band has not yet learned a baseline (SPEC §5.4). `c` and `q` are **never null**.

⚠️ **Saturation no longer nulls `m1`/`m2`** (changed 2026-08-03, SPEC §3.7). When the `saturated`
flag is present those two are **lower bounds** — the ±16 g part clips inside real athletic
movement, and 35 g / 42 g / 60 g / 100 g landings all read the same `m1`. **The UI must render
them as "≥ x" whenever `saturated` is in `f`**, and must not present them as exact. They stay
monotonic, so ordering is still safe; only the magnitude is a floor. The DDL already allows
this (`m1..m5` nullable `REAL`; only `composite`/`quality` are `NOT NULL`), and `metrics_1m`'s
`avg()` skips NULLs, which is the desired behaviour.

⚠️ **`m4` and `m5` are `null` far more often than the other three**, by design — expect them
absent for the first minute of every session, and whenever a leg loses a sensor. Charts must
render gaps, never zero (SPEC §9). Both also carry the `unvalidated` flag through stage 1: they
have no real-data validation (SPEC §11.1).

Display names, bands and the "render null as greyed, never as 0" rule: SPEC §10.

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
| GET `/api/metrics/windows` | `?device=30` | `{"windows":[{"window":"5m","from":ts,"m":[…5 avgs],"sd":[…5 std devs],"composite":{"avg","min","max","sd"},"quality":num,"coverage":num\|null,"trend":"up\|down\|flat"},…]}` — one entry per `PAST_WINDOWS`, `trend` vs the preceding equal-length window. **`sd`** is the within-window standard deviation of each metric; **`coverage`** is observed rows ÷ expected rows for the window (0–1). Both added 2026-08-03: `sd` is what lets an insight express a deviation in units of the athlete's own spread — the property that makes the rule catalogue survive a biomech retune (docs/ANALYTICS.md §4.1) — and `coverage` is what distinguishes a full window from a sliver of one. Both nullable when the window is empty. |
| GET `/api/metrics/history` | `?device=30&window=30m&buckets=24` | `{"device_id","window","from":ts,"bucket_s":int,"buckets":[{"t":ts,"m":[…5 avgs\|null],"composite":{"avg","min","max"},"quality":num}\|null,…]}` — stage-3 (S3-T01): time-bucketed series for the History tab. `window` MUST be one of `PAST_WINDOWS` (400 otherwise); `buckets` 1–96, default 24; bucket span = window/buckets, clamped to ≥1m when reading `metrics_1m` (bucket count shrinks accordingly — the response's `bucket_s` is authoritative). Buckets are aligned to `from`. Reads `metrics_1m` (or `metrics` for windows ≤5m, same source rule as `/windows`); a bucket with no rows is `null` (chart gap, never 0) |
| GET `/api/forecasts/latest` | `?device=30` | `{"made_at":ts,"model_version","points":[{"horizon":"10m","target_time":ts,"pred","ci_low","ci_high"},…]}` (404-shaped empty if no run yet) |
| GET `/api/insights` | `?device=30&limit=20` (device optional) | `[{"insight_id","created_at","device_id","severity","rule_id","message","context","action":str\|null,"rationale":str\|null},…]` newest first (`action`/`rationale`: migration 002, §1) |
| GET `/api/health` | — | `{"status","db":bool,"redis":bool,"ingest":{per-device/sensor rates, last_seen, drop counters},"api":{"ws_clients","ws_dropped","db_buffer","db_dropped"}}` |
| GET `/api/health/live` | — | `{"status":"ok"}` (unauthenticated liveness) |
| **WS** `/ws/live` | `?devices=30,31` (omit = all) — cookie-authed handshake | server→client stream of `tick` and `status` messages; closes 4401 on auth expiry |

## 4. Redis contract

| Key / channel | Type | Writer → reader | Content |
|---|---|---|---|
| `ticks` | pub/sub channel | ingest → api | tick JSON (§2), all devices on one channel |
| `last_seen:dev:{device_id}` | string (unix ms) | ingest → api | refreshed ≤1s while packets flow |
| `last_seen:sensor:{dev}:{src}:{sen}` | string (unix ms) | ingest → api | per-sensor liveness (detects one dead leg) |
| `ingest:stats` | hash, rewritten 1s | ingest → api (`/api/health`) | per-sensor `rate_hz`, counters: `recv, crc_fail, late_drop, buf_drop, ticks_out, sat_count` |
| `biomech:diag:{device_id}` | hash, rewritten 1s, TTL `2×SESSION_GAP_S` | ingest → api (`/api/health`) | biomech diagnostics (SPEC §10 item 3), all values formatted `%.6g`: `flags` (comma-separated), signed transmission ratio `R`, `R_base`, signed **`usi_pct`** (`m5`'s pre-normalisation USI, in percent), `dose`, `move_t`, `intensity`, `a_int`, `w_int`, `sat_frac`, `m1_lo`, per-tick noise weight `W`, `demand`, `degradation` — for tuning the provisional reference bounds against real trial data |
| `biomech:state:{device_id}` | **string (JSON), rewritten 1s, TTL `2×SESSION_GAP_S`** | ingest → **ingest** | **Warm-restart snapshot** (~200 B, SPEC §7.4). A single JSON document, *not* a hash — it is written and read whole, so one `SET` beats a multi-field `HSET`. Fields: `v` (schema version, currently `1` — a mismatch is rejected and the session starts fresh), `dose`, `accL`, `accR`, `move_t`, `R_base`, `session_start_t`, `last_tick_t` (both unix seconds), `cal` = `{limb_name: {k, gyro_bias[3], sigma}}` **keyed by limb name, never by slot index** (§7.4), and `cal_src` = `{limb_name: "default"\|"carried"\|"measured"}` (calibration provenance, §5). Written fire-and-forget; **read only by ingest**, applying elapsed-time decay before use, and discarded when `now − last_tick_t > SESSION_GAP_S`. Without it an ingest restart silently resets a mid-session athlete to zero accumulated load. |
| `biomech:cal:{device_id}` | string (JSON), rewritten 1s, **TTL 30 days** | ingest → **ingest** | **Last-known-good calibration, carried BETWEEN sessions** (SPEC §3.8): `{limb_name: {k, gyro_bias[3], sigma}}`, limb-keyed for the same reason as above. Deliberately *not* the §7.4 snapshot, which is discarded after `SESSION_GAP_S` — that is exactly the case this key exists for, an athlete returning the next day. Read once when a device appears and applied immediately, so a device with any history starts calibrated and only refines from there; upgraded in place when a fresh still window lands. Only a device with no history ever runs on defaults. |

`sat_count` counts samples with any axis within 1% of full scale (±16 g / ±2000 °/s). The
squats log peaks at 1875 °/s against a 2000 °/s ceiling, so saturation is a live risk on
harder movements; saturated windows are reported but **not** discarded (a clipped impact is
still a real large impact). See [biomech/SPEC.md](biomech/SPEC.md) §2.

No Redis persistence needed (`appendonly no`); everything in Redis is reconstructible.

## 5. Stable code interfaces (drop-in points for later sessions)

```python
# backend/ingest/biomech.py — algorithm specified in docs/biomech/SPEC.md (S1-T14),
# implemented in S1-T15. The SIGNATURE below is unchanged by the spec.
def compute(frames: dict[str, np.ndarray], state: DeviceState) -> Metrics:
    """frames: limb name -> float32[n_samples, 6] (ax..gz, raw counts) since last
    tick, already time-aligned across limbs. Called at OUTPUT_HZ per device.
    Returns Metrics(m1..m5, composite), all 0..100. `state` persists across calls
    per device and holds the 1 s derived-scalar ring buffers, filter state, and
    session accumulators (SPEC §7). No calibration input is required: compute()
    detects still windows itself and calibrates in place (SPEC §3.8)."""

@dataclass
class Metrics:
    m1: float | None    # Impact          0..100  (None if unavailable — SPEC §8)
    m2: float | None    # Loading Rate    0..100
    m3: float | None    # Accumulated Load 0..100
    m4: float | None    # Movement Control 0..100 (None while warming up)
    m5: float | None    # L/R Balance     0..100 (None while warming up)
    composite: float    # Injury Risk     0..100  — never None
    flags: frozenset[str]     # SPEC §10: 'warming_up','partial','no_shank','saturated'
                              #   ('saturated' => m1/m2 are LOWER BOUNDS, render ">= x"),
                              # 'degraded_sensors','unvalidated', and the three
                              # calibration states below
    raw:   dict[str, float]   # pre-normalisation diagnostics -> biomech:diag:{dev} (§4)

# Calibration flags (SPEC §3.8, §10) — mutually informative, not exclusive:
#   'uncalibrated'  at least one sensor is on defaults: no history, no still window
#   'carried_over'  at least one sensor is running last-known-good values from a
#                   PREVIOUS session (biomech:cal:{dev}, §4) — applied, but not
#                   measured on this athlete today
#   'cal_failed'    a still window was found but its k fell outside [0.95, 1.05];
#                   the correction was refused, last-known-good stands, and the
#                   search continues
# Calibration is automatic (still-detection, no trainer action). The transition
# to calibrated is a visible step in m1/m3 and more in m4/m5, so the UI marks it
# from these flags rather than presenting it as a change in the athlete.

# backend/common/scaling.py — raw counts to SI (ICM-45686, verified vs example/squats.bin)
ACCEL_MS2_PER_COUNT = 9.81 / 2048     # ±16 g  -> m/s²
GYRO_DPS_PER_COUNT  = 1.0 / 16.384    # ±2000 °/s -> °/s

# backend/api/jobs/predict.py — signature is STABLE; the model lives inside it
def fit(history: pd.DataFrame, horizons: list[timedelta]) -> dict[timedelta, Forecast]:
    """history: metrics_1m rows (bucket, composite, m3, …) over
    PREDICT_TRAIN_WINDOW. Returns per-horizon Forecast(pred, ci_low, ci_high)."""

# ⚠️ Under model_version 'dose-scenario-1' (docs/ANALYTICS.md), `ci_low`/`ci_high`
# carry a SCENARIO BAND, not a confidence interval:
#     ci_low  = "if they stop now"                     -> the decaying dose floor
#     pred    = "if recent load continues"
#     ci_high = "if load returns to this session's hardest"
# The columns are unchanged (they are just numbers) but the MEANING follows
# model_version, and the frontend must label it accordingly — calling two
# counterfactuals a "CI" asserts a 95% probability that the truth lies between
# them, which biomech SPEC §2 forbids. Consumers that cannot read model_version
# must not present these as uncertainty. 'linreg-stub-1' rows (a plain OLS
# prediction interval) may still exist in `forecasts` from before the change.

# backend/api/jobs/insights.py — rule list is the extension point
# backend/api/jobs/insights.py — RULES is the stable extension point
@dataclass
class Rule:
    rule_id:   str
    severity:  str                                    # default; evidence may raise it
    evaluate:  Callable[[Ctx], Evidence | None]
    message:   Callable[[Ctx, Evidence], str]         # standalone summary
    action:    Callable[[Ctx, Evidence], str] | None  # short imperative (UI headline)
    rationale: Callable[[Ctx, Evidence], str] | None  # the why, with the numbers

RULES: list[Rule]

# `Ctx` gives a rule the WHOLE picture, not one number (docs/ANALYTICS.md §4.2):
#   ctx.metrics   -> {'m1'..'m5','composite': MetricView}  each across every window
#   ctx.horizons  -> every projected point incl. the ci_low/ci_high scenario band
#   ctx.shortest / ctx.mid / ctx.longest / ctx.windows / ctx.forecasts
#   ctx.trustworthy -> coverage + quality-match gate for athlete-facing claims
#
# MetricView.z = (now - baseline) / max(sd, floor) is the field rules fire on. It
# is EXACTLY invariant to a rescale of the metric's 0-100 bounds, so retuning the
# biomech model changes what an insight REPORTS without changing whether it fires.
```
