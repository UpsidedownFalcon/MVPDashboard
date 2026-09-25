# App Flow — user journeys & data journeys

| | |
|---|---|
| Status | Set in stone (mirrors [TRD.md](TRD.md); updates only if the TRD changes). **This end state is the CURRENT state** — all three stages shipped 2026-08-03, so the auth flows (§1.1, §3) and data flows 2.2–2.4 are live, not pending. See TRD §1.1. |
| Related | [UIUX.md](UIUX.md) · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) |

## 1. User flows

### 1.1 Login
```
visit any route ─▶ has valid JWT cookie? ──yes──▶ requested page
        │no
        ▼
     /login ─▶ POST /api/auth/login ─▶ 401: inline error, stay
                       │200 (Set-Cookie: httpOnly JWT)
                       ▼
                      /  (overview)
```
Logout: `POST /api/auth/logout` (clears cookie) → `/login`. Expired cookie: any 401
from REST or a WS close with code 4401 → redirect `/login`.

### 1.2 Watch live → drill down → rename
```
/ overview ─▶ GET /api/devices (cards) ─▶ open the app-wide WS /ws/live
   │  charts render at 60Hz from the rAF buffer; numeric readouts refresh from a
   │  250 ms snapshot; status events flip online/offline badges
   ├─▶ click ✏ on card ─▶ inline edit ─▶ PATCH /api/devices/:id ─▶ name everywhere
   └─▶ click card ─▶ /device/:id
         ├─ GET /api/metrics/recent?device=:id&seconds=30  (chart backfill)
         ├─ WS stream splices in (live section)
         ├─ GET /api/metrics/windows?device=:id            (window meta, poll 60s)
         ├─ GET /api/metrics/history?device=:id&window=&buckets=  (History tab, poll 60s)
         ├─ GET /api/forecasts/latest?device=:id           (Projections tab, poll 60s;
         │                                                  `provisional` arrives on the wire
         │                                                  while bootstrapping but is not
         │                                                  surfaced — demo posture 2026-08-05,
         │                                                  UIUX §4)
         ├─ GET /api/insights/timeline?device=:id          (Insights tab — the advice TIMELINE
         │                                                  since 2026-08-06: live + stored
         │                                                  insights bucketed over PAST_WINDOWS,
         │                                                  ≤3 actions per bucket, poll 10s.
         │                                                  Survives page reloads; /current
         │                                                  remains as the pure live view.
         │                                                  Each action carries its newest
         │                                                  Adopt/Override `decision`)
         ├─ POST /api/insights/decisions                   (Adopt/Override press on one card
         │                                                  — 2026-08-07, migration 004; then
         │                                                  the timeline query is invalidated)
         └─ GET /api/insights?device=:id&limit=100         (evidence join for those cards,
                                                            same 10s. The Overview chip uses
                                                            the same route at limit=5 / 30s)
```
The WS is **one app-wide connection** opened by `LiveProvider` on entering the
authed shell — it carries all devices (the server's `?devices=` filter is unused).

### 1.3 Device lifecycle (trainer's view)
```
wearable powers on ─▶ first packet ─▶ auto-registered (name = device ID)
  ─▶ card appears "online" ─▶ trainer renames to wearer
wearable silent > OFFLINE_AFTER_S ─▶ status event ─▶ the card badge flips to offline
  (last-seen shows on the detail overlay only)
  ─▶ the card and sidebar entry STAY — offline badge, frozen live column, and the
     stored history/projections/insights all remain browsable (user decision
     2026-08-06, reversing the 2026-08-02 "silent >10s disappears" rule;
     OFFLINE_HIDE_MS survives only as the calibration badge's re-arm threshold)
wearable returns ─▶ online again (same identity, same name)
```

### 1.4 Knee sleeves: set the leg, set the full scale, pair (2026-09-23)

A knee sleeve is one MCU on one leg (TRD §3). It registers like any wearable, but three things
about it are dashboard-owned rather than measured: its pairing, its leg and its IMU full scale.
Since 2026-09-23 (PLAN_msd_management decision H) the leg is **seeded from the sleeve's own
`source_id`** (0 = left, 1 = right — what the person fitting it wrote on the card, or set in
Sleeve storage, §1.5) and the full scale from `.env`; an operator may still change either here.
Every one of them **hard-resets that soldier's biomech session** — dose, baselines, calibration.

```
sleeve powers on ─▶ first 0xA6 packet ─▶ ingest routes it to its own rig "u31-0"
  ─▶ ingest publishes unit:{u31-0}:rig into ingest:stats
  ─▶ api (unit mirror, every 2 s) INSERTs the unpaired row into sleeve_units with
     side = its wire source_id (0 left, 1 right — decision H, 2026-09-23; ingest's own
     default for a new unit is the same row, so the mirror publish resets nothing)
  ─▶ the soldier appears, summary "2 sensors | one leg"
     (a NULL side now means an operator CLEARED it: "2 sensors | side not set")

/device/u31-0 ─▶ sleeve controls in the header (sleeve rigs only)
   ├─ change or clear the leg ─▶ PATCH /api/units/u31-0 {side}   (null = cleared)
   ├─ open Full scale ──▶ PATCH /api/units/u31-0 {accel_fs_g | gyro_fs_dps}
   ├─ "Pair with..." ───▶ GET /api/units (unpaired sleeves) ─▶ pick one + a leg each
   │                   ─▶ POST /api/units/u31-0/pair {unit_id, side, host_side?}
   └─ "Unpair" ────────▶ POST /api/units/u31-0/unpair

any of the four ─▶ ONE db transaction ─▶ SET unit:cfg:{id} + PUBLISH unit_cfg
  ─▶ ingest tears down the affected rig(s) and rebuilds them on the next packet,
     skipping the Redis session snapshot (the old dose/baselines are not comparable)
  ─▶ client invalidates devices, units and both rigs' windows/history/forecasts/
     insights/advice-timeline
```

**Pairing hides exactly one row.** The sleeve you pair FROM is the host: it keeps its id,
name and history, and the joiner becomes a member on the other leg. From that moment the whole
pipeline is keyed by the host rig, so **the joiner's own card disappears from the overview,
the sidebar, the squad insight feed and the forecast job** for as long as the pairing lasts —
otherwise it would sit there forever offline with no sensors and a frozen composite. Nothing is
deleted: its `devices` row and its whole history stay untouched, `PATCH /api/devices/{id}` still
answers for it (so a rename mid-pairing works, rather than 404-ing), and **unpair brings the
card back with its old history**. Unpair keeps both sides, so each released sleeve is a one-leg soldier on the leg it
was worn on.

A rig with one leg instrumented carries the `one_leg` flag: `m1`..`m4` run normally and only
`m5` (L/R balance) reads blank with "one leg" as its reason. It is never reported as missing
sensors.

### 1.5 Sleeve storage: edit CONFIG.TXT, transfer logs, convert them (2026-09-23, 2026-09-25)

A sleeve plugged into the operator's PC exposes its SD card as the **HIPPOSDATA** USB drive
(`CONFIG.TXT`, `LOG_NNNN.BIN`, `LOG_NNNN.TXT`). The dashboard's `/storage` page (UIUX §15,
plan of record `PLAN_msd_management.md`) opens it in **Chrome or Edge on https or localhost**
through the File System Access API — nothing is uploaded, there is no helper app. While the
card is mounted the sleeve neither logs nor streams, and a host **eject does not end its
session**: only unplugging does, about 2 s after which it re-reads `CONFIG.TXT`. Since 2026-09-25
each transferred log is also converted on the PC (`agent-docs/03_PLAN_csv_summary.md`).

