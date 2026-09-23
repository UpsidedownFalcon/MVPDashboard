# PLAN_msd_management: sleeve "HIPPOSDATA" storage management in the dashboard

Plan of record for branch `MSD-management`. Approved by the user 2026-09-23 (Phases 0-C).
This file lives at `agent-docs/02_PLAN_msd_management.md` (moved from the project root on
2026-09-24 when the agent planning files were gathered into agent-docs/).
Code is ground truth; deviations are recorded in "As built" at the end.

## 1. Context

Unilateral knee sleeves (fw 1.1.0 fielded, 1.2.0 in progress, same CONFIG.TXT and log
format) expose their FAT32 SD card as a USB drive "HIPPOSDATA" holding CONFIG.TXT,
LOG_NNNN.BIN (sensor data) and LOG_NNNN.TXT (diagnostics). Today users edit the config in
Notepad and copy logs by hand. The dashboard (React SPA at https://DOMAIN on a VPS; Chrome or
Edge is acceptable for this feature, Chrome preferred) must:

1. edit CONFIG.TXT safely, with a basic view for users and a warning-gated Advanced view;
2. transfer LOG files to the user's PC, verify the copies, and only then delete them from
   the sleeve; CONFIG.TXT is never listed, transferred or deleted;
3. (change-set 2) turn each transferred log into a CSV plus a plain-text summary on the PC,
   so users see results rather than raw files.

Direction (locked): browser-native. The File System Access API in Chrome/Edge reads and
writes the drive; conversion runs in a Web Worker in the page. No upload, no helper app.
The file-operations layer sits behind one interface so a native helper could be added later.

## 2. Decisions (user, 2026-09-23, locked)

| # | Decision |
|---|---|
| A | Browser-native via File System Access API; Chrome/Edge only; no helper app, no upload. |
| B | Summary = a ported subset of sensor_stats.py (text), not the .TXT diagnostics. |
| C | Two change-sets: CS1 = config editor + verified transfer; CS2 = CSV + summary. |
| D | New sidebar page `/storage` "Sleeve storage" under Command (not a Device tab). |
| E | Advanced gate = warning + "I understand" acknowledgement per session. No role gating. |
| F | Basic view edits ONLY: wifi_ssid, wifi_password, device_id ("the sleeve's number, allocate it yourself"), source_id as a Left/Right toggle (0 = left, 1 = right), diag_log_enabled, stream_enabled. Everything else is Advanced. |
| G | Basic view shows the UDP target read-only ("Streams to a.b.c.d:port (this dashboard / not this dashboard)") with a one-click "Point at this dashboard" button; Advanced edits udp_ip/udp_port and has the same button. Backed by new `GET /api/config/udp-target`. |
| H | Dashboard side defaults from wire source_id (0 left, 1 right) at unit registration; still overridable in the Device page. Amends PLAN_unilateral decision G. |
| I | After a config save that changed accel_fs_g/gyro_fs_dps, auto-PATCH `/api/units/{u<dev>-<src>}` to match when that unit exists, and say so on screen. |
| J | Delete each file from the sleeve automatically after ITS copy verifies; "Keep copies on the sleeve" checkbox (default off) skips deletion for that run. |
| K | Destination layout: `<dest>/sleeve-u<dev>-<src>/raw/LOG_NNNN.{BIN,TXT}`; CS2 outputs one level up next to `raw/`. Raw is kept; the dashboard never deletes from the PC. |
| L | CS2 conversion runs automatically after each file verifies, in a worker, while the next file copies. |
| M | Summary format: plain text, `LOG_NNNN_summary.txt`, sensor_stats wording, also shown in the page. |
| N | CSV byte-exact with bin2csv.py (half-to-even on exact ties via integer arithmetic); meta.json compared parsed, not byte-wise. |

## 3. Facts the implementation must honour (verified in firmware source, fw 1.2.0 tree)

- MSC = raw FAT32 card, removable, writable; logging and streaming stop while mounted; the
  firmware never writes while the host owns the card. Host Eject does NOT end the session;
  only unplug (2 s debounce) remounts, re-reads CONFIG.TXT and starts a new session
  (wifi_ssid/wifi_password need a power cycle). UI must say "Eject, then unplug".
