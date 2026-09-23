# CLAUDE.md

## Project Context
Discovered 2026-09-23 on branch `unilateral-devices`. Code is ground truth: when this
section and the code disagree, fix this section.

### What it is
HIPPOS injury-risk dashboard. Wearable leg IMUs stream 22-byte UDP datagrams at ~640 Hz
per sensor; a Python biomech pipeline emits five primitives (m1..m5) plus a composite at
60 Hz per device; a React UI shows live charts, rolling windows, OLS forecasts and
rules-based advice cards with Adopt/Override. User-facing wording is military ("soldier",
"PTI cue", "commander"). Read docs/PLAN.md first, then docs/TRD.md, docs/BACKEND_SCHEMA.md,
docs/biomech/SPEC.md, docs/UIUX.md, docs/ANALYTICS.md and docs/tasks/STAGE{1..4}.md.

### State (2026-09-23)
- Stages 1-3 shipped 2026-08-03; stage 4 (frontend demo mode with five unmarked synthetic
  "demo-" soldiers, military copy, plain-ASCII punctuation) shipped 2026-09-12 at 95f1372.
- Open: S3-T08 acceptance run (docs/ACCEPTANCE.md does not exist yet), S3-T09 hardening.
- No CI. This checkout has no root .env and no .venv (copy .env.example; set JWT_SECRET).

### Stack and layout
- backend/ (uv workspace member, flat packages): common/ (config, packet codec, scaling,
  redis keys), ingest/ (UDP -> align -> jitter -> 60 Hz ticker -> biomech -> Redis),
  api/ (FastAPI routes, WS hub, DB COPY writer, predict + insight jobs), migrations/
  (raw SQL 001..004, custom runner), tests/ (pytest, asyncio_mode=auto).
- frontend/ (React 18, TS 5.6, Vite 5, Vitest 3, uPlot for 60 Hz live, ECharts for history).
  src/lib/config.ts holds UI constants; src/lib/demo/ is the always-on synthetic layer.
- docker-compose.yml: redis, ingest, api, db (timescale pg17), caddy (serves frontend
  image). `debug` profile exposes redis 6379 and postgres 5432 on 127.0.0.1.
- simulator/simulate.py replays example/squats.bin as UDP devices; scripts/validate_stage1.py
  is a 17-20 min matrix that restarts containers (not read-only).

### Build, test, run (Windows 11, PowerShell; Git Bash available)
- Toolchain: uv 0.12.1 at C:\Users\bhavy\.local\bin\uv.exe (managed CPython 3.12.13);
  Node 24 / npm 11 in C:\Program Files\nodejs; Docker 29 with compose v5. Bare `python`
  on PATH is 3.14, so always use `uv run`. No caddy or psql on the host.
- `uv sync --dev`; `uv run pytest backend/tests/` (DB tests need
  `docker compose --profile debug up -d` first or they self-skip; test_ws needs redis).
- `cd frontend; npm run build` (tsc -b is the lint gate; no ESLint); `npm test`.
- `docker compose up -d --build`; simulator: `uv run python simulator/simulate.py
  --devices 5 --target 127.0.0.1:5005` (its default 5010 is a Docker Desktop workaround).
- deploy/deploy.sh rebuilds PRODUCTION over ssh with no confirmation; provision.sh resets
  ufw and hardens sshd. Never run either, or the simulator against prod (IDs >= 100 only,
  bounded --duration), without explicit user confirmation.

### Config tiers (where tunables live)
- Wiring and operational tunables: root .env only, documented in .env.example, read by
  backend/common/config.py. Duration syntax <int><s|m|h|d|w>. INSIGHT_HOLD_S > COOLDOWN_S.
- Model constants: file-local on purpose (backend/ingest/biomech.py header block,
  backend/common/scaling.py; backend/api/jobs/predict.py restates dose constants with a
  sync test). Do not move them to .env: stored metrics must stay comparable.
- Frontend: src/lib/config.ts; risk bands and copy tables in src/lib/metrics.ts.