```
/storage ─▶ isSupported()? ──no──▶ "Sleeve storage needs Chrome or Edge on a secure (https) address"
   │yes
   ├─ GET /api/config/udp-target   (where sleeves should stream: UDP_PUBLIC_IP, else DOMAIN
   │                                resolved server-side; null when unresolvable; re-fetched
   │                                when older than 60 s)
   ├─ GET /api/units + the device registry   (soldier name for the sleeve, duplicate-id warning)
   └─ handles remembered in IndexedDB ─▶ permission still granted? ─yes─▶ drive opens itself
                                                                     ─no──▶ "Reconnect sleeve drive"

EDIT CONFIG.TXT
"Open sleeve drive" ─▶ showDirectoryPicker ─▶ folder holds CONFIG.TXT? ──no──▶ "Pick the HIPPOSDATA
   │yes                                                                          drive itself"
   ▼
read CONFIG.TXT ─▶ interpretAsFirmware (what the sleeve will actually read: whole-line comments
   only, last duplicate wins, base-0 integers, 159-byte fgets pieces, BOM) ─▶ notices + fields
   ├─ Basic: WiFi network / password, sleeve number, Left/Right leg (source_id), diag log, streaming
   ├─ "Streams to ip:port (this dashboard | not this dashboard)" + "Point at this dashboard"
   └─ Advanced (warning + "I understand", once per page session): udp_ip/udp_port, low_batt_mv,
      accel/gyro full scale, wifi_tx_power_dbm, batt_cal_*_mv
"Save to sleeve" ─▶ applyEdits (only the changed value spans; missing keys appended with CRLF;
   never a BOM or an inline comment; integers plain decimal)
   ─▶ createWritable (CONFIG.TXT.crswap beside it) ─▶ close ─▶ re-read ─▶ verifyReadback
        │mismatch ─▶ "The file read back differently from what was written; nothing else was changed"
        ▼ok
   "Saved. Now eject the HIPPOSDATA drive, then unplug the cable."
     + "also switch the sleeve off and on again"      when wifi_ssid / wifi_password changed
     + "will now appear as a new soldier (u<new>)"     when device_id / source_id changed
   ─▶ accel_fs_g / gyro_fs_dps changed AND u<dev>-<src> is known? ─▶ PATCH /api/units/u<dev>-<src>
        ─▶ "Dashboard full scale for u<dev>-<src> updated to match"  (decision I; resets that rig,
            invalidates devices, units and the rig's windows/history/forecasts/insights caches)
eject ─▶ unplug ─▶ ~2 s ─▶ the sleeve re-reads CONFIG.TXT and starts a new session

TRANSFER LOGS
listing = LOG_NNNN.BIN / LOG_NNNN.TXT only
   (CONFIG.TXT never; *.crswap, System Volume Information, $RECYCLE.BIN and dotfiles hidden)
   each BIN's 512 B header ─▶ firmware, sleeve u<dev>-<src>, full scale, session
"Choose destination folder" (remembered) ─▶ [ ] Keep copies on the sleeve ─▶ "Transfer selected"
   per file, in order:
   probe    dest/sleeve-u<dev>-<src>/raw/  (identity from the file's own header, else the TXT's
            "# cfg:" line, else CONFIG.TXT); a same-name file already there is sized + CRC32'd
   copy     card ─4 MiB slices, each awaited─▶ dest, running CRC32 + whole-file block scan;
            close() must succeed
   verify   re-read the LOCAL copy: length, CRC32 and an identical block scan; every block the
            scan called bad is re-read from the card and compared byte for byte; a TXT is
            byte-compared whole
            │mismatch ─▶ local copy removed, card untouched, "Failed: the copy did not match ..."
   dedupe   same size + CRC32 as the pre-existing file ─▶ fresh copy dropped, "Already transferred"
            (different content ─▶ kept as LOG_NNNN-2.BIN)
   delete   from the sleeve ONLY now — copy + verify passed, Keep copies off, not cancelled
"Do not unplug the sleeve while a transfer is running" ─▶ progress | rate | ETA  (~1 MB/s USB)
cancel or unplug mid-copy ─▶ writable aborted (no partial copy), card as it was, "N not started"
done ─▶ "Done: N copied, N already transferred, N failed, N removed from the sleeve" ─▶ re-list

CONVERT  (Web Worker, 2026-09-25: one file at a time while the transfer keeps copying)
entry    a BIN's item-done (or item-failed where only the card-side delete failed)
   |     "Convert missing (N)"  (a scan of dest/sleeve-*/raw/LOG_*.BIN for files lacking any
   |                             output, run when the destination is chosen or reconnected;
   |                             the button queues them, the scan itself starts nothing)
   |     "Retry" on a failed or cancelled row  (the whole run again, outputs rewritten)
   v
queue    FIFO, one worker; handles only (raw file + sleeve folder), never paths, no upload
   ->    three non-empty outputs beside raw/? --yes--> "Already converted" (nothing written)
   |no
   v
pass 1   "Scanning N%"    block CRCs, sync anchors, timestamps (outlier filter), the dt and
                          |a| medians   (bounded memory: histograms and tables, never the CSV)
pass 2   "Converting N%"  bin2csv's loop -> <stem>.csv (byte-exact, half-to-even ties)
                          + <stem>.meta.json (bin2csv's keys + unused_tail_blocks, first_bad)
                          + count histograms, clip counts, 22 ms detrended noise windows
summary  <stem>_summary.txt   sensor_stats' text sections; placement "left thigh" when the
                          dashboard knows the leg (GET /api/units), else "thigh"
   each output lands on close() (.crswap until then): a failure or a cancel never leaves a
   partial file; an empty placeholder can remain, rewritten by Retry / Convert missing
   v
"Converted" -> the page shows the summary: "Written to sleeve-u<dev>-<src> as LOG_NNNN_summary.txt"
raw/ is only read, never modified; nothing on the PC is ever deleted
```

