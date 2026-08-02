# TRD — Technical Requirements & Architecture

| | |
|---|---|
| Status | Approved. §-level "TBD" markers show what later sessions may change. |
| Related | [PLAN.md](PLAN.md) · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) · [APPFLOW.md](APPFLOW.md) · [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) |

## 1. System overview — SET IN STONE

Five Docker Compose services on one VPS (identical stack locally via Docker Desktop):

```
Wearables (1–5) ──UDP :5005 (public)──▶ ┌──────── ingest (Python) ────────┐
  4 IMUs each, ~600Hz/sensor           │ UDP → CRC → decode → jitter     │
                                       │ buffer → align → biomech →      │
                                       │ 60Hz ticker → Redis publish     │
                                       └────────────┬────────────────────┘
                                                    ▼
                                       ┌────────── redis ────────────────┐
                                       │ pub/sub 'ticks' + status keys   │
                                       └────┬───────────────────┬────────┘
                                            ▼                   ▼
        ┌────────── api (Python/FastAPI) ─────────┐   (ingest stats keys)
        │ WS fan-out /ws/live (60Hz)              │
        │ batched DB writer (1s flush)            │
        │ REST: auth, devices, metrics, forecasts,│
        │       insights, health                  │
        │ jobs: predict loop, insights loop       │
        └───────┬─────────────────────▲───────────┘
                ▼                     │
        ┌── db: TimescaleDB ──┐   ┌── caddy :80/:443 ──────────────┐
        │ hypertable + caggs  │   │ auto-TLS, serves React build,  │
        │ (no public port)    │   │ reverse-proxies /api and /ws   │
        └─────────────────────┘   └────────────────────────────────┘
```

**Failure-isolation rationale (why not one process):** `ingest` has no DB and no HTTP —
a slow query, stuck WebSocket client, or DB outage physically cannot stall packet
processing. Either Python service can crash/restart independently. Redis pub/sub is
fire-and-forget: if `api` is down, ticks published during the outage are simply lost
(acceptable — live view has no consumer then; history gap is visible in charts).

**Backpressure golden rule:** every stage owns a **bounded buffer with drop-oldest
policy and a monotonically increasing drop counter**. Nothing ever blocks upstream.
Counters are exposed (ingest → Redis stats keys → `GET /api/health`).

### 1.1 Staged rollout — SET IN STONE (revised 2026-08-02)

This diagram is the **end state**; components turn on per stage
(details: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)):

