# Stage 1 tasks — local real-time biomech

> Scope: real biomech model on live data, tested **locally only**. Services: redis +
> ingest + minimal api (`/debug` viewer). No TimescaleDB, no Caddy, no auth, no deploy.
> Required reading for every task: [../PLAN.md](../PLAN.md), [../TRD.md](../TRD.md)
> §§1–4,7,10, [../BACKEND_SCHEMA.md](../BACKEND_SCHEMA.md) §§2,4,5.
> Rules: never change a stable interface or config key without updating the docs;
> Windows dev machine (Docker Desktop/WSL2); Python 3.12; every task ends with its
> done-check actually run.

Task order is chronological. `⛓` = hard dependency. `∥` = may run in parallel with
the previous task.

---

## S1-T01 — Repo scaffold

**Goal:** empty repo becomes a working skeleton every later task builds on.
**Depends:** nothing.
**Files:** `.gitignore`, `.gitattributes`, `README.md`, `.env.example`, `.env`
(copy, gitignored), full empty folder tree per [../IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) repo layout.

Steps:
1. `git init` (branch `main`).
2. `.gitignore`: `.env`, `__pycache__/`, `*.pyc`, `.venv/`, `node_modules/`, `dist/`,
   `.pytest_cache/`, `caddy_data/`.
3. `.gitattributes`: `* text=auto` + `*.sh text eol=lf`, `*.sql text eol=lf`,
   `Caddyfile text eol=lf`.
4. `.env.example` with **every** key + test default from [../TRD.md](../TRD.md) §7
   (DB/JWT keys included now, unused until stage 2/3). Comment each key in one line.
5. `README.md`: one-paragraph project summary + "read docs/PLAN.md first" + quickstart
   placeholder (filled by S1-T13).
6. Initial commit.

**Done check:** `git status` clean; `.env` exists locally and is untracked;
tree matches the layout doc.

## S1-T02 — Backend package + config loader

**Goal:** installable `backend` package; all config in one typed object.
**Depends:** ⛓ S1-T01.
**Files:** `backend/pyproject.toml`, `backend/common/__init__.py`,
`backend/common/config.py`, `backend/common/durations.py`,
`backend/common/redis_keys.py`, `backend/tests/test_config.py`.

Steps:
1. `pyproject.toml` (uv-compatible): deps `fastapi`, `uvicorn[standard]` (pulls
   uvloop — note: uvloop is skipped on native Windows; inside Linux containers it
   applies), `redis>=5` (async client), `numpy`, `pydantic-settings`, `orjson`;
   dev-deps `pytest`, `pytest-asyncio`, `httpx`.
2. `durations.py`: `parse_duration("5m") -> timedelta` supporting `s|m|h|d|w`;
   `parse_duration_list("5m,30m,2h") -> list[timedelta]`; `format_duration` for UI
   labels. Raise clear `ValueError` on junk.
3. `config.py`: `Settings(BaseSettings)` reading the root `.env` — every TRD §7 key,
   with `LIMB_MAP: dict[tuple[int,int], str]` parsed from the JSON env string
   (`"0,1"` keys → `(0,1)` tuples) and `past_windows` / `future_horizons` as
   `list[timedelta]` properties. Singleton accessor `get_settings()`.
4. `redis_keys.py`: constants/format functions — channel `ticks`,
   `last_seen_dev(id)`, `last_seen_sensor(dev,src,sen)`, `INGEST_STATS = "ingest:stats"`
   (must match [../BACKEND_SCHEMA.md](../BACKEND_SCHEMA.md) §4 exactly).
5. Tests: duration parsing (incl. errors), limb-map parsing, defaults load without a
   real `.env`.

**Done check:** `uv run pytest backend/tests/test_config.py` green.

## S1-T03 — Packet decoder (`common/packet.py`)

**Goal:** vectorized UDP-payload decoder — the single source of truth for the wire
format ([../TRD.md](../TRD.md) §3).
**Depends:** ⛓ S1-T02. **∥** S1-T04.
**Files:** `backend/common/packet.py`, `backend/tests/test_packet.py`.

Steps:
1. Port from [../../example/parse_imu.py](../../example/parse_imu.py): CRC-8 table
   builder, i16/u32 LE helpers — but operate on a **list of 21-byte payloads**
   (stack into an `(n, 21)` uint8 matrix), not a file.