- CONFIG.TXT parser: `key=value` per line; only whole-line comments (`#` or `;` after
  leading whitespace); keys case-sensitive; values trimmed (C isspace); split at first `=`;
  last duplicate wins; fgets buffer 160 so a line of 159+ chars is split and its tail parsed
  as its own line; integers via strtoul base 0 (leading 0 = octal, 0x = hex): ALWAYS write
  plain decimal; empty wifi_ssid/wifi_password keeps the compiled-in default; byte limits
  ssid 32, password 64, udp_ip 15 (no IP validation in firmware; a bad IP silently disables
  streaming); wifi_tx_power_dbm 2.0-20.0 in quarter-dB steps; firmware writes CRLF, UTF-8,
  no BOM (a BOM makes the first key unknown); unknown keys ignored; missing keys appended by
  the firmware; user lines never rewritten. Ranges: udp_port 1-65535, device_id 0-255,
  source_id 0-1, diag_log_enabled/stream_enabled 0/1, low_batt_mv 2500-4200,
  accel_fs_g {2,4,8,16,32}, gyro_fs_dps {125,250,500,1000,2000,4000},
  batt_cal_*_mv 0 or 2500-4500.
- LOG_NNNN.BIN (format_version 1, frozen): 512 B header (magic 0x534B594E "NYKS" u32 LE,
  format_version u16, header_size u16, fw_version[16], device_id, source_id, sensor_count,
  fifo_watermark, odr_hz u32, accel_fs_g u16, gyro_fs_dps u16, accel_scale f32,
  gyro_scale f32, session_id u32, boot_esp_us u64, utc_valid, reserved, CRC32-IEEE over
  bytes 0..507 at 508); then 4096 B blocks: 32 B header (magic 0xB10C u16, type u8: 0 IMU,
  1 TIME_SYNC, 2 SESSION_END, sensor_id u8 (1 thigh, 2 shin), seq u32 global, base_ts_us
  u64, sample_count u16 <= 290, flags u16 (bit0 FIFO overflow, bit1 ts clamped), crc32 u32
  over the whole block with this field zeroed, reserved u64) + 14 B samples {dt_us u16, ax
  ay az gx gy gz i16}. TIME_SYNC payload at 32: esp_us u64, unix_us u64, source u8.
  1.1.0 files are exactly 512 MiB preallocated; a power-cut one has a 0xFF (sometimes
  0x00) tail and a 3584 B trailing partial block; 1.2.0 appends (512 + 4096 n) and rotates
  at 2 GiB with seq continuing across files. NNNN is a persisted NVS counter (never reused
  after deletion). FAT mtimes are a fixed 1979 date: useless.
- LOG_NNNN.TXT: only when diag_log_enabled=1; same NNNN as the session's FIRST BIN; not
  rotated; LF; `#` header incl. a `# cfg:` line with dev/src; ends `session NNNN end`.
- Chrome: showDirectoryPicker needs a secure context + user gesture; Chrome 122+ offers
  "Allow on every visit"; handles are storable in IndexedDB (re-check permission on load);
  createWritable writes `<name>.crswap` beside the target and swaps on close (ignore
  `*.crswap`; an orphan is harmless); close() may run Safe Browsing on large files.
  Chromium's blocklist has no drive-root entry (Windows: Program Files, Windows, AppData;
  macOS: /System/Volumes, /Applications). ESP32-S3 USB is full-speed: ~1 MB/s, so
  512 MiB ~ 8-9 min, 2 GiB ~ 35-40 min.
- TypeScript 5.6 lib.dom already types FileSystem*Handle and FileSystemWritableFileStream;
  add lib "DOM.AsyncIterable" for `dir.values()`; declare `showDirectoryPicker`,
  `queryPermission`, `requestPermission` locally.

## 4. Change-set 1 (CS1): config editor + verified transfer

### 4.1 Frontend layout (`frontend/src`)

Pure modules (vitest, node) unless marked browser.