| Component | Stage 1 (local) | Stage 2 (public VPS) | Stage 3 |
|---|---|---|---|
| redis, ingest | ✔ (real biomech is stage 1's goal) | ✔ | ✔ |
| api | minimal: WS fan-out + `/debug` viewer page | + writer, REST, predict/insight jobs | + auth on every route |
| db (TimescaleDB) | — | ✔ | ✔ |
| caddy | — (direct `localhost:8000`) | ✔ (TLS, serves crude UI) | ✔ (serves product UI) |
| frontend | `/debug` page only | crude disposable AI-generated UI | designed product UI + login |
| auth | none | **none — fully public temporarily (accepted risk)** | JWT cookie enforced |

Wearables in stage 1 target the dev machine's LAN IP `:5005/udp`; from stage 2 the
VPS IP. Nothing except the stage-2 crude UI is throwaway.

## 2. Runtime & stack — SET IN STONE

| Component | Choice | Notes |
|---|---|---|
| Python | 3.12, `uv`-managed, one `backend/` package shared by both services | asyncio + uvloop |
| ingest | plain asyncio program (no web framework) | `asyncio.DatagramProtocol` |
| api | FastAPI + uvicorn (uvloop worker) | REST + WebSocket |
| DB | TimescaleDB (`timescale/timescaledb:latest-pg17` image) | = Postgres + hypertables/caggs |
| DB driver | asyncpg | batched `copy_records_to_table` |
| Broker | Redis 7 (`redis:7-alpine`) | pub/sub + status keys only |
| Proxy | Caddy 2 | automatic Let's Encrypt |
| Frontend | React 18 + Vite + TypeScript; uPlot (live charts); ECharts (forecast/history); TanStack Query | built to static files, served by Caddy |
| Numerics | numpy (vectorized; per-batch, never per-sample Python loops) | |

## 3. Wire protocol — SET IN STONE (verified against `example/`)

One UDP datagram = one 21-byte record = one sample from one sensor:

| Offset | Field | Type | Notes |
|---|---|---|---|
| 0 | device_id | u8 | wearable unit / person |
| 1 | source_id | u8 | leg MCU: 0 or 1 |
| 2 | sync | u8 | must be 0xA5, else drop |
| 3 | header | u8 | bits[1:0] = sensor_id (1 or 2), bits[7:2] = version (=1) |
| 4–7 | timestamp_us | u32 LE | device-local µs, **wraps every ~71.6 min**, monotonic per source only |
| 8–19 | ax ay az gx gy gz | 6 × i16 LE | raw counts, unscaled |
| 20 | crc8 | u8 | poly 0x07, init 0x00, MSB-first, over bytes 3..19 of the datagram (= wire[1..17]) |

Decode/CRC logic is ported from `example/parse_imu.py` (vectorized table CRC).
Datagrams failing sync or CRC are dropped and counted. Sensor key = `(device_id,
source_id, sensor_id)`; **device identity comes from the payload, never the UDP
source address** (NAT may rewrite it).

**Limb mapping — default SET, values CONFIGURABLE** (`LIMB_MAP` in `.env`):
`(0,1)=left_shin, (0,2)=left_thigh, (1,1)=right_thigh, (1,2)=right_shin`.

Expected stream rate ~600Hz/sensor (device decimates from ~6.6kHz). Ingest never
assumes the exact rate: it measures per-sensor rate live and computes quality against
`EXPECTED_INPUT_HZ`.

## 4. Ingest pipeline design — SET IN STONE (parameters configurable)

1. **UDP server:** `DatagramProtocol.datagram_received` does *only* length check +
   append to a raw `deque` (bounded, ~2s worth). `SO_RCVBUF` raised to 4MB at socket
   creation. A pipeline task drains and decodes in numpy batches every ~10ms.
2. **Timestamp unwrap:** per sensor, u32 µs timestamps are unwrapped to i64
   (decrease > 2³¹ ⇒ +2³²).
3. **Clock alignment:** per `(device, source)`, maintain `offset = server_recv_time −
   device_ts` as a rolling minimum (least-queued packets) with slow drift tracking;
   map all samples to server time. Offset jump > `RESET_OFFSET_JUMP_S` (default 5s)
   ⇒ device/leg rebooted ⇒ reset that source's buffers. Legs are paired on mapped
   server time (tolerance ±25ms) — raw timestamps are never compared across sources.
4. **Jitter buffer:** per sensor, samples are held `JITTER_BUFFER_MS` (default 50ms)
   sorted by unwrapped timestamp, releasing reordered data in order; late arrivals
   beyond the window are dropped and counted.
5. **Framing:** on each 60Hz tick, all samples per limb released since the previous
   tick (~10 at 600Hz) are assembled as `frames: {limb: float32[n, 6]}`, resampled/
   padded as needed — biomech gets a rate-flexible input.
6. **Biomech (STUB tonight — interface SET IN STONE):**
   `compute(frames: dict[str, ndarray], state) -> (m1, m2, m3, m4, m5, composite)`
   called at 60Hz per device. Stub: per-limb gyro/accel RMS energy + weighted
   normalized composite, so charts move realistically with the replayed squat data.
   The real algorithm replaces `biomech.py` only. *Definitions of the 6 metrics: TBD
   (dedicated session; notes go in `docs/biomech/`).*
7. **60Hz ticker:** wall-clock driven (`asyncio` timer, drift-corrected by absolute
   scheduling). **Emits every tick regardless of input** — on missing data it
   holds the last value (flag `held=true` internally, quality reflects it). Output
   cadence is constant even if the input rate wobbles. If no packets for
   `OFFLINE_AFTER_S` (default 2s), the device's ticker suspends (device offline)
   rather than streaming stale holds.
8. **Quality:** per tick, `received_samples / expected_samples` across the device's
   4 sensors (expected = `EXPECTED_INPUT_HZ / OUTPUT_HZ × 4`), clamped to [0,1].
9. **Publish:** tick JSON → Redis channel `ticks`; per-device/sensor `last_seen` and
   rate/drop stats → Redis keys every 1s (see [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) §4).

**Performance budget:** 12k pkt/s decode ≈ single-digit % of one core vectorized;
numpy releases the GIL. Escape hatch if the real biomech is heavy: per-device
`ProcessPoolExecutor` — do not build until profiling demands it.

## 5. api service — SET IN STONE

- **WS fan-out:** subscribes `ticks`; per-client bounded `asyncio.Queue(maxsize=120)`,
  drop-oldest. Client subscribes to all or selected devices via query param.
- **DB writer:** same subscription feeds an in-memory buffer flushed once per second
  with asyncpg COPY (≤300 rows). DB down ⇒ buffer caps at ~60s then drops oldest +
  counts; WS streaming unaffected.
- **REST + WS API:** full spec in [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) §3.
- **Auth (activated in stage 3):** bcrypt (passlib) password hashes; JWT (HS256,
  `JWT_SECRET`) in an httpOnly/Secure/SameSite=Lax cookie, `JWT_EXPIRE_HOURS`
  (default 24). Cookie authenticates REST and the WS handshake. Users seeded by
  `seed_users.py` from env. Stages 1–2 run all routes unauthenticated (§1.1).
- **Jobs (asyncio loops in the api process):**
  - *predict* every `PREDICT_INTERVAL_S` (default 300): per device, fit on recent
    `metrics_1m` composite over `PREDICT_TRAIN_WINDOW`; write one row per horizon in
    `FUTURE_HORIZONS` to `forecasts`. **Stub tonight:** numpy linear fit, CI from
    residual std × horizon scaling. *Model: TBD (dedicated session). Interface SET:*
    `predict.fit(history: DataFrame) -> {horizon: (pred, ci_low, ci_high)}`.
  - *insights* every `INSIGHT_INTERVAL_S` (default 60): evaluate a rule list over
    window aggregates + latest forecasts; insert rows with per-rule cooldown
    (`INSIGHT_COOLDOWN_S`, default 600) to avoid spam. **Starter rules tonight**
    (composite threshold; rising trend + forecast crossing). *Rule catalogue &
    thresholds: TBD.*

## 6. Storage — SET IN STONE

One TimescaleDB for everything (full DDL in [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md)):
`metrics` 60Hz hypertable (retention `METRICS_RETENTION`, default 30d) → `metrics_1m`
continuous aggregate (kept forever; past-window queries aggregate over it at request
time) → separate `forecasts`, `insights`, `devices`, `users` tables.
Explicitly rejected: extra columns on `metrics` for windows/forecasts (different
cadence, keys, and retention; constant UPDATEs would bloat Postgres).

## 7. Configuration — SET IN STONE (single source of truth)

Everything that connects components lives in **one root `.env`** (template:
`.env.example`), loaded by compose and by `backend/common/config.py`
(pydantic-settings). File-local tuning constants may stay in their files.

| Key | Default (test) | Notes |
|---|---|---|
| `DOMAIN` | dash.example.com | dashboard hostname (Caddy + cookies) |
| `UDP_PORT` | 5005 | device ingest |
| `API_PORT` | 8000 | internal only (Caddy proxies) |
| `POSTGRES_*` | db/5432/mvpdash/… | host, port, db, user, password |
| `REDIS_URL` | redis://redis:6379/0 | |
| `JWT_SECRET`, `JWT_EXPIRE_HOURS` | —, 24 | |
| `SEED_USERS` | trainer:changeme | comma-sep `user:pass` pairs, seeded once |
| `EXPECTED_INPUT_HZ` | 600 | per sensor |
| `OUTPUT_HZ` | 60 | tick + WS + DB rate |
| `LIMB_MAP` | JSON, see §3 | `(source,sensor) → limb` |
| `JITTER_BUFFER_MS` | 50 | reorder window |
| `OFFLINE_AFTER_S` | 2 | online/offline threshold |
| `PAST_WINDOWS` | `5m,30m,2h` | **3 durations; deployment e.g. `1h,1d,3d`** |
| `FUTURE_HORIZONS` | `10m,30m,1h` | deployment e.g. `1d,3d,1w` |
| `PREDICT_INTERVAL_S` / `PREDICT_TRAIN_WINDOW` | 300 / 2h | |
| `INSIGHT_INTERVAL_S` / `INSIGHT_COOLDOWN_S` | 60 / 600 | |
| `METRICS_RETENTION` | 30d | hypertable retention |

Duration syntax everywhere: `<int><s|m|h|d|w>`.

## 8. Deployment & network — SET IN STONE (provider = Hetzner unless changed)

- VPS: Hetzner CX22-class (2 vCPU / 4GB, ~€5/mo), Ubuntu LTS, Docker + compose plugin.
- DNS: Cloudflare **DNS-only (grey cloud)** A-record `dash.<domain>` → VPS IP.
  Cloudflare cannot proxy UDP, and DNS-only lets Caddy obtain Let's Encrypt certs
  with zero extra config. Devices are configured with the **raw `VPS_IP:5005`**.
- Firewall (ufw): allow 22/tcp (key-only SSH, password auth off), 80+443/tcp,
  5005/udp. DB and Redis publish no host ports.
- Caddy: serves `frontend/dist`, proxies `/api/*` and `/ws/*` to `api:8000`, auto-TLS.
- Deploy flow: Windows dev (Docker Desktop/WSL2) → push to GitHub → `deploy/deploy.sh`
  = `ssh vps 'cd app && git pull && docker compose up -d --build'`. `.gitattributes`
  forces LF for `*.sh`, Caddyfile, `*.sql`.
- Docker UDP: published port via Docker NAT is fine at 12k pkt/s; if drops appear,
  switch ingest to `network_mode: host` (one-line change).

## 9. Security posture — SET IN STONE for MVP

- UDP port is open to the world: accepted MVP risk. Mitigations: strict length/sync/CRC
  validation, drop+count anything malformed, all values parameterized into SQL.
  *Post-MVP (TBD): truncated per-packet HMAC with a shared key; device allow-list.*
- Dashboard: **stage 2 is fully public temporarily (user-accepted risk** — no auth
  until stage 3; revisit immediately if identifiable trainee data appears before then).
  From stage 3: HTTPS only, httpOnly cookies, bcrypt, no signup surface, rate-limited
  login, all routes + WS authenticated.
- Secrets only in `.env` (never committed; `.env.example` has placeholders).

## 10. Observability — SET IN STONE

`GET /api/health` returns: per-device/sensor measured input rate, last_seen, tick
output rate, and all drop counters (CRC-fail, late-drop, buffer-drop, WS-drop,
DB-buffer-drop), plus DB/Redis connectivity. This is the first place to look when
anything misbehaves; the frontend quality badge is driven from the same numbers.

## 11. Open items (TBD ledger)

| Item | Where decided | Placeholder until then |
|---|---|---|
| 5 primitives + composite definitions, units, calibration | **stage 1** biomech session (`docs/biomech/` → SPEC.md) | RMS-energy stub during pipeline bring-up |
| Prediction model + CI method | prediction session (stage 2+) | linear regression stub |
| Insight rule catalogue + thresholds | insights session (stage 2+) | 2 starter rules |
| Final UI design | **stage 3** frontend session (`mockup/`) | stage-2 crude disposable UI |
| Production window/horizon durations | config flip at deploy | test durations |
| Per-packet auth (HMAC) | post-MVP | open UDP + validation |