2. `decode(payloads: list[bytes]) -> Batch` where `Batch` is a dataclass of numpy
   arrays: `device_id, source_id, sensor_id, version, ts_us (u32), imu (n,6) int16`,
   plus counters `n_in, n_bad_len, n_bad_sync, n_bad_crc` (bad records are filtered
   out, only counted). Wrong-length payloads never raise.
3. `encode(device_id, source_id, sensor_id, ts_us, imu6) -> bytes` — the inverse
   (single record, correct CRC). Used by the simulator; keeps encode/decode in one
   file so they can't drift apart.
4. Tests: (a) round-trip encode→decode; (b) golden test — read the first ~1000
   records of `example/squats.bin` (21-byte stride), decode, compare fields against
   the same rows of `example/squats_decoded.csv`; (c) corrupt CRC/sync/length are
   counted and filtered.

**Done check:** `uv run pytest backend/tests/test_packet.py` green, incl. golden test.

## S1-T04 — Compose file (stage-1 services)

**Goal:** `docker compose up` runs redis + ingest + api locally.
**Depends:** ⛓ S1-T02. **∥** S1-T03.
**Files:** `docker-compose.yml`, `backend/Dockerfile`.

Steps:
1. `backend/Dockerfile`: python:3.12-slim, install via uv, copy `backend/`,
   no CMD (compose supplies per-service command).
2. `docker-compose.yml` services:
   - `redis`: `redis:7-alpine`, `--appendonly no`, no host port (internal) —
     but map `127.0.0.1:6379:6379` in a `debug` profile for redis-cli poking.
   - `ingest`: build backend, command `python -m ingest.main`, env from `.env`,
     ports `"${UDP_PORT}:${UDP_PORT}/udp"`, `restart: unless-stopped`.
   - `api`: command `uvicorn api.main:app --host 0.0.0.0 --port ${API_PORT}`,
     ports `127.0.0.1:${API_PORT}:${API_PORT}`, `restart: unless-stopped`.
   - `db` and `caddy` definitions added now but under `profiles: ["stage2"]`
     (inactive until stage 2).
3. Placeholder `ingest/main.py` (logs "waiting for implementation", sleeps) and
   `api/main.py` (FastAPI returning `{"status":"ok"}` at `/api/health/live`) so
   compose is green before T06+.

**Done check:** `docker compose up -d` → all three healthy;
`curl localhost:8000/api/health/live` → `{"status":"ok"}`; `docker compose --profile stage2 config` validates.

## S1-T05 — Simulator

**Goal:** realistic device traffic without hardware — the test rig for everything.
**Depends:** ⛓ S1-T03 (uses `packet.encode`/`decode`).
**Files:** `simulator/simulate.py`, `backend/tests/test_simulator.py` (import-tested).

Steps:
1. Load `example/squats.bin` once with the T03 decoder (read file → 21-byte payload
   list → `decode`). Group samples by `(source_id, sensor_id)` (4 streams), sorted
   by unwrapped ts.
2. Decimate each stream from native (~6.6kHz) to `--rate` (default 600) by stride
   `round(native_hz / rate)` (measure native from median ts delta).
3. Emit loop (asyncio): for each simulated device `d` of `--devices N` (device_id =
   `--base-id` + d, default base 30) and each of its 4 streams, send one datagram per
   sample period using **fresh running timestamps**: per-(device,source) µs counter
   advancing at the true period, with `--drift ppm` skew applied per source (so the
   two "legs" drift apart realistically) and natural u32 wraparound preserved.
   Payload = `packet.encode(...)` with the replayed IMU values (loop the file
   endlessly). Batch scheduling: sleep in ~5ms slots, send everything due.
4. Imperfection flags: `--loss P` (drop before send), `--reorder P` (hold a packet
   back 3–8 slots), `--jitter MS` (uniform extra delay), `--seed` (reproducible),
   `--target host:port` (default `127.0.0.1:5005`).
5. Print once per second: per-device sent/dropped/reordered and effective Hz.
6. Runs on the host (no Docker): `uv run python simulator/simulate.py ...`.

**Done check:** `--devices 2 --loss 5` reports ~2×4×600 pkt/s with ~5% dropped;
unit test: 100 emitted payloads decode back with correct ids/rates.

## S1-T06 — Ingest: UDP server + decode loop + stats

