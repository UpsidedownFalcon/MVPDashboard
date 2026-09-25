# Project context (MVPDashboard)

## Project Context
Refreshed 2026-09-25 on `csv-summary` (first written 2026-09-23). Code is ground truth: when this
file and the code disagree, fix this file. Loaded into every session via the root CLAUDE.md ->
AGENTS.md import; the plans of record sit beside it in agent-docs/ (README.md: reading order).

### What it is
HIPPOS injury-risk dashboard: wearable leg IMUs stream 22-byte UDP datagrams at ~640 Hz per
sensor; a Python biomech pipeline emits five primitives (m1..m5) plus a composite at 60 Hz per
device; a React UI shows live charts, windows, OLS forecasts and advice cards (Adopt/Override).
Wording is military ("soldier", "PTI cue", "commander"). Docs: read docs/PLAN.md first, then
TRD.md, BACKEND_SCHEMA.md, biomech/SPEC.md, UIUX.md, ANALYTICS.md and tasks/STAGE*.md.

### State (2026-09-25)
- Stages 1-3 shipped 2026-08-03; stage 4 (demo mode, five synthetic "demo-" soldiers, military
  copy, plain ASCII) 2026-09-12 at 95f1372; unilateral sleeves 2026-09-23 at c03a714.
- Sleeve storage CS1 (page /storage: CONFIG.TXT editor + verified USB log transfer, decisions A-N)
  built 2026-09-23, merged to main; plan of record agent-docs/02 (real-drive checklist STILL TO
  RUN). CS2 (CSV + meta.json + summary per log in a Web Worker, card "CSV and summary", decisions
  O-V, no backend change) built 2026-09-25 on `csv-summary`; plan of record agent-docs/03.
- Open: S3-T08 acceptance run (docs/ACCEPTANCE.md does not exist yet), S3-T09 hardening. No CI.
  This checkout HAS a root .env (JWT_SECRET set; lacks the three UNILATERAL_* keys, which have
  config defaults) and a .venv (uv-managed CPython 3.12.13).

### Stack and layout
- backend/ (uv workspace member, flat packages): common/ (config, packet codec, scaling, redis
  keys), ingest/ (UDP -> align -> jitter -> 60 Hz ticker -> biomech -> Redis), api/ (FastAPI
  routes, WS hub, DB COPY writer, predict + insight jobs; routes/config.py serves udp-target),
  migrations/ (raw SQL 001..006, custom runner), tests/ (pytest, asyncio_mode=auto).