**Added 2026-09-25 (change-set 2, `agent-docs/03_PLAN_csv_summary.md`, built):** the CONVERT block
above. The conversion runs in a Web Worker on the operator's PC while the transfer keeps copying;
the raw file is only read, nothing on the PC is deleted, and no backend call is involved (the
sleeve's side for the placement column comes from the `GET /api/units` the page already holds).

## 2. Data flows

### 2.1 Hot path: packet → pixel (target ≪ 250ms end-to-end)
```
sensor sample ─UDP─▶ ingest: raw deque (bounded)
  ─batch decode (numpy)─▶ sync/CRC check (drop+count)
  ─▶ per-sensor unwrap ts ─▶ per-(device,source) clock offset → server time
  ─▶ jitter buffer (50ms reorder window)
  ─▶ 60Hz ticker: gather ~10 samples/limb → frames{limb: [n,6]}
  ─▶ biomech.compute → m1..m5, composite (+quality)
  ─▶ Redis PUBLISH ticks
        ├─▶ api: WS hub → per-client bounded queue → browser → uPlot (rAF)
        └─▶ api: write buffer → asyncpg COPY every 1s → metrics hypertable
```

### 2.2 History: tick → the History-tab period stats
```
metrics (60Hz rows, 30d retention)
  ─continuous aggregate policy (in-DB, ~1min)─▶ metrics_1m (forever)
  ─GET /api/metrics/windows─▶ for each PAST_WINDOWS duration:
      windows <= 5m read the RAW `metrics` table; larger ones read `metrics_1m`.
      (`metrics_1m` is materialized-only, so its newest 1-2 min do not exist yet —
       up to 40% of a 5m window. The raw table has no such lag.)
  ─▶ the History-tab period selector, trend arrow and coverage chip
     (frontend polls every 60s)
```

### 2.3 Prediction: history → forecast chart
```
every PREDICT_INTERVAL_S (60s), per device:
  BOOTSTRAP path (until metrics_1m holds >=10 buckets for this device):
    read PREDICT_BOOTSTRAP_BUCKET_S (15s) buckets off the RAW hypertable over
    PREDICT_BOOTSTRAP_WINDOW, project PREDICT_BOOTSTRAP_HORIZONS capped by the
    observed span -> model_version 'trend-ols-boot-1', response `provisional: true`.
    First forecast lands in ~2.5-3.5 min instead of 15-20.
  STEADY path:
  read composite from metrics_1m over PREDICT_TRAIN_WINDOW
  ─▶ predict.fit(history) ─▶ {horizon: (pred, ci_low, ci_high)}
  ─▶ INSERT forecasts (one row per horizon, keyed by made_at)
GET /api/forecasts/latest ─▶ newest made_at per device ─▶ forecast chart
```

### 2.4 Insights: trends + forecasts → feed
```
every INSIGHT_INTERVAL_S (15s), per device:
  inputs: the INSIGHT_LIVE_WINDOW read (30s off the RAW table — this is what every
          rule means by "now") + window aggregates (2.2) + latest forecasts (2.3)
  ─▶ rule list evaluates (each: predicate → severity, message, evidence)
  ─▶ cooldown check (INSIGHT_COOLDOWN_S per device+rule, severity-ranked)
  ─▶ INSERT insights (append-only EVENT log)
       ├─▶ GET /api/insights            ─▶ Overview chip + the evidence join
       ├─▶ GET /api/insights/current    ─▶ the STATE view: rows inside INSIGHT_HOLD_S
       │     grouped on action_id, newest row per rule, ranked, cut to
       │     INSIGHT_MAX_ACTIONS (3)
       └─▶ GET /api/insights/timeline   ─▶ the TIMELINE view (2026-08-06): the same
             rows bucketed by age over PAST_WINDOWS (live / 5m / 30m / 2h with the
             shipped config), each bucket grouped exactly like /current and cut to
             INSIGHT_MAX_ACTIONS, newest-first ─▶ the Insights tab's advice stack
```

### 2.5 Online/offline status
```
ingest: every 1s → SET last_seen:dev:{id} = server ts (+ per-sensor keys)
        no packets for OFFLINE_AFTER_S ─▶ ticker suspends (no stale ticks)
api:    watches last_seen keys → WS event {type:"status", dev, online, last_seen}
        GET /api/devices merges DB registry + live last_seen
```

### 2.6 Degradation modes (by design — see TRD §1 backpressure rule)
| Failure | Effect | Never affected |
|---|---|---|
| DB slow/down | api write buffer caps (~60s) then drops oldest + counts; window/forecast queries error visibly | live WS stream, ingest |
| api down | no dashboard; ticks published meanwhile are lost (gap visible later) | ingest keeps processing |
| ingest down | devices' data lost while down; dashboard shows all offline | history browsing, api |
| Redis down | live+status stop until back (compose restarts it). Sleeve configs (`unit:cfg:*`) are lost with the keyspace but the DB still has them: the api re-mirrors every row at start and every 60 s, so pairings, sides and full scales self-heal within a minute. Until they do, each sleeve runs on defaults (its own rig, the leg its wire `source_id` implies, the `.env` full scale) | DB contents |
| browser tab slow | that client's queue drops oldest | other clients, server |
| packet loss/reorder | quality % drops; jitter buffer reorders within 50ms | tick cadence (holds last) |

## 3. Auth flow detail (REST + WS)
JWT (HS256) in httpOnly Secure SameSite=Lax cookie, set by login, `JWT_EXPIRE_HOURS`
lifetime. Browser sends it automatically on same-origin REST **and the WS upgrade**
(no token in JS/localStorage). api validates on every request; WS validates at
handshake and re-checks expiry every 60 s mid-connection, closing 4401.