**Goal:** datagrams → decoded batches → per-sensor routing, with counters. The hot
path — keep the receive callback trivial.
**Depends:** ⛓ S1-T04, S1-T05 (for testing).
**Files:** `backend/ingest/udp.py`, `backend/ingest/state.py`, `backend/ingest/main.py`
(real), `backend/tests/test_ingest_udp.py`.

Steps:
1. `udp.py`: `asyncio.DatagramProtocol`; `datagram_received` does **only**
   `buf.append(data)` into a bounded `collections.deque(maxlen=32768)` (drop-oldest
   counts `buf_drop`). Create socket with `SO_RCVBUF = 4MB`; log the achieved value.
2. Drain task: every 10ms swap the deque contents into a list, `packet.decode` the
   batch, hand the `Batch` to the router.
3. `state.py`: registry `devices: dict[int, DeviceState]`;
   `DeviceState.sensors: dict[(src,sen), SensorState]`; auto-create on first sight.
   Router appends `(recv_time, ts_us, imu6)` per sensor and updates stats.
4. Stats object (per sensor): `recv`, `rate_hz` (1s sliding window), `crc_fail`,
   `bad_sync`, `late_drop` (T08), `buf_drop`; plus per-device `ticks_out` (T09).
   A 1s task logs a compact one-line summary per device.
5. `main.py`: uvloop if available, config load, start UDP + drain + stats loop;
   clean shutdown on SIGTERM.

**Done check:** simulator 2 devices → ingest logs show ~600Hz per sensor per device,
`crc_fail=0`; with `--loss 10` rates drop ~10%.

## S1-T07 — Ingest: timestamp unwrap + clock alignment

**Goal:** all samples mapped onto one server-time axis; reboots handled
([../TRD.md](../TRD.md) §4 steps 2–3).
**Depends:** ⛓ S1-T06.
**Files:** `backend/ingest/align.py`, `backend/tests/test_align.py`.

Steps:
1. Per sensor: unwrap u32 µs → i64 (`if ts < last_raw and last_raw - ts > 2**31: epoch += 2**32`).
2. Per `(device, source)` `SourceClock`: on each sample compute
   `delta = server_recv_time − unwrapped_ts_seconds`; maintain rolling **minimum**
   over a 2s deque of deltas (min = least network/queue delay) as `offset`; allow
   slow drift by re-evaluating the min each second. `server_time(sample) =
   unwrapped_ts + offset`.
3. Reboot detection: if `abs(new_delta − offset) > RESET_OFFSET_JUMP_S` (config,
   default 5s) for >10 consecutive samples → reset that source's unwrap epoch,
   clock, and jitter buffers; increment `resets` counter.
4. Pure functions/classes, no I/O → property-style unit tests: synthetic streams
   with known offset/drift/wrap/reboot recover server time within ±2ms.

**Done check:** `pytest test_align.py` green incl. wraparound and reboot cases;
live: simulator `--drift 200` runs minutes without resets or growing skew between legs.

## S1-T08 — Ingest: jitter buffer

**Goal:** in-order, deduplicated sample release despite UDP reorder
([../TRD.md](../TRD.md) §4 step 4).
**Depends:** ⛓ S1-T07.
**Files:** `backend/ingest/jitter.py`, `backend/tests/test_jitter.py`.

Steps:
1. Per sensor buffer: samples inserted keyed by unwrapped ts (use `heapq` or
   `SortedList`); duplicates (same ts) dropped.
2. `release(now) -> list[sample]`: pop everything whose mapped server time <
   `now − JITTER_BUFFER_MS`, in ts order.
3. A sample arriving with ts older than the last released one → drop, `late_drop += 1`.
4. Tests: shuffled input comes out ordered; late packet counted; buffer memory
   bounded (cap ~2× expected rate × window, drop-oldest beyond).

**Done check:** `pytest` green; live: simulator `--reorder 5` → ordered release,
`late_drop` near zero at 50ms window, rises if window set to 5ms.

## S1-T09 — Ingest: 60Hz ticker, framing, quality

**Goal:** the constant-rate heartbeat: flexible input → exactly `OUTPUT_HZ` frames
([../TRD.md](../TRD.md) §4 steps 5,7,8).
**Depends:** ⛓ S1-T08.
**Files:** `backend/ingest/ticker.py`, `backend/tests/test_ticker.py`.

