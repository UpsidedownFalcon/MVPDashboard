# Project context (MVPDashboard)

## Project Context
Discovered 2026-09-23 on branch `unilateral-devices`; refreshed 2026-09-23 on `MSD-management`.
Code is ground truth: when this file and the code disagree, fix this file. Loaded
into every session through the root CLAUDE.md -> AGENTS.md import; the plans of record
sit next to it in agent-docs/ (see agent-docs/README.md for the reading order).

### What it is
HIPPOS injury-risk dashboard. Wearable leg IMUs stream 22-byte UDP datagrams at ~640 Hz
per sensor; a Python biomech pipeline emits five primitives (m1..m5) plus a composite at
60 Hz per device; a React UI shows live charts, windows, OLS forecasts and advice cards with
Adopt/Override. Wording is military ("soldier", "PTI cue", "commander"). Read docs/PLAN.md,
then docs/TRD.md, BACKEND_SCHEMA.md, biomech/SPEC.md, UIUX.md, ANALYTICS.md, tasks/STAGE*.md.

### State (2026-09-23)
- Stages 1-3 shipped 2026-08-03; stage 4 (demo mode with five synthetic "demo-" soldiers,
  military copy, plain ASCII) 2026-09-12 at 95f1372; unilateral sleeves 2026-09-23 at c03a714.
- Sleeve storage change-set 1 (page /storage: CONFIG.TXT editor + verified USB log transfer,
  decisions A-N) built 2026-09-23 on `MSD-management`; plan of record agent-docs/02_PLAN_msd_management.md
  (As-built holds deviations and the real-drive manual checklist, STILL TO RUN). Change-set 2
  (CSV + summary in a Web Worker) is planned in its section 5, not built.
- Open: S3-T08 acceptance run (docs/ACCEPTANCE.md does not exist yet), S3-T09 hardening.
- No CI. This checkout HAS a root .env (JWT_SECRET set; lacks the three UNILATERAL_* keys,
  which have config defaults) and a .venv (uv-managed CPython 3.12.13).

### Stack and layout
- backend/ (uv workspace member, flat packages): common/ (config, packet codec, scaling,
  redis keys), ingest/ (UDP -> align -> jitter -> 60 Hz ticker -> biomech -> Redis),
  api/ (FastAPI routes, WS hub, DB COPY writer, predict + insight jobs), migrations/
  (raw SQL 001..006, custom runner), tests/ (pytest, asyncio_mode=auto).