- frontend/ (React 18, TS 5.6, Vite 5, Vitest 3, uPlot for 60 Hz live, ECharts for history).
  src/lib/config.ts = UI constants; src/lib/demo/ = the always-on synthetic layer; src/lib/storage/
  = the sleeve USB layer (pure modules + fsa.ts adapter; CS2: convert/ = pure decoder, stats sink,
  summary, pipeline, protocol, pending scan; convertQueue.ts = one worker, FIFO), src/workers/
  convert.worker.ts, src/pages/Storage.tsx, components/storage/* (ConversionPanel.tsx = card 5),
  fixtures/convert/ (8 synthetic BINs + goldens, also for LOG_0010.head64), e2e/ (dev dry run).
- scripts/kneesleeve/ = bin2csv.py + sensor_stats.py vendored verbatim 2026-09-25 (never edit);
  scripts/storage_goldens.py regenerates the goldens with them; scripts/validate_stage1.py is a
  17-20 min matrix that restarts containers (not read-only). docker-compose.yml: redis, ingest, api,
  db (timescale pg17), caddy (serves the frontend); `debug` profile exposes redis 6379 + pg 5432.

### Build, test, run (Windows 11, PowerShell; Git Bash available)
- Toolchain: uv 0.12.1 at C:\Users\bhavy\.local\bin\uv.exe (managed CPython 3.12.13); Node 24 /
  npm 11 in C:\Program Files\nodejs; Docker 29 with compose v5. Bare `python` on PATH is 3.14,
  so always use `uv run`. No caddy or psql on the host.
- `uv sync --dev`; `uv run pytest backend/tests/` (DB tests need `docker compose --profile debug
  up -d` first or they self-skip; test_ws needs redis). 361 passed 2026-09-23; test_biomech's
  3 ms bench guard fails on a busy machine (biomech unchanged): re-run idle before trusting a red.
- `cd frontend; npx tsc -b` (the lint gate; no ESLint); `npm test` (37 files, 561 tests on
  2026-09-25); `npm run build` (tsc -b + vite, incl. the worker chunk). Goldens: `uv run --with
  matplotlib python scripts/storage_goldens.py` from the repo root must leave `git status` clean.
- Conversion dry run (no sleeve): `npm run dev`, open http://localhost:5173/e2e/convert.html, pick a
  LOG_NNNN.BIN; the real worker converts it (nothing leaves the PC) and the page prints time, CSV
  sha256 (compare with bin2csv.py) and summary. No Chrome on this PC: the 2026-09-25 run used Edge.
- `docker compose up -d --build`; simulator: `uv run python simulator/simulate.py --devices 5
  --target 127.0.0.1:5005` (5010 default = Docker Desktop workaround; `--sleeves N` adds sleeves).
- deploy/deploy.sh rebuilds PRODUCTION over ssh with no confirmation; provision.sh resets ufw and
  hardens sshd. Never run either, or the simulator against prod (IDs >= 100 only, bounded
  --duration), without explicit user confirmation. Procedure: deploy/deploy.md.

### Config tiers (where tunables live)
- Wiring and operational tunables: root .env only, documented in .env.example, read by backend/
  common/config.py. Duration syntax <int><s|m|h|d|w>. INSIGHT_HOLD_S > COOLDOWN_S. UDP_PUBLIC_IP:
  IPv4 the sleeves stream to; blank = api resolves DOMAIN (60 s cache); set it behind a proxy/CDN.
- Model constants are file-local (backend/ingest/biomech.py header, backend/common/scaling.py; api/
  jobs/predict.py restates dose constants, sync-tested). Never in .env: metrics stay comparable.
- Frontend: src/lib/config.ts (incl. STORAGE_*; CS2 added STORAGE_NOISE_F_CUT_HZ 20, STORAGE_GAP_US
  1000, STORAGE_TS_OUTLIER_US 1e6, STORAGE_CSV_WRITE_CHUNK_BYTES 2 MiB; the first three mirror
  sensor_stats.py defaults and change only with the goldens); copy tables in src/lib/metrics.ts,
  rig.ts and storage/copy.ts (all walked by text.test.ts). Firmware on-card format facts are named
  constants in lib/storage/{binFormat,configSchema}.ts and convert/scaled.ts, never tunables.

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
- warming_up and degraded_sensors must never look alike; the calibration badge is never driven
  by warming_up. Stable interfaces and config keys change only with the doc update in the same
  task. Every task ends with its done-check actually run; report failures verbatim.

### Two wearable kinds and the rig model (as built 2026-09-23; common/kinds.py; plan 01)
- **Bilateral unit**: sync 0xA5, two leg MCUs, four IMUs, fixed +-16 g / +-2000 dps; source_id =
  leg MCU (0 left, 1 right); LIMB_MAP default (0,1) left_shin, (0,2) left_thigh, (1,1)
  right_thigh, (1,2) right_shin.
- **Unilateral knee sleeve**: sync 0xA6, ONE MCU on ONE leg with two IMUs, sensor 1 = thigh and
  2 = shin on every source, configurable full-scale (defaults +-32 g / +-4000 dps) that the
  datagram does NOT carry. Firmware repo (ARCHITECTURE.md D13/D35, src/app_config.h):
  `C:\Users\bhavy\GitHub_HXSKL\012_demo-prototypes\003_sep26-NYKnicks-KneeSleeve\firmware\NYKnicksDataLogger`
- **Unit** = one sleeve, wire identity (0xA6, device_id, source_id), id `u<dev>-<src>`. **Rig** =
  what everything downstream is keyed by: a bilateral rig id is the device byte ("30"); an unpaired
  sleeve is its own rig; paired sleeves share the host's rig id and the joiner's own row is hidden
  while paired. In a sleeve rig each unit sits on a VIRTUAL source (left or side-less -> 0, right
  -> 1), so a paired rig presents (0,1),(0,2),(1,1),(1,2) like a bilateral unit. A side-less sleeve
  streams bare segments ("thigh","shin"), neither side to `biomech.limb_role()`; `compute()` takes
  expected_limbs and limb_scale; degraded_sensors = fewer limbs than the rig; one_leg nulls m5.
- Pairing, side and full-scale are dashboard-owned: table `sleeve_units` (005), `/api/units`,
  mirrored to Redis `unit:cfg:{id}` (no TTL), announced on channel `unit_cfg` (the ONLY api ->
  ingest direction in the Redis contract); any change hard-resets that rig's biomech session.
  Since 2026-09-23 (decision H) side is seeded from wire source_id (0 left, 1 right) at registration
  and by `UnitConfig.default` (both MUST agree); NULL = operator cleared; 006 backfilled old rows.

### Sleeve on-card storage "HIPPOSDATA" (fw 1.2.0 source verified 2026-09-23; fielded may be 1.1.0)
- Drive = the raw FAT32 SD card over TinyUSB MSC (prod build only), label "HipposData"; logging AND
  UDP streaming stop while mounted; the host has full read/write. Host "Eject" does NOT end the
  session: only unplug (2 s debounce) -> firmware remounts, re-reads CONFIG.TXT, starts a new
  session (no reboot, no change detection, never formats). UI must say "eject, then unplug".
- CONFIG.TXT: `key=value` per line, `#`/`;` full-line comments only, keys case-sensitive, last
  duplicate wins, fgets(160) so 159+ byte lines split, CRLF, no BOM. Traps: inline comments are NOT
  stripped; integers parse base-0 (leading 0 = octal); empty wifi_ssid keeps the compiled default;
  missing keys get defaults, bad values fall back (nothing bricks); ranges in configSchema.ts.
- LOG_NNNN.BIN (log_format.h, frozen, format_version 1): 512 B header (magic "NYKS", fw, ids, odr,
  scales, session_id, CRC32 at 508) then 4096 B blocks (32 B header: magic 0xB10C, type IMU /
  TIME_SYNC / SESSION_END, sensor 1 thigh 2 shin, seq, base_ts_us, count <= 290, flags, CRC32 at
  20; 14 B samples); offsets in lib/storage/binFormat.ts. NNNN is a persisted NVS counter. 1.1.0
  files are 512 MiB preallocated (0xFF/0x00 tail if power-cut); 1.2.0 appends, rotates at 2 GiB.
- LOG_NNNN.TXT: diagnostics (diag_log_enabled=1), same NNNN as the session's first BIN, not rotated.
- bin2csv.py (numpy) and sensor_stats.py (pandas) are vendored in scripts/kneesleeve/ (its README
  names their origin) and ported in CS2 (lib/storage/convert/): bin2csv matches log_format.h byte
  for byte (512 MiB BIN -> 2.27 GB CSV + .meta.json sidecar); sensor_stats reads CSV + sidecar, NOT
  the .TXT; the port reproduces the CSV byte for byte and the summary's figures (two streaming
  passes, decision T, bounded memory).
- Browser reach: File System Access API, Chrome/Edge/Opera 86+ only, secure context (https://DOMAIN
  or localhost) + a user gesture; Chrome 122+ persists the grant; no drive-root blocklist entry;
  `<name>.crswap` sits beside a target until close() swaps it in; handles cross postMessage.
- Engine invariants (lib/storage/transfer.ts): only `LOG_\d{4}.(BIN|TXT)` is ever listed, copied or
  deleted (CONFIG.TXT cannot match); delete per file ONLY after the LOCAL copy re-reads equal
  (length, CRC32, block scan, bad blocks re-read from the card), never after an abort or with "Keep
  copies". Editor patches value spans only; no BOM, no inline comment. Conversion only reads raw/,
  never deletes on the PC; outputs land on close() (never partial; an empty placeholder can remain
  after a failure, rewritten by Retry / "Convert missing"); a raw file counts as converted only when
  all three outputs exist and are not empty; only a verified copy queues by itself, never the scan.

### Known doc/code drift (2026-09-23)
m4 warm-up is 120 s in SPEC and code but 60 s in README, TRD, BACKEND_SCHEMA, UIUX, ANALYTICS and
comments biomech.py:23,1473 + CalibrationBadge.tsx:6; UIUX contradicts itself on hiding offline
devices (code keeps them visible); metrics.ts:63 comment inverts the m5 sign. Firmware repo (not
ours, report only): README s1.4 and FLASHING.md s3 show inline comments the parser does not strip;
the generated "Leave wifi_ssid empty to keep the radio off" comment is wrong (empty keeps the
compiled default); D33 says INQUIRY rev 1.1 (code 1.2); D17 says 512 MB rotation (D38: 2 GiB).