Steps:
1. One ticker task per device, started on first packet. Absolute scheduling:
   `next_t = start + k/OUTPUT_HZ`, `await sleep(next_t − now)` — no cumulative drift.
2. Each tick: `release()` all four sensors → group via `LIMB_MAP` →
   `frames: dict[limb, float32[n,6]]` (samples in (prev_tick, tick], raw counts as
   float32). Missing limb ⇒ empty array.
3. Quality = `total_samples_this_tick / (4 × EXPECTED_INPUT_HZ / OUTPUT_HZ)`, clamp [0,1].
4. Hold-last: if all frames empty, emit tick flagged `held=True` (metrics repeat
   previous values downstream); if no packets for `OFFLINE_AFTER_S`, suspend ticker
   (resume + state reset on next packet).
5. Emits `TickInput(device_id, t_server, frames, quality, held)` to a callback
   (biomech in T10); `ticks_out += 1`.
6. Tests: fake clock — cadence exactness, hold behavior, suspend/resume.

**Done check:** live with simulator: per-device tick rate = 60.0 ± 0.5 over a
minute (log shows it); quality ≈ 0.95 with `--loss 5`.

## S1-T10 — Ingest: stub biomech + tick assembly

**Goal:** pipeline emits plausible full ticks end-to-end before the real model exists.
**Depends:** ⛓ S1-T09.
**Files:** `backend/ingest/biomech.py`, `backend/tests/test_biomech_stub.py`.

Steps:
1. Implement the **stable interface** ([../BACKEND_SCHEMA.md](../BACKEND_SCHEMA.md)
   §5): `compute(frames, state) -> Metrics(m1..m5, composite)`; `state` is a
   per-device dict the function may use (EMA memory etc.).
2. Stub mapping (placeholder, replaced in S1-T15 — keep *shape* realistic):
   m1..m4 = per-limb gyro RMS (order: left_thigh, left_shin, right_thigh,
   right_shin), m5 = mean accel-magnitude RMS across limbs, all EMA-smoothed
   (α≈0.2) and squashed to ~0..1 via `x/(x+k)`; composite = weighted mean
   (0.15/0.15/0.15/0.15/0.4). Empty frames → repeat previous (`held`).
3. Wire into ticker callback; attach metrics to the tick.

**Done check:** unit test: known sinusoid frames → stable expected values; live:
metrics move visibly during replayed squats and idle near zero between reps.

## S1-T11 — Ingest: Redis publish + service wiring complete

**Goal:** ingest fully implements its side of the Redis contract
([../BACKEND_SCHEMA.md](../BACKEND_SCHEMA.md) §§2,4).
**Depends:** ⛓ S1-T10.
**Files:** `backend/ingest/publish.py`, final `backend/ingest/main.py`.

Steps:
1. Tick → JSON (orjson) exactly per §2 (`type,t,dev,m,c,q`) → `PUBLISH ticks`.
   Redis unavailable ⇒ drop + count + retry connect with backoff (never crash,
   never block the ticker — fire-and-forget task with bounded pending set).
2. 1s loop: `SET last_seen:dev:{id}` / `last_seen:sensor:...` (unix ms) and rewrite
   `ingest:stats` hash: per-sensor `rate_hz` + all counters, per-device `ticks_out`,
   process uptime.
3. `main.py` final wiring + graceful shutdown; structured startup log (config echo
   minus secrets).

**Done check:** `docker compose exec redis redis-cli subscribe ticks` shows
~60 valid JSON msg/s per device; `HGETALL ingest:stats` sane; stop simulator →
ticks stop ≤2s, `last_seen` ages.

## S1-T12 — Minimal api: WS fan-out + `/debug` viewer

**Goal:** eyes on the data — the page used to judge the biomech model.
**Depends:** ⛓ S1-T11 (contract; can scaffold ∥ against fake published ticks).
**Files:** `backend/api/main.py`, `backend/api/ws.py`, `backend/api/debug.html`,
`backend/tests/test_ws.py`.

Steps:
1. `ws.py` hub: one Redis `ticks` subscription task → fan out to clients; per-client
   `asyncio.Queue(maxsize=120)`, on full drop-oldest + `ws_dropped` counter.
   `/ws/live?devices=30,31` (omit = all). No auth (stage 3).
2. Status watcher: 1s scan of `last_seen:dev:*` → on online/offline transition
   (threshold `OFFLINE_AFTER_S`) push `status` message to all clients.