### Frozen interfaces (change only with decoders, clients and docs in the same change-set)
- 22-byte UDP datagram: backend/common/packet.py is the single source of truth (TRD s3).
- Tick JSON on Redis `ticks` == WS payload (BACKEND_SCHEMA s2); flag vocabulary (SPEC s10).
- REST shapes (BACKEND_SCHEMA s3), Redis key names (s4), DB schema (numbered migrations),
  biomech snapshot version (currently 4), predict.fit() signature, insights.RULES, WS 4401.
- Scale factors in scaling.py are compile-time constants by user decision.

### Project hard rules (from the docs; binding)
- SPEC s1: no orientation estimation, movement-agnostic, causal only, 0-100 units.
- Absent limb slots are hold-last filled, never zero-filled. Null renders grey, never 0.
- No medical claims, never "predicts injury", never a directional L/R claim. m5 copy is
  magnitude plus a neutral side label. m5 is signed: + = left-dominant, - = right.
- UI copy: "soldier" never "athlete"; no em/en dash, ellipsis or middle dot (text.test.ts).
  Window and horizon labels come from the API, never hardcoded. Never dual-axis charts.
- warming_up and degraded_sensors must never look alike; the calibration badge is never
  driven by warming_up.
- Stable interfaces and config keys change only with the doc update in the same task.
  Every task ends with its done-check actually run; report failures verbatim.

### Two wearable kinds and the rig model (as built 2026-09-23)
`common/kinds.py` is the vocabulary; `PLAN_unilateral_devices.md` is the plan of record.
- **Bilateral unit**: sync 0xA5, two leg MCUs, four IMUs, fixed +-16 g / +-2000 dps.
  source_id = leg MCU (0 left, 1 right); LIMB_MAP default (0,1) left_shin, (0,2) left_thigh,
  (1,1) right_thigh, (1,2) right_shin.
- **Unilateral knee sleeve**: sync 0xA6, ONE MCU on ONE leg with two IMUs, sensor 1 = thigh
  and 2 = shin on every source, configurable full-scale (defaults +-32 g / +-4000 dps) that
  the datagram does NOT carry. Firmware repo:
  `C:\Users\bhavy\GitHub_HXSKL\012_demo-prototypes\003_sep26-NYKnicks-KneeSleeve\firmware\NYKnicksDataLogger`
  (app_config.h:121-133,170-174; acquisition.c append_sample_; ARCHITECTURE.md D13/D35).
- **Unit** = one sleeve, wire identity (0xA6, device_id, source_id), id `u<dev>-<src>`.
  **Rig** = what everything downstream is keyed by. Bilateral rig id is the device byte
  ("30"); an unpaired sleeve is its own rig; two sleeves paired in the dashboard share the
  host's rig id and the joiner's own row is hidden while paired.
- Inside a sleeve rig each unit sits on a VIRTUAL source (left or side-less -> 0, right -> 1),
  so a paired rig presents (0,1),(0,2),(1,1),(1,2) like a bilateral unit and per-source
  battery, sensor stats and last_seen keys work unchanged.
- A side-less sleeve streams bare segments ("thigh","shin"); `biomech.limb_role()`
  (biomech.py:385) reads side and segment from the limb NAME, so those are neither side.
- `compute()` takes `expected_limbs` (rig size, not a constant) and `limb_scale`
  (per-limb counts-to-SI correction). `degraded_sensors` = fewer limbs than the rig should
  have; new flag `one_leg` = only one leg instrumented, which nulls m5 honestly.
- Pairing, side and full-scale are dashboard-owned: table `sleeve_units` (migration 005),
  endpoints under `/api/units`, mirrored to Redis `unit:cfg:{id}` (no TTL) and announced on
  channel `unit_cfg`. This is the ONLY api -> ingest direction in the Redis contract.
  Any change to them hard-resets that rig's biomech session.
- Simulator: `--sleeves N` emits sleeve units, rescaling the capture's counts to the
  sleeve's full-scale. Pass `--target 127.0.0.1:5005` (its default 5010 is a dev workaround).

### Known doc/code drift (2026-09-23)
README lags stage 4; m4 warm-up is 120 s in SPEC and code but 60 s in README, TRD,
BACKEND_SCHEMA and UIUX; UIUX contradicts itself on hiding offline devices; IMPLEMENTATION_PLAN
omits migration 004 and stage 4; frontend metrics.ts:63 comment inverts the m5 sign.
