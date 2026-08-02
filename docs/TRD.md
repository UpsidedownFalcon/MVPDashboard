# TRD — Technical Requirements & Architecture

| | |
|---|---|
| Status | Approved. §-level "TBD" markers show what later sessions may change. |
| Related | [PLAN.md](PLAN.md) · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) · [APPFLOW.md](APPFLOW.md) · [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) |

## 1. System overview — SET IN STONE

Five Docker Compose services on one VPS (identical stack locally via Docker Desktop):

```
Wearables (1–5) ──UDP :5005 (public)──▶ ┌──────── ingest (Python) ────────┐
  4 IMUs each, ~640Hz/sensor           │ UDP → CRC → decode → jitter     │
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

One UDP datagram = one **22-byte** record = one sample from one sensor.
⚠️ The **SD log** (`example/squats.bin`) stores the same record **without** the
trailing byte, i.e. 21 bytes — `packet.decode()` reads datagrams, `decode_log()`
reads the file. Verified against a live capture from the real device (2026-08-02);
the earlier "21 bytes on the wire" reading of `parse_imu.py` was wrong and caused
every datagram to be rejected as `bad_len` with the device streaming normally.

| Offset | Field | Type | Notes |
|---|---|---|---|
| 0 | device_id | u8 | wearable unit / person |
| 1 | source_id | u8 | leg MCU: 0 or 1 |
| 2 | sync | u8 | must be 0xA5, else drop |
| 3 | header | u8 | bits[1:0] = sensor_id (1 or 2), bits[7:2] = version (=1) |
| 4–7 | timestamp_us | u32 LE | device-local µs, **wraps every ~71.6 min**, monotonic per source only |
| 8–19 | ax ay az gx gy gz | 6 × i16 LE | raw counts, unscaled |
| 20 | crc8 | u8 | poly 0x07, init 0x00, MSB-first, over bytes 3..19 of the datagram (= wire[1..17]) |
| 21 | soc | u8 | **UDP only, absent from the SD log.** Decoded and reported, never validated; meaning unconfirmed (varies sample to sample) |

Decode/CRC logic is ported from `example/parse_imu.py` (vectorized table CRC).
Datagrams failing sync or CRC are dropped and counted. Sensor key = `(device_id,
source_id, sensor_id)`; **device identity comes from the payload, never the UDP
source address** (NAT may rewrite it).

**Limb mapping — default SET, values CONFIGURABLE** (`LIMB_MAP` in `.env`):
`(0,1)=left_shin, (0,2)=left_thigh, (1,1)=right_thigh, (1,2)=right_shin`.

Measured stream rate **~640Hz/sensor** (device decimates from ~6.6kHz); the pre-hardware estimate of 600 is superseded. Ingest never
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
   ⇒ device/leg rebooted ⇒ reset that source's buffers. Raw timestamps are never
   compared across sources — mapped server time is the only common clock. There is
   no explicit leg-pairing step: both legs are released into the same 60Hz tick
   window (step 5), which is what "aligned across limbs" means downstream.
4. **Jitter buffer:** per sensor, samples are held `JITTER_BUFFER_MS` (default 50ms)
   sorted by unwrapped timestamp, releasing reordered data in order; late arrivals
   beyond the window are dropped and counted.
5. **Framing:** on each 60Hz tick, all samples per limb released since the previous
   tick (~11 at 640Hz) are assembled as `frames: {limb: float32[n, 6]}` plus the
   matching server-mapped `times: {limb: float64[n]}`. The ticker neither resamples
   nor pads: limbs may arrive with different sample counts and biomech consumes
   them as-is, deriving its filter coefficients from the measured `Δt`. Length
   equalisation happens one layer down, inside `biomech.compute`, as **hold-last**
   fill with a per-limb `valid_n` that keeps filled rows out of the summaries
   (biomech SPEC §7.2.1) — never zero-fill, which fabricates a 9.75 m/s² impact on
   reconnect.
6. **Biomech (interface SET IN STONE):**
   `compute(frames: dict[str, ndarray], state) -> Metrics(m1..m5, composite)`
   called at 60Hz per device. The real algorithm replaces `biomech.py` only.
   **Definitions are specified in [biomech/SPEC.md](biomech/SPEC.md)** (S1-T14);
   implemented in S1-T15. Summary of what the spec fixes:
   - **Orientation-free by mandate:** rotation-invariant magnitudes (`|a|`, `|ω|`) and
     time derivatives only. No absolute angles, no complementary/Kalman filter, no
     bone-aligned axis. Gravity is removed by high-passing the **scalar** `|a|`, never
     per-axis — per-axis (VeDBA/ODBA) fabricates ~7 m/s² of false impact at ordinary squat
     rotation rates (SPEC §3.3). Jerk uses the **exact identity** `‖Δa/Δt + ω×a‖`, which is
     gravity-free and rotation-invariant *identically*, from measured accel+gyro only
     (SPEC §3.4); this requires accel and gyro to stay synchronised — they share one packet.
   - **Primitives:** `m1` Impact (peak dynamic accel), `m2` Loading Rate (exact linear jerk),
     `m3` Accumulated Load (power-law-weighted decaying dose, exponent 3), `m4` Movement
     Control (**|drift|** of shank→thigh shock transmission vs. session baseline —
     direction-agnostic, as the literature does not fix the sign), `m5` L/R Balance (weighted
     Universal Symmetry Index of accumulated load, full scale 18%). `composite` =
     load-vs-capacity injury risk.
   - **All six outputs are 0–100**, not 0–1. Log-scaled for `m1..m3`, linear for `m4`,`m5`.
   - **Low-pass cutoff is 75 Hz, not the stale model's 50 Hz:** 50 Hz retains 97% of peak
     acceleration but only 36–75% of peak jerk (SPEC §3.6).
   - **⚠️ Claims limits (SPEC §2):** these are surrogates for *external impact loading rate*,
     never bone load (peak tibial accel vs internal tibial force: r ≈ 0). No composite injury
     score has ever passed prospective validation, and base rates make individual alerts ~90%
     false. `composite` is a monitoring/triage aid, not a prediction — UI copy must say so.
   - **🚩 Hardware flag:** ±16 g will clip on the **shank during running/jumping** (published
     resultant peaks 20–27 g; literature recommends ≥±32 g). Fine for thigh and for all
     strength training. Ingest counts saturation and suppresses `m1`/`m2` above 2.6% saturated
     samples rather than reporting a truncated peak.
   - **Sample context:** needs **1 s of trailing history** per limb, not just the ~10 newest
     samples — a single tick underestimates peak acceleration by ~2.3×. Stored as **60
     per-tick float32 summaries**, not 600 raw samples (~3 KB/device total). Raw frames are
     never buffered. Max lookback in ingest is **1 second**; everything longer is an O(1)
     recursive accumulator, and nothing in the live path touches the DB (SPEC §7.2).
   - **Calibration is AUTOMATIC** — the orientation-free design needs none and the system runs
     correctly on defaults, but every session calibrates itself anyway (SPEC §3.8, user
     decision). No trainer action and no API route: per sensor, `compute()` discards the first
     3 s of streaming and then watches for 10 s of continuous stillness, accumulating running
     sums to recover accel gain, gyro bias and noise σ. Worth taking because `m4`/`m5` are
     **inter-sensor ratios**, so gain mismatch biases them directly. Last-known-good values
     carry across sessions in `biomech:cal:{device_id}` (BACKEND_SCHEMA §4), so only a device
     with no history ever runs on defaults; the state is visible in `flags` (`uncalibrated`,
     `carried_over`, `cal_failed`). `m4`'s movement baseline is separate and self-learns from
     the first 60 s of movement.
   - **Real-time budget (measured, SPEC §7.1):** biomech adds **~22.6 ms** onset latency
     (4.2 ms filter + 1.7 ms diff + 16.7 ms tick quantisation) — the existing 50 ms jitter
     buffer dominates. Throughput **1,590 µs/tick for all 5 devices = 9.5% of one core, ~10×
     headroom**, *provided* each device's 4 limbs are batched into a single
     `scipy.signal.lfilter` call per stage. Per-limb calls cost ~3.8× more (Python call
     overhead). Trailing windows delay **release, not onset**: a new impact moves `m1` on the
     next tick.
   - **Per-device fixed-slot batching (SPEC §7.2)** — batching must not assume a fixed sensor
     count. Each device's session allocates a permanent **4-slot matrix** (one per limb in
     `LIMB_MAP`, ordered by sorted limb name so it is stable across restarts); an `active[]`
     mask excludes absent limbs from aggregation, feeding the §8 degradation ladder.
     ⚠️ **This is per-device, not the cross-device 20-slot matrix earlier revisions of this
     section specified.** Ingest runs one asyncio task per device with independently reset tick
     epochs, so there is no instant at which every device's frames are in scope; a cross-device
     batch would mean replacing that scheduler, and it would couple every device's tick to the
     slowest one. Cost is therefore **flat per device (~330 µs) and linear in total** — 344 µs
     at 1 device, 1,590 µs at 5 — and adding a device adds CPU, not latency.
     🚩 **Absent slots MUST be hold-last filled, never zero-filled:** zero-fill lets the gravity
     baseline decay to 0, so a sensor reconnecting while the athlete stands still emits a
     **9.75 m/s² phantom impact** (`m1` ≈ 85/100). Hold-last gives 0.096 m/s² — the true noise
     floor — and needs no filter re-seeding, because the baseline tracks orientation-independent
     `|g|` and stays valid even across a remount.
   - **Session state is snapshotted to Redis at 1 Hz** (`biomech:state:{device_id}`, ~200 B/device,
     TRD-approved) and restored on startup with elapsed-time decay applied, so an `ingest` restart
     does not silently reset a mid-session athlete to zero accumulated load (SPEC §7.4).
   - **Session state** resets after a gap > `SESSION_GAP_S` (300 s, **an `.env` key** — §7),
     deliberately longer than `OFFLINE_AFTER_S` so a brief dropout does not wipe dose.
   - **🚩 Unit trap:** the `ω×a` jerk term needs **ω in rad/s**; °/s makes `m2` wrong by 57.3×
     and it looks plausible rather than broken (SPEC §3.5, test 23).
   - **🚩 Peak statistic:** per-tick `p90` of the ~10 samples, then **`max` across the 1 s ring**.
     A percentile *across the ring* is silently broken — an isolated 50 m/s² impact moves it by
     0.000, so running impacts would be under-reported and cadence-dependent (SPEC §5.1).
   - **🚩 `m4`/`m5` freeze when a required sensor goes inactive.** Without the gate a single dead
     sensor drives `m5` to 100 ("severe asymmetry") within 30 s and pins `m4` at 100 — a hardware
     fault rendering as the most alarming possible finding (SPEC §5.4, §5.5).
   - **⚠️ `m4` and `m5` ship without real-data validation** — the only capture holds 19.9 s of
     movement vs their 60 s / 30 s warm-ups, so neither emits on it. Synthetic fixtures only, by
     user decision; flagged `unvalidated` in `Metrics` for all of stage 1 and surfaced via
     `/api/health`. Closing this needs one ≥10-min session with a fatigue block (SPEC §11.1).
   - **Degraded operation:** devices may stream <4 sensors; unavailable primitives emit
     `null` and the composite reweights (SPEC §8).
   - Scale factors are compile-time constants in `backend/common/scaling.py`:
     `9.81/2048` m/s² per count, `1/16.384` °/s per count (ICM-45686, ±16 g / ±2000 °/s,
     verified against `example/squats.bin`).
7. **60Hz ticker:** wall-clock driven (`asyncio` timer, drift-corrected by absolute
   scheduling). **Emits every tick regardless of input** — on missing data it
   holds the last value (flag `held=true` internally, quality reflects it). Output
   cadence is constant even if the input rate wobbles. If no packets for
   `OFFLINE_AFTER_S` (default 2s), the device's ticker suspends (device offline)
   rather than streaming stale holds.
8. **Quality:** per tick, `received_samples / expected_samples` across the device's
   mapped sensors (expected = `EXPECTED_INPUT_HZ / OUTPUT_HZ × len(LIMB_MAP)`), clamped
   to [0,1]. Scaled by the **mapped** count, not a hardcoded 4: a valid 3-sensor
   `LIMB_MAP` used to read a permanent 0.75 — a 25% data-loss warning on healthy
   hardware — while a 5+ sensor map clamped at 1.0 and hid real loss. A physically
   dead sensor still lowers quality, which is what SPEC §8 relies on.
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
| `UDP_PORT` | 5005 | device ingest. **Local dev may differ** — Docker Desktop on the dev machine wedged both 5005 and 5010 (port shows bound, nothing reaches the container); the local `.env` overrides it and real-device sessions run ingest natively on the host. See README “Gotchas”. The VPS keeps 5005. |
| `API_PORT` | 8000 | internal only (Caddy proxies) |
| `POSTGRES_*` | db/5432/mvpdash/… | host, port, db, user, password |
| `REDIS_URL` | redis://redis:6379/0 | |
| `JWT_SECRET`, `JWT_EXPIRE_HOURS` | —, 24 | |
| `SEED_USERS` | trainer:changeme | comma-sep `user:pass` pairs, seeded once |
| `EXPECTED_INPUT_HZ` | 640 | per sensor; **measured** on the real device (median inter-sample spacing 1563 us across all 4 sensors, two captures 2026-08-02). The earlier 600 was an estimate and made `quality` read ~6% low. |
| `OUTPUT_HZ` | 60 | tick + WS + DB rate |
| `LIMB_MAP` | JSON, see §3 | `(source,sensor) → limb` |
| `JITTER_BUFFER_MS` | 50 | reorder window |
| `OFFLINE_AFTER_S` | 2 | online/offline threshold |
| `RESET_OFFSET_JUMP_S` | 5 | offset jump ⇒ reboot, reset source buffers (§4 step 3) |
| `SESSION_GAP_S` | 300 | gap after which biomech resets accumulated load/baselines (biomech SPEC §7); deliberately ≫ `OFFLINE_AFTER_S` |
| `PAST_WINDOWS` | `5m,30m,2h` | **3 durations; deployment e.g. `1h,1d,3d`** |
| `FUTURE_HORIZONS` | `10m,30m,1h` | deployment e.g. `1d,3d,1w` |
| `PREDICT_INTERVAL_S` / `PREDICT_TRAIN_WINDOW` | 300 / 2h | |
| `INSIGHT_WARN_THRESHOLD` / `INSIGHT_ALERT_THRESHOLD` | 85 / 92 | composite 0–100. Raised from 70/85 after the SPEC §6.1 rescale: a measured hard interval session reads ~77, so 70 warned during ordinary hard training. At 85 acute effort alone does not fire — accumulated dose is what raises the flag |
| `INSIGHT_INTERVAL_S` / `INSIGHT_COOLDOWN_S` | 60 / 600 | |
| `METRICS_RETENTION` | 30d | hypertable retention |
| `MAX_DEVICES` | 5 | hard cap on concurrently tracked devices; a 6th while all 5 are live is dropped and counted in `ingest:stats/global:dev_dropped`, never merged into another device's stream (biomech SPEC §7.2). Raising it needs an ingest restart |
| `POSTGRES_HOST` / `POSTGRES_PORT` | db / 5432 | expanded from the `POSTGRES_*` row above, which listed no per-key defaults |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | mvpdash / mvpdash / changeme | password is a placeholder — real value only in `.env`, never committed |
| `JWT_EXPIRE_HOURS` | 24 | cookie lifetime; `JWT_SECRET` has no default on purpose (empty until stage 3) |
| `PREDICT_TRAIN_WINDOW` | 2h | history fed to `predict.fit()`; duration syntax |
| `INSIGHT_COOLDOWN_S` | 600 | per-rule re-fire suppression, so one condition cannot spam the feed |

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
  forces LF for `*.sh`, Caddyfile, `*.sql`; `*.pdf` marked binary (auto-detection
  misclassifies the example PDF as text).
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

Ingest additionally **logs an error** when datagrams are arriving but none decode
and no device exists. That state is a configuration error (wrong frame format),
not a data-quality statistic, and leaving it to a counter nobody watches cost a
whole session: a healthy device streaming a 22-byte frame into a decoder that
required 21 read exactly like "device not connected" (§3).

## 11. Open items (TBD ledger)

| Item | Where decided | Placeholder until then |
|---|---|---|
| ~~5 primitives + composite definitions, units, calibration~~ | **DECIDED** — [biomech/SPEC.md](biomech/SPEC.md) (S1-T14) | — implemented in S1-T15 |
| Asymmetry full-scale threshold (`SI_FULL_SCALE`) + reference-bound calibration | biomech SPEC §13 open items | 15%; provisional bounds |
| Prediction model + CI method | prediction session (stage 2+) | linear regression stub |
| Insight rule catalogue + thresholds | insights session (stage 2+) | 2 starter rules |
| Final UI design | **stage 3** frontend session (`mockup/`) | stage-2 crude disposable UI |
| Production window/horizon durations | config flip at deploy | test durations |
| Per-packet auth (HMAC) | post-MVP | open UDP + validation |
