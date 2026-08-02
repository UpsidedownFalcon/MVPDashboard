# Session Plan Snapshot — Injury-Risk Prediction Dashboard MVP

> Snapshot of the approved planning-session output (2026-08-02). This is the anchor document
> for all other Claude Code sessions. Detailed specs live in the sibling docs:
> [PRD.md](PRD.md) · [TRD.md](TRD.md) · [UIUX.md](UIUX.md) · [APPFLOW.md](APPFLOW.md) ·
> [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) · [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)

## Context

A trainer-facing web dashboard that predicts when trainees are approaching injury.
1–5 wearable devices stream raw IMU data over UDP to a public server; a Python
biomechanical pipeline converts it to 60Hz metric streams (5 primitives + 1 composite
per device — algorithm TBD, stubbed for now); the dashboard shows live charts,
historical rolling windows, regression-based future predictions of the composite,
and rules-based insights, behind a login with preset users.
Everything favors simplicity, stubs with stable interfaces, and configuration over code.

**Build order (revised 2026-08-02 — user-mandated stages):**
1. **Stage 1 — local only:** real biomech model (5 primitives + 1 composite) on live
   real-time data; tested locally with simulator + LAN wearables + a minimal debug
   viewer. No deployment, no DB, no auth.
2. **Stage 2 — deploy + intelligence:** VPS deployment, TimescaleDB persistence,
   historical windows, future predictions, actionable insights — with a crude,
   disposable AI-generated frontend. **Fully public temporarily (accepted risk).**