- frontend/ (React 18, TS 5.6, Vite 5, Vitest 3, uPlot for 60 Hz live, ECharts for history).
  src/lib/config.ts holds UI constants; src/lib/demo/ is the always-on synthetic layer;
  src/lib/storage/ is the sleeve USB layer (pure modules + fsa.ts adapter), page
  src/pages/Storage.tsx, components/storage/*. api/routes/config.py serves udp-target.
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
- `cd frontend; npm run build` (tsc -b is the lint gate; no ESLint); `npm test` (20 files,
  206 tests). Full pytest: 361 passed on 2026-09-23; test_biomech's 3 ms bench guard fails
  on a busy or slow machine (biomech unchanged), re-run idle before trusting a red.
- `docker compose up -d --build`; simulator: `uv run python simulator/simulate.py
  --devices 5 --target 127.0.0.1:5005` (its default 5010 is a Docker Desktop workaround).
- deploy/deploy.sh rebuilds PRODUCTION over ssh with no confirmation; provision.sh resets
  ufw and hardens sshd. Never run either, or the simulator against prod (IDs >= 100 only,
  bounded --duration), without explicit user confirmation. Procedure: deploy/deploy.md.

### Config tiers (where tunables live)
- Wiring and operational tunables: root .env only, documented in .env.example, read by
  backend/common/config.py. Duration syntax <int><s|m|h|d|w>. INSIGHT_HOLD_S > COOLDOWN_S.
  UDP_PUBLIC_IP: IPv4 the sleeves stream to; blank = api resolves DOMAIN (60 s cache), set
  it explicitly behind a proxy/CDN.
- Model constants: file-local on purpose (backend/ingest/biomech.py header block,
  backend/common/scaling.py; backend/api/jobs/predict.py restates dose constants with a
  sync test). Do not move them to .env: stored metrics must stay comparable.
- Frontend: src/lib/config.ts (incl. STORAGE_* tunables); copy tables in src/lib/metrics.ts,
  rig.ts and storage/copy.ts (all walked by text.test.ts). Firmware on-card format facts are
  named constants in lib/storage/{binFormat,configSchema}.ts, never tunables.

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
`common/kinds.py` is the vocabulary; `agent-docs/01_PLAN_unilateral_devices.md` is the plan of record.
- **Bilateral unit**: sync 0xA5, two leg MCUs, four IMUs, fixed +-16 g / +-2000 dps.
  source_id = leg MCU (0 left, 1 right); LIMB_MAP default (0,1) left_shin, (0,2) left_thigh,
  (1,1) right_thigh, (1,2) right_shin.
- **Unilateral knee sleeve**: sync 0xA6, ONE MCU on ONE leg with two IMUs, sensor 1 = thigh
  and 2 = shin on every source, configurable full-scale (defaults +-32 g / +-4000 dps) that
  the datagram does NOT carry. Firmware repo (ARCHITECTURE.md D13/D35, src/app_config.h):
  `C:\Users\bhavy\GitHub_HXSKL\012_demo-prototypes\003_sep26-NYKnicks-KneeSleeve\firmware\NYKnicksDataLogger`
- **Unit** = one sleeve, wire identity (0xA6, device_id, source_id), id `u<dev>-<src>`.
  **Rig** = what everything downstream is keyed by. Bilateral rig id is the device byte
  ("30"); an unpaired sleeve is its own rig; two sleeves paired in the dashboard share the
  host's rig id and the joiner's own row is hidden while paired.
- Inside a sleeve rig each unit sits on a VIRTUAL source (left or side-less -> 0, right -> 1),
  so a paired rig presents (0,1),(0,2),(1,1),(1,2) like a bilateral unit; per-source keys work.
- A side-less sleeve streams bare segments ("thigh","shin"); `biomech.limb_role()`
  (biomech.py:385) reads side and segment from the limb NAME, so those are neither side.
- `compute()` takes `expected_limbs` (rig size) and `limb_scale` (per-limb counts-to-SI).
  `degraded_sensors` = fewer limbs than the rig should have; `one_leg` nulls m5 honestly.
- Pairing, side and full-scale are dashboard-owned: table `sleeve_units` (migration 005),
  endpoints under `/api/units`, mirrored to Redis `unit:cfg:{id}` (no TTL) and announced on
  channel `unit_cfg`. This is the ONLY api -> ingest direction in the Redis contract.
  Any change to them hard-resets that rig's biomech session. Since 2026-09-23 (decision H)
  side is seeded from wire source_id (0 left, 1 right) at registration and by
  `UnitConfig.default` (both MUST agree); NULL = operator cleared; 006 backfilled legacy rows.
- Simulator: `--sleeves N` emits sleeve units, rescaling the capture's counts to the
  sleeve's full-scale. Pass `--target 127.0.0.1:5005` (its default 5010 is a dev workaround).

### Sleeve on-card storage "HIPPOSDATA" (verified in firmware source 2026-09-23)
Firmware tree is fw 1.2.0 with uncommitted changes; fielded units may still run 1.1.0.
Host-side scripts: `C:\Users\bhavy\GitHub_HXSKL\NYKnicks\Knee Sleeve\{bin2csv.py,sensor_stats.py}`.
- The drive is the raw FAT32 SD card over TinyUSB MSC (prod build only), label "HipposData",
  composite device with the chip MAC as USB serial. MSC starts when a real host enumerates;
  logging AND UDP streaming stop while mounted; host has full read/write. Host "Eject" does
  NOT end the session: only unplug (2 s debounce) -> firmware remounts, re-reads CONFIG.TXT,
  starts a new session, no reboot. No change detection, no dirty flag, never formats.
  UI must say "eject, then unplug"; a browser cannot eject.
- CONFIG.TXT: `key=value` per line, `#`/`;` full-line comments only, keys case-sensitive,
  last duplicate wins, fgets(160) so 159+ byte lines split, firmware writes CRLF, no BOM.
  Traps: inline comments are NOT stripped; integers parse base-0 (leading 0 = octal); empty
  wifi_ssid keeps the compiled-in default. Missing file/keys are regenerated/appended with
  defaults (incl. compiled-in WiFi creds). The 14 keys, ranges and byte limits are in
  frontend/src/lib/storage/configSchema.ts. Bad values fall back; nothing can brick the unit.
- LOG_NNNN.BIN (log_format.h, frozen, format_version 1): 512 B header (magic "NYKS", fw,
  device/source id, odr, full scales + scales, session_id, CRC32 at 508) then 4096 B blocks
  (32 B header: magic 0xB10C, type IMU/TIME_SYNC/SESSION_END, sensor 1 thigh 2 shin, seq,
  base_ts_us, count <= 290, flags, CRC32 at 20; 14 B raw samples). Offsets are constants in
  lib/storage/binFormat.ts. NNNN is a persisted NVS counter. 1.1.0 files are 512 MiB
  preallocated (0xFF/0x00 tail if power-cut); 1.2.0 appends and rotates at 2 GiB.
- LOG_NNNN.TXT: diagnostics when diag_log_enabled=1, same NNNN as the session's first BIN,
  not rotated; `#` header, `[uptime] event` lines, periodic status blocks, ends "session NNNN end".
- bin2csv.py (numpy) matches log_format.h byte for byte; per-block loop, slow; 512 MiB BIN
  -> 2.27 GB CSV plus a .meta.json sidecar. sensor_stats.py (pandas, matplotlib) reads the
  CSV + sidecar, NOT the .TXT; outputs text plus PDF/PNG plots.
- Browser reach: File System Access API (showDirectoryPicker etc.) is Chrome/Edge/Opera 86+
  only, needs a secure context (https://DOMAIN or localhost) and a user gesture; Chrome 122+
  can persist the grant. Chromium's blocklist has no drive-root entry. Chrome writes a
  `<name>.crswap` beside the target and swaps on close (listings ignore *.crswap).
- Engine invariants (lib/storage/transfer.ts): only `LOG_\d{4}.(BIN|TXT)` is ever listed,
  copied or deleted (CONFIG.TXT cannot match); delete per file ONLY after the LOCAL copy
  re-reads equal (length, CRC32, block scan, bad blocks re-read from the card), never after
  an abort or with "Keep copies". Editor patches value spans only; no BOM, no inline comment.

### Known doc/code drift (2026-09-23)
m4 warm-up is 120 s in SPEC and code but 60 s in README, TRD, BACKEND_SCHEMA, UIUX and
ANALYTICS (and comments biomech.py:23,1473, CalibrationBadge.tsx:6); UIUX contradicts itself
on hiding offline devices (code keeps them visible); frontend metrics.ts:63 comment inverts
the m5 sign. Firmware repo (not ours, report only): README s1.4 and FLASHING.md s3 show
inline comments the parser does not strip; the generated "Leave wifi_ssid empty to keep the
radio off" comment is wrong (empty keeps the compiled default); D33 says INQUIRY rev 1.1
(code 1.2); D17 says 512 MB rotation (D38 corrects it to 2 GiB).
