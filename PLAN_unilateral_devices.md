# Plan: unilateral device (knee sleeve) support

Status: approved 2026-09-23, shipped 2026-09-23 (Phases D and E done; see "As
built" at the end). Plan of record for the
unilateral-devices work; linked from `docs/PLAN.md`. Code is ground truth once
shipped; keep the "as built" notes at the end honest.

## Context

MVPDashboard models one wearable kind: a bilateral unit with two leg MCUs (`source_id`
0 = left, 1 = right), four IMUs, 22-byte UDP datagrams with sync byte 0xA5, fixed
+-16 g / +-2000 dps. A second kind now exists: the NY Knicks knee sleeve, one ESP32-S3
MCU with two ICM-45686 IMUs on ONE leg. Firmware:
`C:\Users\bhavy\GitHub_HXSKL\012_demo-prototypes\003_sep26-NYKnicks-KneeSleeve\firmware\NYKnicksDataLogger`.

Verified 2026-09-23 against that firmware (`src/app_config.h:121-133,170-174,269-271`,
`src/acquisition.c:117-128`, `src/imu_icm45686.c:293`, `ARCHITECTURE.md:440-462`):

| Property | Bilateral (today) | Unilateral sleeve |
|---|---|---|
| Datagram | 22 B, LE | identical layout |
| Sync byte | 0xA5 | **0xA6** (D13 "unilateral device marker") |
| Header / CRC8 | sensor_id bits 0-1, version 1 / poly 0x07 over bytes 3..19 | identical |
| sensor_id meaning | per LIMB_MAP (src0: s1 shin, s2 thigh; src1: s1 thigh, s2 shin) | **1 = thigh, 2 = shin** on every source |
| device_id / source_id | device byte; MCU 0/1 | set per sleeve in CONFIG.TXT (defaults 1 / 0) |
| Full-scale | fixed +-16 g / +-2000 dps (`scaling.py`) | **configurable**: accel {2,4,8,16,32} g, gyro {125,250,500,1000,2000,4000} dps, defaults 32 / 4000; packet carries no scale |
| Rate / battery | ~640 Hz per sensor; soc per source, min published | 640 Hz per sensor; one soc per sleeve, outside the CRC |
| Default port | UDP_PORT 5005 | firmware default 5050 (configurable) |

Today every 0xA6 datagram is rejected by `backend/common/packet.py:140-143` as `bad_sync`
with no log line, so a sleeve is invisible. Goal: sleeves appear as soldiers, one or two per
person, with the same metrics as bilateral units when paired, honest metrics and copy when
single, and dashboard-driven pairing and full-scale settings.

## Decisions (user, 2026-09-23, locked)

| # | Decision |
|---|---|
| A | Pairing is a dashboard action with persistent state. Sleeve device_ids may be equal or different. |
| B | Full-scale defaults +-32 g / +-4000 dps for every 0xA6 packet; editable per sleeve in the UI. |
| C | Sleeves send to the existing `UDP_PORT` (set `udp_port` in each sleeve's CONFIG.TXT). |
| D | Ids are namespaced by kind so nothing is dropped: bilateral stays `"30"`, sleeve unit is **`u<device_id>-<source_id>`** (e.g. `u30-0`). Two sleeves with identical (device_id, source_id) are indistinguishable on the wire; operators keep that pair unique. |
| E | Pairing takes effect in ingest: paired sleeves feed ONE ticker and biomech session, so m1..m5 and the composite work as for a bilateral unit. |
| F | Full-scale editable for sleeves only; bilateral stays at the compile-time constants. |
| G | An unpaired sleeve has NO side until an operator sets it (side-less limbs; m1..m4 run; UI says "side not set"). |
| H | The sleeve you pair FROM keeps its id and history; the joiner becomes a member and its own row is hidden while paired, returning with its old history on unpair. |
| I | m5 on a one-leg soldier: row kept, greyed, null reason "one leg". |
| J | New tick flag **`one_leg`**; `degraded_sensors` keeps meaning "a sensor the rig should have is missing". |
| K | Figure: instrumented leg lit, other leg dim with no sensor nodes; side-less sleeve shows both legs dim. |
| L | Demo soldiers stay bilateral. |
| M | Sensor summary and hero copy: rig-aware for sleeves, R1 literal kept for bilateral and demo; hero loses the fixed count. Record as a deliberate re-open of STAGE4 R1 for sleeves only. |
| N | Any pairing, unpairing, side or full-scale change hard-resets that soldier's biomech session (dose, baselines, calibration). |

**Amended 2026-09-23 (PLAN_msd_management decision H) — decision G.** The side is now
**seeded from the sleeve's wire `source_id`** at registration (0 = left, 1 = right,
`common/kinds.py::side_for_source`; `api/unit_mirror.py::register_units` inserts it and
`UnitConfig.default()` derives the same value, so the api row still equals ingest's default
and registering a new sleeve resets nothing). `NULL` therefore means an operator **cleared**
the side, not "not set yet"; the UI still reads "side not set", streams the bare `thigh`/`shin`
segments and flags `one_leg` for that case, and `PATCH {"side": null}` still works on an
unpaired unit. Migration `006_sleeve_side_backfill.sql` (data-only) gives every legacy unpaired
`NULL` row the side its `wire_source_id` implies; paired members and explicit sides are
untouched, and the change is reversible per unit through `PATCH /api/units/{id}`. Everything
else in G (side-less limbs, m1..m4 running, the copy) stands for a cleared side.

## Design

### 1. Vocabulary
- **Kind**: `bilateral` (sync 0xA5) or `unilateral` (sync 0xA6).
- **Unit**: one physical sleeve, wire identity (0xA6, device_id, source_id), id `u<dev>-<src>`.
- **Rig**: what ingest tickers, biomech sessions, `devices` rows, WS ticks and UI cards are keyed
  by (`device_id` everywhere downstream). A bilateral unit is rig `"30"`. An unpaired sleeve is
  rig `"u30-0"` (its own unit id). A pair is the host sleeve's rig with two member units.
- **Virtual source** inside a sleeve rig: left or side-less unit -> 0, right unit -> 1. A paired
  rig therefore presents sensors (0,1),(0,2),(1,1),(1,2) like a bilateral unit, and the existing
  per-source battery, sensor stats and `last_seen:sensor` keys work unchanged.
- **Limb names** for a unit: `<side>_<segment>` when the side is set (`left_thigh`), else the
  bare segment (`thigh`, `shin`); `biomech.limb_role()` (`biomech.py:385`) already yields side
  `None` + segment for those, and `impact_i` still resolves to the shank.

### 2. Shared helpers (new `backend/common/kinds.py`)
`SYNC_BILATERAL = 0xA5`, `SYNC_UNILATERAL = 0xA6`, `KIND_BILATERAL = 0`, `KIND_UNILATERAL = 1`,
a 256-entry sync->kind LUT (255 = invalid), `unit_id(dev, src)`, `parse_unit_id(s)`,
`rig_kind(rig_id)` (prefix `u` = unilateral), `ACCEL_FS_ALLOWED = (2,4,8,16,32)`,
`GYRO_FS_ALLOWED = (125,250,500,1000,2000,4000)` (mirrors firmware `imu_fs_valid()`),
`lsb_per_g(fs_g) = 32768/fs_g`, `lsb_per_dps(fs_dps) = 32768/fs_dps`, and a frozen
`UnitConfig(unit_id, rig_id, side, accel_fs_g, gyro_fs_dps)` with `default(unit_id, settings)`
and JSON to/from (`{"unit","rig","side","accel_fs_g","gyro_fs_dps","v":1}`).
`BILATERAL_EXPECTED_LIMBS = 4`, `UNIT_LIMBS = 2`.

### 3. Config (`backend/common/config.py`, `.env.example`)
New Settings fields, all documented in `.env.example` (rule 5, tier 1):
- `UNILATERAL_SENSOR_MAP` default `{"1":"thigh","2":"shin"}` (sensor_id -> segment; firmware
  `SENSOR_ID_IMU0/1`), parsed like `LIMB_MAP`, segments must be distinct.
- `UNILATERAL_ACCEL_FS_G` default 32, `UNILATERAL_GYRO_FS_DPS` default 4000 (validated
  against the allowed sets). These seed every newly seen unit; per-unit values then live in
  the DB.

### 4. Wire (`backend/common/packet.py`)
- `_decode`: `kind = SYNC_LUT[wire[:,0]]`, `sync_ok = kind != 255`; `kind` is sliced by the
  sync mask (`:143`) and the CRC mask (`:148`) like the other columns. `Batch` gains
  `kind: np.ndarray` (uint8) and `n_by_kind`. Because `_decode` is shared, `decode_log()` accepts
  both sync bytes too; document that (the SD golden capture is all 0xA5, no test impact).
  Keep `SYNC = SYNC_BILATERAL` as an alias for existing importers and the golden test.
- `encode(..., sync: int = SYNC_BILATERAL)`.
- Docstring: single source of truth for BOTH sync bytes.

### 5. Ingest
**Routing (`backend/ingest/state.py`)**
- Pack `kind << 24 | dev << 16 | src << 8 | sen` (bits 24+ are free, `state.py:175-189`);
  unpack `k >> 24`, `(k >> 16) & 0xFF`.
- `Registry(max_devices=None, *, limb_map=None, unilateral_sensor_map=None, unit_cfg=None)`
  with defaults (`dict(_DEFAULT_LIMB_MAP)`, `{1:"thigh",2:"shin"}`, empty cache) so the bare
  `Registry()` used by 20+ tests keeps working; `ingest/main.py` passes the real settings.
- `Registry.devices: dict[str, DeviceState]`; `DeviceState(rig_id: str, kind, limb_map,
  expected_limbs, limb_scale, units, skip_restore)`; every `%d` log format on a device id
  becomes `%s` (state.py:107,131,157,162; ticker.py:112,145,179,199,209; main.py:98,112,118,195)
  and type hints follow (`evict_stale -> list[str]`, `TickInput.device_id: str`,
  `TickerManager.tickers`). `publish.py:53` already stringifies.
- Per group: `resolve(kind, dev, src) -> (rig_id, vsrc, unit_id)`. Bilateral: `(str(dev), src,
  None)`. Unilateral: `unit = u{dev}-{src}`; `cfg = unit_cfg.get(unit) or default`;
  `rig_id = cfg.rig_id`; `vsrc = 1 if cfg.side == 'right' else 0`.
- Rig map built from config, not from packets: the cache indexes units by `rig_id`, so a paired
  rig's map includes BOTH members at creation, before the second member's first packet.
  Bilateral = `limb_map`; unilateral = for each member unit
  `{(vsrc, sen): f"{side}_{seg}" or seg}`. The builder verifies limb-name uniqueness; on a
  collision (two side-less members) it logs and falls back to per-unit rigs rather than build
  the map `config.py:147-169` warns about. `expected_limbs`: bilateral 4, unilateral
  `2 * len(units)`. `limb_scale`: unilateral `{limb: (lsb_per_g, lsb_per_dps)}`, bilateral `None`.
- `Registry.apply_unit_config(cfg)`: no-op if equal to the effective config; otherwise
  `_remove` the unit's old rig and the new rig (cancels their tickers), record
  `reset_at[rig_id] = now`, and fire `on_rig_reset(rig_id)`. The next packet recreates the
  rig(s); a DeviceState created within 2 s of `reset_at` carries `skip_restore=True`, which
  `BiomechRestorer.device_added` honours (decision N, and it closes the race with a snapshot
  written up to 1 s earlier). `MAX_DEVICES` counts rigs.
- `_warn_if_all_rejected` (`ingest/main.py:130-152`) also watches `bad_sync`.

**Unit config cache (new `backend/ingest/unit_config.py`)**
- `load()`: `SCAN unit:cfg:*` into dicts keyed by unit and by rig. Startup order in `amain`:
  settings -> registry -> publisher -> `start_udp_server` (the deque buffers) ->
  `await asyncio.wait_for(cache.load(), timeout=3)` (on timeout or Redis down: log, run on
  defaults, the subscriber loads when it connects) -> drain task -> other tasks. The
  subscriber task joins `tasks` for shutdown.
- Subscribe channel `unit_cfg`; on message `GET unit:cfg:{id}` (missing = default) and call
  `registry.apply_unit_config`. Reconnect loop like `publish.py`.

**Ticker (`backend/ingest/ticker.py`)**: frames and `_expected_per_tick` come from
`device.limb_map` instead of `settings.limb_map` (`:64-67`, `:128`); constructors unchanged.
A config change recreates the DeviceState and therefore the ticker.

**Biomech (`backend/ingest/biomech.py`)**
- `compute(frames, state, times, *, expected_limbs=4, limb_scale=None)`; both optional, so
  every caller (main.py:190, scripts/calibrate_capture.py:140, tests, all positional) is
  unchanged. `_Sess` gains `fs_a_vec`, `fs_w_vec` (shape `(1, n, 1)`, default 1.0); `compute`
  sets them every tick from `limb_scale` (no-op when equal, because `get_session` `:800-804`
  can rebuild the session) as `ACCEL_LSB_PER_G / lsb_per_g` and `GYRO_LSB_PER_DPS /
  lsb_per_dps`; applied at `:1054-1055`, composing with `k_vec` (`:566`). Saturation (`:1057`)
  stays in counts and is correct for any full-scale. Scale is config, not snapshot state. The
  batch `calibrate()` (`:961-994`, scripts only) stays at bilateral constants; note it.
- Flags: `:1264` becomes `if n_limbs < expected_limbs: degraded_sensors`. New
  `one_leg = n_limbs > 0 and (len(left_i) == 0 or len(right_i) == 0)` -> `flags.add("one_leg")`;
  `:1420-1421` already nulls m5 when a side is absent, so only the `warming_up` /
  `degraded_sensors` adds at `:1500-1509` gain a `not one_leg` guard. A bilateral unit
  configured with a 2-entry one-side map carries both flags, which is true. No Metrics or
  snapshot change.
- `BiomechRestorer._restore` uses `tuple(sorted(device.limb_map.values()))` (must equal
  `tuple(sorted(frames))` or `get_session` rebuilds every tick); the ctor `limbs` stays as the
  fallback so `test_restore.py` constructs it unchanged.
- `ingest/main.py`: `on_tick` passes `expected_limbs=device.expected_limbs,
  limb_scale=device.limb_scale`.

**Publish (`backend/ingest/publish.py`)**: `ingest:stats` gains `dev:{rig}:kind`,
`sensor:{rig}:{src}:{sen}:limb`, `unit:{unit}:rig`, `unit:{unit}:side`,
`unit:{unit}:last_seen`, `unit:{unit}:soc`, `global:recv:bilateral`, `global:recv:unilateral`.
`on_rig_reset` queues `DEL biomech:state/cal/diag:{rig}` and `last_seen:dev:{rig}` into the
Publisher's NEXT stats pipeline (ordered after any pending SET, `publish.py:182-196`), never
an ad-hoc delete. Tick JSON unchanged (pinned by `test_ws.py:145`); `one_leg` rides in `f`
and joins `FLAG_VOCABULARY` (`test_ws.py:32-35`).

### 6. API and DB
**Migration `backend/migrations/005_sleeve_units.sql`** (plain DDL only; the runner splits on `;`):
```sql
CREATE TABLE IF NOT EXISTS sleeve_units (
    unit_id        TEXT PRIMARY KEY,          -- 'u30-0'
    wire_device_id SMALLINT NOT NULL,
    wire_source_id SMALLINT NOT NULL,
    rig_id         TEXT NOT NULL,             -- own unit_id when unpaired; host unit_id when paired
    side           TEXT CHECK (side IN ('left','right')),   -- NULL until set (decision G)
    accel_fs_g     SMALLINT NOT NULL,         -- seeded from Settings, no DDL default (rule 5)
    gyro_fs_dps    SMALLINT NOT NULL,
    first_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sleeve_units_rig_idx ON sleeve_units (rig_id);
```
No FK, same argument as `004`. `devices` is unchanged: kind derives from the id prefix.
`test_migrations.py:60-63` exact list gains `005_sleeve_units.sql`.

**Registration and mirror (new `backend/api/unit_mirror.py`, one task started in the
lifespan with `pool` and `app.state.redis`)**: every 2 s `HGETALL ingest:stats`, parse
`unit:{id}:rig` keys, `INSERT ... ON CONFLICT DO NOTHING` (`rig_id = unit_id`, `side NULL`,
Settings full-scale), and `mirror_unit` for new rows; every 60 s and at start `mirror_all`.
`mirror_unit(redis, row)` = `SET unit:cfg:{id}` (JSON, **no TTL**) + `PUBLISH unit_cfg {id}`.
Ingest already runs those defaults and `apply_unit_config` no-ops on an equal config, so
registration never resets a rig. `Writer` is untouched (it has no Redis handle and returns
early on an empty buffer, `writer.py:32,94-95`). Rig rows still auto-register from ticks
(`u30-0` shows its id as `display_name` until renamed). First api-written, ingest-read key;
document the direction and that a Redis restart self-heals within 60 s.

**Queries (`backend/api/queries.py`)**: one `visible_rig_predicate` (SQL fragment) shared by
`devices()`, the squad insight feed (`/api/insights` with no device), `InsightJob` and
`PredictJob`: a unilateral rig is hidden while its own unit exists with `rig_id != device_id`.
`device_one()` bypasses the predicate so renaming or reading a hidden member returns it
rather than 500 (`routes/devices.py:42-43`). Each device row adds `kind` and `units[]`; each
sensor adds `unit_id` (null for bilateral) and takes `limb` from the new stats field, falling
back to `settings.limb_map`. New `units()` for the pair picker.

**Routes (new `backend/api/routes/units.py`, included with the guard in `main.py:75-83`)**

| Method + path | Body | Result |
|---|---|---|
| `GET /api/units` | | `[{unit_id, rig_id, rig_display_name, side, accel_fs_g, gyro_fs_dps, online, last_seen, soc, paired}]` |
| `PATCH /api/units/{unit_id}` | `{side?, accel_fs_g?, gyro_fs_dps?}` | unit object. 404 unknown; 422 value not allowed; 409 side already taken in its rig, or `side: null` requested on a paired unit |
| `POST /api/units/{host}/pair` | `{unit_id, side, host_side?}` | the rig's device object. 404; 409 either already paired, same side, or host full; 422 host side unknown and not given |
| `POST /api/units/{unit_id}/unpair` | | `{units: [...]}`; releases the member (or all members if called on the host). Sides are kept. 409 if not paired |

Every write: one transaction, then `mirror_unit` for each affected unit. Templates:
`routes/devices.py:13-44` (validator, UPDATE...RETURNING, 404, re-read) and
`routes/insights.py:199-234` (POST, Literal enums). `StubRedis` in
`test_routes_metrics.py:22-37` gains `set`, `publish`, `scan_iter`.

**`GET /api/devices` row additions** (additive, BACKEND_SCHEMA s3):
`"kind": "bilateral"|"unilateral"`, `"units": [{unit_id, side, accel_fs_g, gyro_fs_dps,
online, last_seen, soc}]` (empty for bilateral), sensors gain `"unit_id"`.

### 7. Simulator (`simulator/simulate.py`)
`--sleeves N` (default 0) emits N unilateral units with ids `--sleeve-base-id` (default
`base_id + devices`) and `--sleeve-source-id` (default 0), sync 0xA6, streams
`(src,1)=thigh` from the capture's `(0,2)` stream and `(src,2)=shin` from `(0,1)`, with int16
counts multiplied at load by `16/--sleeve-accel-fs` and `2000/--sleeve-gyro-fs` (defaults
32 / 4000) so the same motion is represented at the sleeve's scale. `--dead-sensors`, `--soc`,
`--loss` etc. apply to sleeves too. `DeviceSim` keeps its optional kwargs so
`test_simulator.py` survives. README recipe passes `--target 127.0.0.1:5005` explicitly
(the default is the 5010 dev-box workaround).

### 8. Frontend
- `lib/api.ts`: `Device` gains **optional** `kind?`, `units?: Unit[]`; `Sensor` gains optional
  `unit_id?`; new `Unit` type. Optional so `demo/api.ts:67` and `demo/api.test.ts:37` stay
  untouched (decision L); `rig.ts` treats undefined as bilateral. `fetchUnits`, `patchUnit`,
  `pairUnit`, `unpairUnit` follow the `isDemoId` short-circuit shape of `renameDevice`
  (`api.ts:288-295`) so the network never learns a demo soldier exists.
- new `lib/rig.ts`: `isSleeveRig`, `instrumentedSides`, `sensorSummaryText(device)` (R1 literal
  for bilateral; `2 sensors | one leg | 6400Hz logging`, `2 sensors | side not set | 6400Hz
  logging`, `4 sensors | 2 sleeves | 6400Hz logging` for sleeves), `batteryTooltip(device)`,
  all strings in an exported `RIG_COPY` table that `text.test.ts:18-22` walks. `config.ts`
  keeps `SENSOR_SUMMARY_TEXT` and adds `LOGGING_RATE_TEXT = '6400Hz logging'`.
- `lib/metrics.ts`: `FLAG_META.one_leg = {weight:'muted', label:'one leg', hint:'One leg
  instrumented - balance needs both legs'}`. `Device.tsx:39-47` `nullReason(flags, metricId)`
  considers `one_leg` only for `m5`, so m4 still reads "warming up" during its warm-up
  (decision G); order for m5: `['one_leg','degraded_sensors','partial','saturated','warming_up']`.
- `components/HumanoidFigure.tsx`: when `limbs` is provided, skip nodes whose limb is absent
  (`:354-357`); `aria-label` from a `sensorCount` prop with no side wording. `Device.tsx`
  suppresses `emphasis` unless both sides are instrumented. Side-less limbs (`thigh`,
  `shin`) match no bone, so both legs render dim (decision K) via the existing `:313-317` path.
- `components/SensorSummary.tsx` (widen its `Pick` to `kind`/`units`/`device_id`) and
  `Battery.tsx` read `rig.ts` helpers.
- new `components/RigControls.tsx` in the device header (`Device.tsx:110-124`, spec amendment
  UIUX s4): per unit a side segmented group (Left / Right) and a full-scale readout
  (`+-32 g | +-4000 dps`) that expands into two segmented groups over the allowed sets;
  `Pair with...` expands an inline list of unpaired units from `fetchUnits` plus a side
  choice (Override-note form pattern, `InsightsPanel.tsx:107-139`); `Unpair`. Mutations
  invalidate `['devices']`, `['units']` and the per-rig keys (`windows`, `history`,
  `forecasts`, `insights`, `advice-timeline`) for both rigs; errors show inline (higher
  stakes than rename). Hidden for bilateral and demo rigs. The 60 s live buffer keeps
  pre-change samples after a full-scale change; accepted and noted in UIUX.
- `pages/Overview.tsx`: eyebrow `Lower-limb telemetry | thigh and shin sensors | live`;
  paragraph opens `Sensors on each soldier's thighs and shins stream motion...`; rest unchanged.
- Tests: `lib/rig.test.ts`; `text.test.ts` walks `RIG_COPY`; `api.test.ts` asserts the four new
  helpers never fetch for demo ids; components stay unrendered (no DOM tests, by design).

### 9. Docs and decision log (Phase E, same change-set)
README (status, sleeve quickstart: CONFIG.TXT `udp_port`, unique device_id/source_id,
pairing and full-scale flow, simulator flags); `docs/TRD.md` s3 (two sync bytes, kinds, unit
ids, virtual sources, unilateral sensor map) and s7 (new keys); `docs/BACKEND_SCHEMA.md` s1
(merged schema + 005 paragraph), s2 (`one_leg`), s3 (new endpoints and fields), s4 (new keys,
channel and stats fields with `api -> ingest` direction), s5 (`compute()` kwargs);
`docs/biomech/SPEC.md` s7.2 (per-limb scale), s8 ladder (one-leg row -> `one_leg`,
`expected_limbs`), s10 flag table, dated entries; `docs/UIUX.md` s4 header, sensor summary,
figure, chips, s11 copy, data table; `docs/APPFLOW.md` pairing flows and hidden member rows;
`docs/tasks/STAGE4.md` as-built note that R1 is re-opened for sleeves (user decision
2026-09-23); `docs/PLAN.md` and `docs/IMPLEMENTATION_PLAN.md` stage-5 pointer;
`.env.example`; root `CLAUDE.md` Project Context. `docs/ANALYTICS.md` needs no change
(no metric equation changes).

## Work packages (Phase D)

| WP | Files | Depends on |
|---|---|---|
| 1 common | `common/kinds.py`, `packet.py`, `config.py`, `redis_keys.py`; `tests/test_packet.py`, `test_config.py` | none, do first |
| 2 ingest | `ingest/state.py`, `ticker.py`, `biomech.py`, `publish.py`, `main.py`, new `unit_config.py`; `tests/conftest.py`, `test_ticker.py`, `test_biomech.py`, `test_soc.py`, `test_restore.py`, `test_ingest_udp.py`, `test_ws.py` (flag vocabulary), new `test_unit_config.py` (int ids in these tests become `"30"`) | WP1 |
| 3 api | `migrations/005_sleeve_units.sql`, `api/queries.py`, new `routes/units.py`, new `unit_mirror.py`, `main.py`, `routes/insights.py` (feed predicate), `jobs/insights.py`, `jobs/predict.py`; `tests/test_migrations.py`, `test_routes_metrics.py` (StubRedis), new `test_units.py` | WP1 |
| 4 simulator | `simulator/simulate.py`; `tests/test_simulator.py` | WP1 |
| 5 frontend | files in section 8 | none (contract above) |
| 6 docs | section 9 | WP2-5 green |

WP2, WP3, WP4 and WP5 touch disjoint files and run in parallel after WP1. Nothing shares
the build except the backend test run, which is serialised.

## Verification

Prerequisite: this checkout has no `.env` and no `.venv`. I will copy `.env.example` to
`.env` with a locally generated `JWT_SECRET` (never clobbering an existing file) and run
`uv sync --dev`, then `docker compose --profile debug up -d` for the DB and Redis tests.

1. `uv run pytest backend/tests/` with the debug profile up: all existing tests green plus the
   new ones. Key new assertions: 0xA6 decodes with kind, 0x00 still rejected; a 0xA6 real
   datagram fixture round-trips; routing of a sleeve to `u30-0` with `thigh`/`shin` limbs and
   quality about 1.0; a configured pair builds a 4-limb map before the second unit streams;
   ladder row `("left_shin","left_thigh")` with `expected_limbs=2` gives `one_leg` and no
   `degraded_sensors`, with 4 gives both; a +-32 g frame scaled through `limb_scale`
   calibrates (k in 0.95..1.05) where the unscaled one goes `cal_failed`; a config change
   recreates the rig with `skip_restore`; migration list includes 005; unit registration from
   stats; pair/unpair/side/FS validations and Redis mirror; hidden member rig excluded from
   `/api/devices` but readable by `device_one`.
2. `cd frontend; npm run build; npm test`: type-check clean, 66 existing tests plus new ones.
3. Live: `docker compose up -d --build`; `uv run python simulator/simulate.py --devices 1
   --sleeves 2 --target 127.0.0.1:5005 --duration 600`; `/api/devices` shows `30`, `u31-0`,
   `u32-0`; sleeves carry `one_leg`, no `degraded_sensors`, calibration completes; set sides,
   pair `u32-0` into `u31-0`; `u32-0` disappears from the list, `u31-0` shows 4 sensors and,
   after warm-up, m5; unpair restores both; change full-scale on a sleeve and confirm the
   session reset and no `cal_failed`. Bilateral regression: default simulator run unchanged.
   Docker Desktop UDP gotchas per README apply.
4. `/api/health` shows `global:recv:unilateral` counting and `bad_sync` no longer growing.

## Recommended commit points
1. `common: accept the 0xA6 unilateral sync byte, kind-tagged batches, unit ids and settings`
2. `ingest: rigs, unit config cache, per-rig limb maps and scale, one_leg flag`
3. `api: sleeve_units (005), unit endpoints, Redis mirror, rig-aware device list`
4. `simulator: --sleeves emits unilateral units at +-32 g / +-4000 dps`
5. `frontend: sleeve rigs, pairing and full-scale controls, one_leg, rig-aware copy`
6. `docs: unilateral devices across README, TRD, BACKEND_SCHEMA, SPEC, UIUX, APPFLOW`

---

## As built (2026-09-23)

Code is ground truth. Where the design above differs from what shipped, the code
is right and this section records it; the rest of the plan matched.

1. **`Batch` counters.** The plan says `n_by_kind`; `common/packet.py` has
   `n_bilateral` and `n_unilateral` (two ints, cheaper than a dict per batch).
2. **`one_leg` guard.** The plan writes `n_limbs > 0 and (...)`; `biomech.py`
   drops the `n_limbs > 0` term because `compute()` has already returned
   `HELD_ZERO` by then when there are no frames. Behaviourally identical.
3. **Extra stats fields.** `publish.py` also emits `unit:{unit}:accel_fs_g` and
   `unit:{unit}:gyro_fs_dps`, so `/api/health` shows what scale a sleeve is
   being read at without a DB round-trip.
4. **Two more 409s on pair** than the plan's table: pairing a unit with itself,
   and pairing in a joiner that already hosts a pair (it would orphan its
   members). `backend/api/routes/units.py` is the contract.
5. **`FLAG_META.one_leg` carries an icon** (`PersonStanding`), because UIUX s6
   requires a chip to pair icon + label.
6. **Migration DDL.** The plan's inline comment contained a `;`, which the
   migration runner's naive statement splitter would have cut the CREATE TABLE
   in half. The shipped `005_sleeve_units.sql` has no `;` inside a comment.
7. **Unit registration** lives in `api/unit_mirror.py`'s periodic task, not in
   `writer.py`: the Writer has no Redis handle and returns early on an empty
   tick buffer, so a hidden member would never have been registered.
8. **Side default changed after shipping** (2026-09-23, `PLAN_msd_management.md`
   decision H, amendment to decision G above): registration now seeds `side`
   from the wire `source_id` instead of `NULL`, `UnitConfig.default()` matches,
   and migration 006 backfills legacy unpaired rows. The sleeve's `CONFIG.TXT`
   (including `source_id`) is now edited from the dashboard's Sleeve storage
   page (`/storage`) rather than by hand over USB; that page's plan of record
   is `PLAN_msd_management.md`.

### Verification actually run

| What | Result |
|---|---|
| `uv run pytest backend/tests/` (Postgres + Redis up) | 336 passed, 0 skipped |
| `cd frontend; npm run build` | clean (only the pre-existing echarts chunk-size note) |
| `cd frontend; npm test` | 10 files, 81 tests passed |
| Live stack + `simulate.py --devices 1 --sleeves 2` | see below |

Live run against the local stack: both kinds register side by side as `30`,
`u31-0` and `u32-0` with no id collision; a side-less sleeve maps `thigh`/`shin`
and flags `one_leg` without `degraded_sensors`; setting a side renames its limbs;
pairing hides the joiner's own rig, gives the host all four limbs attributed to
the right units, clears `one_leg` and produces balance (near even, because both
sleeves replay the same capture); clearing a paired unit's side is refused with
409; a full-scale change is stored and restarts the rig; unpair restores both
rigs with their sides intact; ~2.9 M unilateral samples accepted with
`bad_sync = 0`; quality settles at 0.99 on both kinds, so a sleeve is not scored
against the four-limb map.

Three checks in the first live pass failed because of the harness, not the code,
and were corrected: the squats capture never holds still, so nothing calibrates
(the bilateral rig included); balance is published in the metrics stream, not in
the `/api/health` diagnostics block; and quality was sampled during warm-up and
mid-reconfiguration, where it is legitimately low.