3. **Stage 3 — product:** properly designed frontend (from user's mockup), login
   system, and everything else.

## Confirmed facts (from `example/` + user answers — SET IN STONE)

- **Packet format:** 1 UDP datagram = one **22-byte** record — the 21-byte SD-log record
  in `example/parse_imu.py` **plus a trailing `soc` byte the log omits** (verified by
  live capture 2026-08-02; assuming 21 made ingest reject every real packet):
  `device_id u8 | source_id u8 | wire[19] | soc u8` where
  wire = `sync 0xA5 | header(sensor_id bits0-1, version bits2-7) | timestamp_us u32 LE
  (wraps ~71.6 min) | ax,ay,az,gx,gy,gz i16 LE raw | crc8 (poly 0x07, init 0x00, over wire[1..17])`.
- **Rate:** UDP streams ~600Hz per sensor (decimated from the ~6.6kHz SD-log rate).
  4 sensors/device → ~2,400 pkt/s/device, ~12k pkt/s at 5 devices. Ingest is written
  rate-agnostic (measures actual rate) but sized for bursts.
- **Sensor topology:** per device, `source_id` ∈ {0,1} × `sensor_id` ∈ {1,2}.
  Default limb map (config-overridable): (0,1)=left shin, (0,2)=left thigh,
  (1,1)=right thigh, (1,2)=right shin. Timestamps are monotonic per source (leg),
  NOT across sources; UDP arrival order untrusted; CRC must be checked.
- **Biomech contract:** flexible input rate in; **constant 60Hz out** (wall-clock-driven
  ticker; hold-last/interpolate over gaps) — the dashboard renders at 60Hz.
- **Hosting:** new Hetzner-class VPS; user's existing domain pointed at it via Cloudflare
  **DNS-only records** (Cloudflare cannot proxy UDP; DNS-only also lets Caddy get
  Let's Encrypt certs directly). Devices target the raw `VPS_IP:5005/udp`.
- **Retention:** 60Hz data ~30 days (configurable), aggregates forever. Raw 600Hz never stored.

## Architecture (SET IN STONE)

Five containers via Docker Compose (identical locally on Docker Desktop and on the VPS):

```
Wearables ──UDP :5005──▶ ┌─ ingest (Python #1) ───────────────────────────┐
                         │ asyncio UDP → CRC check → decode → per-       │
                         │ (device,source) jitter buffer (reorder) →     │
                         │ leg alignment → biomech STUB (numpy) →        │
                         │ 60Hz wall-clock ticker → publish tick         │
                         └───────────────┬────────────────────────────────┘
                                         ▼ Redis pub/sub (+ last_seen keys)
                         ┌─ api (Python #2, FastAPI) ─────────────────────┐
                         │ sub → WS fan-out (bounded, drop-oldest)       │
                         │ sub → batched writer → TimescaleDB (1s COPY)  │
                         │ REST: auth, devices(+rename), windows,        │
                         │ forecasts, insights, health                   │
                         │ asyncio jobs: predict STUB + insight rules    │
                         └───────┬───────────────────────▲───────────────┘
                                 ▼                       │
                         [ TimescaleDB ]          [ Caddy :443 ] ─▶ browsers
                         (internal only)          (TLS + React build)
```

**Why two Python services + Redis (stall-risk isolation):** the `ingest` process does
*nothing* but UDP→biomech→publish (no DB, no HTTP — almost nothing can stall it);
the `api` process can restart, block, or die without ingest losing a packet.
Every internal stage also has bounded drop-oldest buffers with drop counters exposed
via health endpoints — a slow DB costs history rows, never live latency.

**Storage:** rolling windows and forecasts are NOT separate databases — one TimescaleDB
(= Postgres) holds the 60Hz `metrics` hypertable, rolling windows as **continuous
aggregates**, forecasts in their own `forecasts` table, plus `users`, `devices`, `insights`.
Not extra columns on the realtime table (different cadence/keys/retention would bloat it).

**Single-place config:** one `.env` at repo root wires everything (ports, credentials,
limb map, `PAST_WINDOWS=5m,30m,2h`, `FUTURE_HORIZONS=10m,30m,1h`, etc.). Duration lists
are set to minutes/hours for testing, flipped to days for deployment — zero code changes.

**Device naming:** devices auto-register on first packet; `display_name` defaults to the
device ID, renameable from the frontend (trainer sets wearer's name).

## Build phases (see IMPLEMENTATION_PLAN.md for the master task index)

Work is decomposed into individually assignable tasks with IDs (S1-T01 … S3-T09),
each with dependencies, files, step-by-step instructions, and a done-check:
- **Stage 1** ([tasks/STAGE1.md](tasks/STAGE1.md)): scaffold → packet decoder →
  simulator → ingest pipeline (UDP, alignment, jitter, 60Hz ticker) with stub
  metrics → `/debug` viewer → validation matrix → **biomech planning session**
  (`docs/biomech/SPEC.md`) → real biomech + sign-off.
- **Stage 2** ([tasks/STAGE2.md](tasks/STAGE2.md)): TimescaleDB + migrations →
  writer → REST (windows/recent/devices) → forecast + insight jobs → crude public
  dashboard → Caddy → VPS provision + deploy + WAN validation.
- **Stage 3** ([tasks/STAGE3.md](tasks/STAGE3.md)): design session (`mockup/`) →
  product frontend → auth backend + login UI → cutover/polish → F1–F10 acceptance
  run → hardening backlog.

**To brief an agent on a task:** use the protocol in
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) §"How to brief an agent" — required
reading order, no-assumption rule, and always run the task's done-check.

Stable interfaces so real algorithms drop in later with no rework:
`biomech.compute(aligned_frames) -> 6 floats @60Hz` and
`predict.fit(history_df) -> {horizon: (pred, ci)}`.

## To be decided later (separate planning sessions)

1. **Biomech algorithm** (5 primitives + composite definitions, calibration, input
   context length) — drop notes into `docs/biomech/`, dedicated session **early in
   stage 1** (it is the point of stage 1).
2. **Prediction/regression spec** — linear-regression stub in stage 2.
3. **Insight rules** — starter rules in stage 2 (threshold / trend slope); refine later.
4. **Frontend design** — user has rough React mockup; drop into `mockup/`, dedicated
   session at **stage 3** against [UIUX.md](UIUX.md).
5. Preset user list (stage 3); production window durations; subdomain choice; VPS
   provider signup (stage 2).