| File | Responsibility |
|---|---|
| `lib/storage/fsa.d.ts` | `declare global` for `Window.showDirectoryPicker(opts)`, `FileSystemHandle.queryPermission/requestPermission`, `DirectoryPickerOptions`. |
| `lib/storage/io.ts` | Interfaces: `ByteSource {size; read(off,len): Promise<Uint8Array>}`, `ByteSink {write; close; abort}`, `DirLike {list(); open(name); create(name); remove(name); subdir(name, create); exists(name)}`; `readChunks(src, chunkBytes, signal)` async generator (slice-based; first chunk 512 + n*4096 so blocks rarely straddle). |
| `lib/storage/fsa.ts` (browser) | `isSupported()` (`'showDirectoryPicker' in window && isSecureContext`), `pickDirectory('sleeve'\|'dest')` (mode readwrite, id per purpose), `ensurePermission(h)`, `fsaDir(handle): DirLike` adapter (getFile().slice for reads, createWritable for writes, removeEntry). |
| `lib/storage/handleStore.ts` (browser) | IndexedDB `hippos-storage/handles`: `saveHandle(key,h)`, `loadHandle(key)`, `clearHandle(key)`. |
| `lib/storage/memDir.ts` | In-memory `DirLike` for tests, with injectable read/write/remove failures ("unplug"). |
| `lib/storage/crc32.ts` | Table CRC32 (poly 0xEDB88320 = zlib = firmware crc32_ieee): `crc32Init/Update/Final`. |
| `lib/storage/binFormat.ts` | Format constants; `parseFileHeader(bytes)`; `readBlockHeader(view, off)`; `blockCrcOk(block)` (incremental over [0,20) + 4 zero bytes + [24,4096), no copy); `classifyBlock(block): 'valid'\|'bad'\|'unused'` (all 0x00 or all 0xFF). |
| `lib/storage/scanner.ts` | `class BlockScanner { push(chunk); finish(): ScanResult }` with a 512 B header buffer and a 4096 B carry; scans the WHOLE file (does not stop at a bad block; records `firstBad`). `ScanResult = {header, blocks, valid, bad, unused, firstBad, trailingBytes, seqGaps, cleanEnd, syncBlocks[], samples{1,2}, fifoOverflows}`; `scanResultsEqual(a,b)`. Shared by CS1 verify and CS2 decode. |
| `lib/storage/logNames.ts` | `LOG_NAME_RE = /^LOG_(\d{4})\.(BIN\|TXT)$/i`, `isIgnoredEntry(name)` (`*.crswap`, `System Volume Information`, dotfiles), `findConfigEntry(names)` (case-insensitive CONFIG.TXT, returns actual name), `groupSessions(entries)`, `dupName(name, n)` -> `LOG_0010-2.BIN`. CONFIG.TXT can never match the transfer allowlist by construction. |
| `lib/storage/configSchema.ts` | The 14 keys in firmware order with spec `{kind: 'str'\|'int'\|'bool'\|'enum'\|'ip'\|'qdbm', min, max, allowed, maxBytes, group: 'basic'\|'advanced', effect: 'boot'\|'session'}`; `validateValue(key, text)`; `encodeValue(key, v)` (plain decimal, quarter-dB text like the firmware's `write_qdbm_`, canonical dotted quad); `unitIdFrom(dev, src)`; `UNIT_ID_RE` mirroring `kinds._UNIT_ID_RE`. Firmware limits are format facts here, not tunables. |
| `lib/storage/configFile.ts` | Byte-level model. `splitLines(bytes)`; `interpretAsFirmware(bytes): FirmwareView` (emulates trim, comments, first-`=` split, last-wins, unknown keys, malformed lines, the 159-byte fgets split, BOM detection, base-0 integer reading so the UI shows what the firmware would use); `applyEdits(bytes, edits): Uint8Array` (replace only the value span of the LAST occurrence; append `key=value\r\n` for missing keys, preceded by `\r\n` if the file lacks a final EOL, like the firmware's append; untouched bytes spliced verbatim, invalid UTF-8 survives; never a BOM, never inline comments); `verifyReadback(bytes, intended)`. |
| `lib/storage/transfer.ts` | The engine (4.3). |
| `lib/storage/pageState.ts` | `useReducer` reducer, actions, selectors (`canSave`, `canStart`, `udpTargetStatus`). |
| `lib/storage/copy.ts` | `STORAGE_COPY` as const with every user-facing string; added to `lib/text.test.ts` (both lists). |
| `lib/api.ts` | `UdpTarget {ip: string\|null; port: number; source: 'env'\|'dns'\|'unresolved'}`, `fetchUdpTarget()`. Existing `fetchUnits`, `patchUnit` reused. |
| `lib/config.ts` | `STORAGE_READ_CHUNK_BYTES = 4 MiB`, `STORAGE_RATE_EWMA_ALPHA = 0.2`, `STORAGE_EXPECTED_BYTES_PER_S = 1e6` (initial ETA), `STORAGE_UDP_TARGET_STALE_MS = 60_000`. |
| `pages/Storage.tsx` (browser) | Route `/storage`; owns reducer, queries (`['units']`, `['devices']`, `['udp-target']`), handles, mutations; composes components. Unsupported/insecure context -> explanatory state. |
| `components/storage/{DrivePanel,ConfigEditor,UdpTargetRow,LogList,TransferPanel,EjectNotice}.tsx` (browser) | Presentational; reuse `.segmented/.segment/.is-active/.rig-action/.notice/.chip.flag-*/.data-table/.card/.empty-state`; new `.storage-*` rules appended to `app.css`. Use `--ink`/`--ink-2` (never the undefined `--ink-1`). `role="alert"` on error lines, `aria-live="polite"` on progress. |
| `main.tsx`, `components/Sidebar.tsx` | `<Route path="/storage">` inside the RequireAuth layout; NavLink "Sleeve storage" with lucide `HardDrive` under Command. |
| `tsconfig.json` | add `"DOM.AsyncIterable"` to `lib`. |

### 4.2 Config editor behaviour

- Open drive -> validate root has CONFIG.TXT (case-insensitive) -> read bytes -> `interpretAsFirmware`
  -> identity `u<dev>-<src>` -> lookup in `['units']`/`['devices']` for the soldier name.
- Basic view (decision F): fields with help text; device_id explained as the sleeve's number;
  warn when `u<dev>-<src>` already exists in `/api/units` ("a sleeve with this number and leg
  is already known to the dashboard; if that is this sleeve, ignore"). source_id toggle
  Left/Right. Password masked with a show toggle. Empty wifi_ssid is BLOCKED with an
  explanation (firmware keeps its compiled default; WiFi cannot be disabled from the file).
  Empty wifi_password: warn only (open networks exist; empty also keeps the default).
- UDP row (decision G): `Streams to <udp_ip>:<udp_port>` + "(this dashboard)" when equal to
  udp-target, else "(not this dashboard)" + button "Point at this dashboard" (disabled when
  `ip` is null with the reason). Advanced holds the editable fields plus the same button.
- Advanced (decision E): collapsed `<details>`-style section; opening shows the warning
  ("Changing these can make the sleeve misbehave: stop logging, mis-scale its sensors or
  stop it streaming") and requires the acknowledgement checkbox once per page session.
- Validation mirrors firmware ranges; byte lengths count UTF-8 bytes; leading/trailing
  whitespace in text values is rejected (firmware trims it); lines that would exceed 159
  bytes are rejected; existing suspicious values (leading-zero numbers, BOM, duplicate keys,
  over-long lines) are surfaced as notices with the value the firmware actually uses. A BOM
  gets an explicit one-click "Repair" (strip) rather than a silent rewrite.
- Save = `applyEdits` -> write via createWritable -> re-read -> `verifyReadback` -> success
  panel: "Eject the HIPPOSDATA drive, then unplug the cable. The sleeve re-reads the file
  about 2 s after unplugging" + which keys need a power cycle (wifi_*) vs next session.
- Post-save (decision I): if accel_fs_g/gyro_fs_dps changed and the unit exists -> `patchUnit`
  and invalidate `['units']`, `['devices']` and `RIG_QUERY_KEYS` for that rig (pattern
  RigControls.tsx:47-55); show "Dashboard full scale for u1-0 updated to match". If
  device_id/source_id changed -> notice "This sleeve will appear as a new soldier (u<new>);
  pairing, side and history stay with u<old>".

### 4.3 Transfer engine (`lib/storage/transfer.ts`)

`runTransfer(items, deps: {card: DirLike, dest: DirLike, keepCopies, now}, {signal}):
AsyncGenerator<TransferEvent, Summary>`; the page consumes it with `for await` and dispatches
each event. Reads are slice-based with explicit backpressure (await write before next read).
Cancellation: `signal.throwIfAborted()` between chunks; abort the sink (Chrome discards the
.crswap); item `cancelled`; card untouched.

Per BIN item: `probe` (same-name file in dest? if same size, CRC32 it locally) -> `copy`
(card -> `sleeve-u<dev>-<src>/raw/<name>` or `dupName`, running CRC32 + BlockScanner,
`bytesRead === size`, `sink.close()` must resolve) -> `verify` (re-open the LOCAL copy: length,
CRC32 and `scanResultsEqual` must match what was read from the card; mismatch -> remove the
bad local copy, error `verify-mismatch`, card untouched) -> `dedupe` (if a pre-existing local
file had the same size and CRC, drop the fresh duplicate and report `already-transferred`)
-> `delete` (only when copy+verify passed, `!keepCopies`, not aborted; failure = `card-delete`,
the copy stays valid). TXT items: copy, then byte-compare local vs card (small), then delete.
Folder identity for a TXT without its BIN: the TXT `# cfg:` line, then CONFIG.TXT.

Error codes: `permission`, `card-read` (NotReadable/NotFound mid-copy = unplugged),
`dest-write` (quota, NotAllowed, Safe Browsing abort on close), `verify-mismatch`,
`card-delete`, `aborted`. Events: `item-start`, `progress {id, phase, bytesDone, bytesTotal,
bytesPerS, etaMs}` (EWMA), `item-done {id, result, deleted, scan}`, `item-failed`, `done`.
Runs on the main thread (CPU cost is negligible against 1 MB/s); no DOM dependency, so it can
move to a worker later. The listing reads only each BIN's 512 B header (fw, dev/src, full
scale, session id) plus sizes; duration is not shown until CS2 (mtimes are useless).

UI: file list with checkboxes (all selected by default), sizes, session id, header facts;
"Choose destination folder" (persisted); "Keep copies on the sleeve" checkbox; Start/Cancel;
per-file status lines; overall progress with rate and ETA; a persistent "Do not unplug the
sleeve while a transfer is running" notice; failures never delete anything.

### 4.4 Backend change A: side defaults from wire source_id (decision H)

- `common/kinds.py`: `side_for_source(source_id) -> 'left'|'right'` (= `SIDES[source_id]`);
  `UnitConfig.default()` derives `side` from `parse_unit_id(unit)`. Without this, ingest's
  default config for a brand-new sleeve (`ingest/unit_config.py:50-56`) would differ from the
  registered row and the mirror publish would hard-reset the rig two seconds after it appears.
- `api/unit_mirror.py:137-145`: INSERT `side` = `side_for_source(source_id)` instead of NULL.
  No migration (NULL stays allowed; `PATCH {"side": null}` still works).
- Tests: `tests/test_units.py` registration test expects `left` for `u30-0`, `right` for
  `u31-1`, mirrored JSON equal to the new default; new `test_register_side_follows_wire_source`;
  `test_kinds.py` / `test_unit_config.py` default-side assertions updated.
- Docs: PLAN_unilateral decision G amendment (dated), UIUX s4 and s11 ("never claim a leg the
  operator has not set" -> the leg comes from the sleeve's own source_id, set on the card or
  in Sleeve storage; the operator may override), BACKEND_SCHEMA s1 note, docstrings.

### 4.5 Backend change B: `GET /api/config/udp-target` (decision G)

- New `api/routes/config.py`, `router = APIRouter()`, included in `main.py` with the guard.
  Response `{"ip": str|null, "port": settings.udp_port, "source": "env"|"dns"|"unresolved"}`:
  `settings.udp_public_ip` when set, else `asyncio.wait_for(loop.getaddrinfo(settings.domain,
  None, family=AF_INET), 3)`; failure -> `ip: null` with 200.
- `common/config.py`: `udp_public_ip: str = ""` validated with `ipaddress.IPv4Address` when
  non-empty. `.env.example`: `UDP_PUBLIC_IP=` under UDP_PORT: "IPv4 the sleeves stream to;
  blank = resolve DOMAIN. Set it explicitly if DOMAIN is behind a proxy/CDN (the resolved
  address would then not be this box)".
- Tests: `tests/test_routes_config.py` (bare app, no DB: env override -> env; monkeypatched
  resolver -> dns; raising resolver -> null); `test_config.py` rejects `UDP_PUBLIC_IP=garbage`;
  `test_auth.py` 401 for the new path.
- Docs: BACKEND_SCHEMA s3 row; TRD s7 key; README config.

### 4.6 CS1 tests

Vitest: `crc32`, `binFormat` (real fixture header, CRC rejection, classify), `scanner` (chunk
sizes 1, 100, 4095, 4096, 4 MiB+512 give identical results; partial trailing block; 0xFF and
0x00 tails; bad block mid-file), `logNames`, `configSchema` (multibyte SSID byte limits, octal
trap never emitted, qdbm text, dotted quad), `configFile` (firmware sample CONFIG.TXT as a
fixture: CRLF preserved, last duplicate edited, comments/unknown keys/invalid UTF-8 untouched,
append with and without final EOL, no BOM ever, 159-byte split emulation, `;` comments, inline
`#` kept in values, empty SSID blocked), `transfer` with `memDir` (happy path deletes;
keepCopies; verify mismatch keeps card and removes local; read failure mid-copy; abort;
duplicate same CRC -> already-transferred; different CRC -> suffixed; delete failure reported),
`pageState`, `text.test.ts` with `STORAGE_COPY`, `api.test.ts` udp-target line.
Fixtures under `frontend/src/lib/storage/fixtures/`: the firmware sample CONFIG.TXT, a
canonical 1.2.0-generated CONFIG.TXT, `LOG_0010.head64.bin` (first 512 + 64*4096 bytes of the
real sample, 262,656 B).
Pytest: 4.4 and 4.5. Build gate: `cd frontend; npm run build` (tsc -b) and `npm test`;
`uv run pytest backend/tests/` with the debug compose profile up.

Manual real-drive checklist (user, Chrome, https://DOMAIN or `npm run dev` on localhost):
picker offers the drive root on Windows and what `handle.name` shows (UI never relies on it);
"Allow on every visit" persists across reload and re-plug; CONFIG.TXT edit -> `.crswap`
visible during write -> eject -> unplug -> the next LOG_NNNN.TXT `# cfg:` line shows the new
values; throughput and ETA on a 512 MiB file; delete then re-plug shows the file gone; unplug
mid-copy leaves the card intact and nothing deleted; Firefox/insecure context shows the
unsupported state; Edge works. Dev without hardware: pick any local folder holding copies of
CONFIG.TXT + LOG_0010.{BIN,TXT}.

## 5. Change-set 2 (CS2): CSV + summary (planned now, built after CS1 ships)

- `workers/convert.worker.ts` created with `new Worker(new URL(..., import.meta.url), {type:
  'module'})` (Vite 5 native). Protocol: main -> `{type:'convert', jobId, raw:
  FileSystemFileHandle, outDir: FileSystemDirectoryHandle, stem}` / `{type:'cancel', jobId}`
  (handles are structured-cloneable and carry the granted permission); worker -> `progress
  {phase: 'syncs'|'csv'|'stats'|'write', blocksDone, blocksTotal, rows}`, `done {meta,
  summary, outputs}`, `error {code, message}`. Worker `self` typed via a local interface to
  avoid the DOM/WebWorker lib clash in the single tsconfig.
- `lib/storage/convert/{decode.ts, csv.ts, meta.ts, stats.ts, pyFormat.ts, binEncode.ts}`,
  all pure. decode mirrors bin2csv: sync pre-pass; bad blocks counted and skipped (scan
  continues); trailing partial block ignored; seq gaps over all valid blocks; END sets
  clean_end and continues; count > 290 counted valid then skipped; flags&1 -> overflow; sync
  pick = latest esp_us <= base else the FIRST sync (extrapolates backwards); unix_us empty
  only when the file has no sync; u64 via BigInt with a < 2^53 guard.
- CSV: header `device_id,sensor_id,seq,t_us,unix_us,ax,ay,az,gx,gy,gz`, LF, block order;
  `formatScaled(count, fs, d)` exact integer arithmetic (num = |count|*fs*10^d; q, r vs 32768;
  half-to-even), two 65536-entry string tables per file; written through createWritable in
  2 MiB chunks via `TextEncoder.encodeInto`. `<stem>.meta.json` = bin2csv keys (fw, device_id,
  source_id, sensor_count, odr_hz, accel_fs_g, gyro_fs_dps, accel_scale, gyro_scale,
  session_id, source_file, units, blocks_valid, blocks_bad, seq_gaps, fifo_overflows,
  clean_end, time_sync[], rows{}) plus dashboard extras (`unused_tail_blocks`, `first_bad`).
- Summary `LOG_NNNN_summary.txt` (decision M): file header and decoder verdict lines;
  inventory per sensor (placement thigh/shin, side from the dashboard when known); "Sampling
  and data continuity" (nominal = 1e6/median(dt) via exact integer histogram, delivered =
  N/span, at-nom share within +-20 %, gaps > 1 ms, lost s, max gap ms, clipped counts at
  >= 0.9999 rail); "Motion-agnostic noise and bias" (22 ms detrended windows = 0.44/20 Hz
  inside gap-free bursts, residual std, median over windows, accel divided by resting |a|;
  rest orientation per-axis medians via i16 histograms, gyro bias in mdps, tilt). Tunables
  `STORAGE_NOISE_F_CUT_HZ = 20`, `STORAGE_GAP_US = 1000`, `STORAGE_TS_OUTLIER_US = 1e6` in
  config.ts. `pyFormat.ts` reproduces Python `%.Nf` incl. half-even `.0f` ties and `-0`.
- Outputs to `<dest>/sleeve-u<dev>-<src>/` next to `raw/` (decision K); the page shows the
  summary text after conversion; raw kept.
- Goldens: vendor `bin2csv.py` and `sensor_stats.py` verbatim into `scripts/kneesleeve/`
  (with a README naming the source path and date) so `scripts/storage_goldens.py` can
  regenerate `<fixture>.csv.sha256`, `.meta.json` and summary sections with the repo venv
  (numpy present; pandas/matplotlib via `uv run --with`). Fixtures: `LOG_0010.head64.bin`
  plus synthetic files from `binEncode.ts` (sync blocks, END mid-file, count > 290, bad CRC,
  overflow flag, 0xFF tail, partial tail), written once by a `STORAGE_WRITE_FIXTURES=1` test.
  CSV compared by sha256, meta parsed-equal, summary numeric columns with tolerance.

## 6. Work packages and order

CS1: WP1 pure core (io, crc32, binFormat, scanner, logNames, configSchema, configFile,
memDir + tests) -> WP2 transfer engine + tests -> WP3 backend A + B + tests (independent of
WP1/2; can run in parallel) -> WP4 browser layer + page + components + copy + CSS + routing
-> WP5 build/test gates, manual checklist handed to the user -> WP6 docs (Phase E).
CS2: WP7 decode/csv/meta/pyFormat + goldens -> WP8 stats + summary -> WP9 worker + page
integration -> WP10 docs.

Recommended commit points: after WP2 ("storage: pure CONFIG.TXT model, block scanner and
transfer engine with tests"), after WP3 ("api: side defaults from wire source_id;
/api/config/udp-target"), after WP5 ("frontend: Sleeve storage page - config editor and
verified log transfer"), after WP6 ("docs: sleeve storage as built"). CS2 likewise.

## 7. Docs to update (Phase E, same change-set)

`docs/UIUX.md` (s1 routes + sidebar, new s15 "Sleeve storage", s4/s11 side-default amendment),
`docs/APPFLOW.md` (new s1.5 flow), `docs/BACKEND_SCHEMA.md` (s3 route row, s1 side default
note, "s4 Redis unchanged"), `docs/TRD.md` s7 (UDP_PUBLIC_IP), `README.md` (new "Sleeve storage
(USB)" subsection, config key, browser requirement), `docs/PLAN.md` + `docs/IMPLEMENTATION_PLAN.md`
(change-set entries MSD-01 / MSD-02), `PLAN_unilateral_devices.md` (decision G amendment,
as-built item), `.env.example`, the project context file (now agent-docs/00_PROJECT_CONTEXT.md: State,
Config tiers, storage section, drift list
incl. the D38/D39 correction). Firmware repo (not ours; report to the user, do not edit):
README s1.4 and FLASHING.md s3 show inline comments the parser does not strip; the generated
comment "Leave wifi_ssid empty to keep the radio off" is wrong; D33 text says INQ rev 1.1.

## 8. Risks

- Drive-root picking and `handle.name` on Windows are unverified until the manual check.
- Safe Browsing on `close()` for multi-hundred-MB writes may delay or abort (handled as
  `dest-write`; the card is never touched before close resolves).
- DOMAIN behind a proxy/CDN makes DNS resolution wrong for UDP: UDP_PUBLIC_IP override + docs.
- Persisted permission is per origin/profile; a user on a new PC re-picks the drive (cheap).
- 1.1.0 stale-data tails could contain old valid blocks after unused blocks; the scanner
  classifies every block and reports `firstBad`; CS2 mirrors bin2csv exactly, so results equal
  the Python tool's.

## As built
### As built, change-set 1 (2026-09-23)

CS1 shipped in six work packages (pure core, transfer engine, backend, browser layer and
page, review fixes, docs). Deviations from sections 4 and 5 above, code wins:

1. Module names and shapes. The FSA global augmentation is `lib/storage/fsa.globals.d.ts`
   (a `fsa.d.ts` next to `fsa.ts` is silently dropped by tsc). `ScanResult` carries extra
   `firstUnused` and `badBlocks` (bad-block indices, capped at
   `STORAGE_BAD_BLOCK_CONFIRM_MAX` = 32); `samples` has an index signature. `io.ts` also
   exports `chunkPlan`, `concatBytes`, `bytesEqual`, `errorName`, `errorDetail` (the one
   copy of the DOMException-name mapping). Progress events add `runBytesDone` /
   `runBytesTotal`; only copy-phase bytes feed the rate meter. `config.error` is
   `{code: 'write'|'readback', detail?}`. `dest` has a third status `reconnect`.
   `RIG_QUERY_KEYS` moved from RigControls.tsx to `lib/rig.ts`. New tunables in config.ts:
   `STORAGE_TXT_CFG_PROBE_BYTES` = 4096 and `STORAGE_BAD_BLOCK_CONFIRM_MAX` = 32.
   `fixtures/node-fs.d.ts` is a 6-line ambient `node:fs` declaration (no @types/node in the
   repo); `fixtures/.gitattributes` keeps the byte-exact fixtures out of autocrlf. The LOG_0010
   samples live in `...\Knee Sleeve\FW1.1.0 Sensor Quality\`, not `...\Knee Sleeve\`.
2. Editor semantics. `validateValue` rejects `key=value` at 159 bytes or more (one byte
   conservative). `applyEdits` replaces from after `=` to the end of the trimmed value, so
   `key = old` becomes `key =new`; over-long physical lines are patched whole and
   `verifyReadback` is the safety net. Missing keys are appended in firmware key order.
   `wifi_password` may be empty (warned), `wifi_ssid` may not. Leading-zero integers are
   rewritten as the decimal the sleeve already reads on any save and by themselves enable
   Save; a value the sleeve cannot read at all ("08") blocks Save until replaced.
   "Point at this dashboard" is its own action and bypasses the Advanced acknowledgement
   (decision G puts it in the basic view). The leg toggle shows Left when `source_id` is
   missing (the firmware default) and writes an explicit `source_id=0` when clicked.
   The identity line and a per-row Status column were added to the log table.
3. Transfer engine. A delete failure is `item-failed {code: 'card-delete'}` with the verified
   copy kept and counted in `summary.failed`; if removing a fresh duplicate fails during
   dedupe the item is reported as `copied` under its dup name. An abort during verify removes
   the unverified copy; during dedupe/delete the item completes with `deleted: false`.
   Two hardenings from the review: (a) every block the scan called bad is re-read from the
   card and compared byte for byte with the local copy before the delete (`confirmBadBlocks`),
   so a transient read error can never pass as on-card corruption; (b) an abandoned run
   (page unmount, `generator.return()`) aborts the open writable and removes an unverified
   copy via `finally`, so no `.crswap` or partial file lingers.
4. Backend. `UnitConfig.default()` derives the side from the unit id (registration and ingest
   must agree or a new sleeve would hard-reset on its first mirror publish). Migration
   `006_sleeve_side_backfill.sql` seeds legacy UNPAIRED `side IS NULL` rows from
   `wire_source_id` (data change, NULL to a value only, reversible per unit via PATCH; paired
   and explicit sides untouched). `/api/config/udp-target` caches resolutions per host for
   `UDP_TARGET_CACHE_S` = 60 s, unresolved answers included, because `getaddrinfo` cannot be
   cancelled and would otherwise pin an executor thread per request while DNS is down.
   The `UDP_PUBLIC_IP` validator strips surrounding whitespace. Six existing unit tests
   needed decision-H updates (hosts now register with a side).
5. Copy. `STORAGE_COPY` keeps composite messages as `{slot}` templates inside the table so
   `text.test.ts` walks them; `lib/storage/format.ts` formats bytes, rate and ETA.

Firmware-repo findings passed to the user, not edited here: README s1.4 and FLASHING.md s3
show inline `# comments` the parser does not strip (they corrupt `udp_ip` and reject the
full-scale values); the generated comment "Leave wifi_ssid empty to keep the radio off" is
wrong (empty keeps the compiled default); ARCHITECTURE D33 still says INQUIRY rev 1.1 (code
says 1.2) and D17 still says 512 MB rotation (code: 2 GiB, noted in D38).

#### Verification actually run (2026-09-23)

| Gate | Result |
|---|---|
| `cd frontend; npx tsc -b` | exit 0 |
| `cd frontend; npm test` | 20 files, 206 tests passed (was 10 / 81) |
| `cd frontend; npm run build` | exit 0 (pre-existing echarts chunk-size warning only) |
| `uv run pytest backend/tests/` with the debug DB up | 361 passed, 1 failed: test_biomech.py::test_bench_compute_five_devices, a machine-speed guard (3.58-3.76 ms/tick measured vs the 3 ms guard, three runs, one in isolation). biomech.py, scaling.py, the test and conftest are byte-identical to HEAD and the same test passed twice earlier the same day (337 and 359 passed runs), so it is environmental, not this change-set. Re-run on an idle, mains-powered machine before release |
| Plain-ASCII check of every new copy/source file | clean |
| Real-drive picker flow | NOT run (needs a browser and a sleeve): manual checklist below |

#### Manual checklist (Chrome; `npm run dev` on localhost or https://DOMAIN)

1. Sidebar shows "Sleeve storage" under Command; `/storage` renders. Firefox or plain http
   shows the unsupported message instead of the button.
2. "Open sleeve drive": a folder without CONFIG.TXT is refused with the reason; the
   HIPPOSDATA root (or any folder holding copies of CONFIG.TXT + LOG_0010.BIN/.TXT) shows
   the identity line, fields and the log table. Note what the picker shows for the drive
   root on Windows.
3. Reload: with "Allow on every visit" the drive re-opens by itself; otherwise "Reconnect
   sleeve drive" works with one click.
4. Editor: Save enables only when something changed and validates (device_id 300 shows the
   range message); empty WiFi network is refused; Advanced is disabled until "I understand".
5. UDP row reflects GET /api/config/udp-target; "Point at this dashboard" fills the target.
6. Save: a `.crswap` appears briefly beside CONFIG.TXT, then the green "Saved. Now eject..."
   panel. Eject, unplug, wait ~2 s, re-plug: the newest LOG_NNNN.TXT `# cfg:` line carries the
   new values. In Notepad only the edited values changed; CRLF kept; no BOM.
7. Transfer: choose a destination, start; per-row status, run bar, rate and ETA, the
   "Do not unplug" notice; files land in `<dest>/sleeve-uN-M/raw/`; re-running reports
   "Already transferred"; "Keep copies" leaves the card untouched; unplugging mid-copy leaves
   the card intact and no partial local file. Time a 512 MB file (expect ~9 min).