3. `GET /api/health`: `{status, redis, ingest: <ingest:stats contents>, api:
   {ws_clients, ws_dropped}}` (no db yet). Keep `/api/health/live`.
4. `GET /debug`: serve `debug.html` — one self-contained page (uPlot from CDN is
   fine for now): connects WS, one panel per device: 6 rolling 30s charts
   (composite large, m1..m5 small), quality %, held indicator, online badge from
   status messages, per-sensor Hz polled from `/api/health` every 2s.
5. `test_ws.py`: httpx/websockets client asserts ≥55 msg/s/device for 5s and
   correct tick schema.

**Done check:** browser `localhost:8000/debug` with 5 simulated devices: all panels
live and smooth; kill one sim device → badge offline ≤2s; WS test green.

## S1-T13 — Stage-1 integration validation + README quickstart

**Goal:** prove the plumbing under stress before the real model lands; document how
to run everything.
**Depends:** ⛓ S1-T12.
**Files:** `backend/tests/test_e2e_stage1.py` (or `scripts/validate_stage1.py`),
README update.

Steps — run and record the matrix (5 devices unless noted):
1. Clean run 10 min: tick rate 60±0.5/device, `crc_fail=0`, `late_drop≈0`,
   memory flat (`docker stats`).
2. `--loss 10 --reorder 5 --jitter 20`: quality ≈0.90, charts still smooth,
   no counter runaway.
3. `--drift 500`: no resets, legs stay paired.
4. Kill/restart simulator devices repeatedly: offline/online transitions correct,
   no stale ticks while offline.
5. Kill/restart `api`: ingest unaffected (counters keep advancing) — proves the
   isolation goal. Kill/restart `redis`: ingest reconnects with backoff, no crash.
6. Real wearables (if available): point at dev machine LAN IP `:5005`, verify
   device auto-appears; note Windows Defender Firewall must allow inbound UDP 5005
   (document the `netsh`/UI step in README).
7. README quickstart: compose up → simulator → `/debug`, + firewall note.

**Done check:** all six scenarios pass; results pasted into the task log/commit
message; README quickstart verified from scratch.

## S1-T14 — Biomech planning session  ⚑ USER REQUIRED

**Goal:** turn the user's biomech knowledge into `docs/biomech/SPEC.md`.
**Depends:** ⛓ S1-T13 (pipeline proven, so the spec discussion is concrete).

Steps:
1. User drops notes/papers/pseudocode into `docs/biomech/` (any format).
2. Dedicated planning session produces `docs/biomech/SPEC.md`: definitions of the 5
   primitives + composite; raw-count scaling (accel LSB→g, gyro LSB→deg/s — from
   IMU datasheet/config); filters (type, cutoff, causal only — no lookahead);
   per-limb vs paired-leg inputs; required sample context (does 60Hz output need
   more than the ~10 newest samples? if so, ring-buffer length); calibration
   (standing still? per-session zeroing?); output ranges/units; display names for
   `lib/metrics.ts`; validation plan (what should the metrics read during the
   squats replay?).
3. Update TRD §4 step 6 and BACKEND_SCHEMA §5 if the interface needs extending
   (e.g. `state` gains calibration fields) — doc updates are part of this task.

**Done check:** SPEC.md reviewed and approved by the user in that session.

## S1-T15 — Real biomech implementation + sign-off  ⚑ USER SIGN-OFF

**Goal:** replace the stub with the real model; stage-1 exit.
**Depends:** ⛓ S1-T14.
**Files:** `backend/ingest/biomech.py` (rewrite), `backend/tests/test_biomech.py`,
`backend/common/scaling.py` (if SPEC defines unit conversion).

Steps:
1. Implement SPEC.md behind the unchanged `compute()` interface; vectorized numpy;
   any needed history via `state` ring buffers.
2. Unit tests from SPEC's validation plan (golden values on synthetic + squats data).
3. Perf guard: with 5 devices live, ingest CPU well under one core; add a
   micro-benchmark (`pytest -k bench`, target: single `compute()` call ≪ 16ms/OUTPUT_HZ headroom, e.g. <1ms).
4. Iterate with the user on `/debug` (simulator + real wearables on LAN) until happy.
5. Update `docs/biomech/SPEC.md` with any tuning decisions made while iterating.

**Done check (stage-1 exit):** user signs off on real-time metric quality; S1-T13
matrix re-run green with the real model.
