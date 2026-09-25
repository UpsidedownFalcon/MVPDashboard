# PLAN_csv_summary: sleeve storage change-set 2, CSV + summary per transferred log

Status: decisions locked with the user 2026-09-25 (sections 3 and 4); building on branch
`csv-summary` (from `main` f5b78e0). Derived from `02_PLAN_msd_management.md` section 5,
which stays as history; its decisions A-N are not reopened. The "As built" section at the
end records deviations and the verification actually run. Code wins over this file.

## 1. Context

Change-set 1 (CS1, shipped) copies `LOG_NNNN.BIN` / `.TXT` from a sleeve's USB drive to
`<dest>/sleeve-u<dev>-<src>/raw/` with a verified copy before the card-side delete. This
change-set (CS2) turns each verified BIN into, next to `raw/`:

- `<stem>.csv`, byte-exact with `bin2csv.py` (decision N);
- `<stem>.meta.json`, the same keys as `bin2csv.py` plus two dashboard extras;
- `<stem>_summary.txt`, a port of the text sections of `sensor_stats.py` (decisions B, M),
  also shown in the page.

`<stem>` is the raw file's name without extension (`LOG_0010`, or `LOG_0010-2` for a
duplicate-named copy). Everything runs in the browser, in a Web Worker, on the user's PC:
nothing is uploaded and nothing on the PC is ever deleted (decisions A, K).

Reference material (read-only, vendored verbatim into `scripts/kneesleeve/`):
`C:\Users\bhavy\GitHub_HXSKL\NYKnicks\Knee Sleeve\{bin2csv.py,sensor_stats.py}`
(sha256 92B272B3... and 94D79F40...), sample data in `...\Knee Sleeve\FW1.1.0 Sensor
Quality\` (LOG_0010.BIN 512 MiB, its CSV sha256
A033F3ACB89853044774018F16B91D0F5BD12B881D24A788C9C74126EDF1EB2F), and the firmware's
`src/log_format.h` (format_version 1, unchanged by this work).

## 2. Facts that bind the port (verified 2026-09-25 against the code and current docs)

bin2csv.py (220 lines):
- Header: struct `<IHH16sBBBBIHHffIQB`; magic, `format_version == 1`, `header_size == 512`
  and CRC32 over bytes 0..507 are checked, else ValueError. The float32 scales are read from
  the header and used as-is (`count.astype(float64) * scale`); they equal fs / 32768.
- Sync pre-pass over the whole file: every block with magic 0xB10C, type 1 and a good CRC
  gives `(esp_us, unix_us)`; the list is sorted. `pick_sync(base)` = the latest sync with
  `esp_us <= base_ts_us` of the BLOCK, else the FIRST sync (extrapolates backwards).
  `unix_us = t_us + (unix - esp)`; the column is empty only when there is no sync at all.
- Main loop per 4096 B block: a trailing partial block is ignored (loop ends); bad magic or
  CRC -> `blocks_bad++`, skipped, scan continues (so a preallocated 0xFF / 0x00 tail counts
  as bad); otherwise `blocks_valid++`; one global `last_seq` over ALL valid blocks (IMU,
  sync, end): `seq != last_seq + 1` is a gap; SESSION_END sets `clean_end` and continues;
  non-IMU skipped; `sample_count > 290` -> skipped but already counted valid, no overflow
  count; `flags & 1` -> `fifo_overflows++` (bit 1, TS_CLAMPED, ignored); rows[sid] += count.
- CSV: header `device_id,sensor_id,seq,t_us,unix_us,ax,ay,az,gx,gy,gz`, LF, rows in block
  order, `t_us = base + dt` (integer), accel `%.6f`, gyro `%.4f`, Python rounds the exact
  binary value half-to-even. Ties are real: -694/1024 prints `-0.677734` (toFixed says
  `-0.677735`). A file with no IMU block gets no CSV and no meta at all.
- meta.json: `json.dumps(meta, indent=1)`, keys fw, device_id, source_id, sensor_count,
  odr_hz, accel_fs_g, gyro_fs_dps, accel_scale, gyro_scale, session_id, source_file, units,
  blocks_valid, blocks_bad, seq_gaps, fifo_overflows, clean_end, time_sync[{esp_us,unix_us}],
  rows{"1":n,...} (first-seen order); CRLF and no trailing newline on Windows.

sensor_stats.py (1625 lines), stats mode, `--no-describe --no-plot`:
- Reads the CSV (dtypes u8/u8/u32/u64/f64, signal columns inferred float64) and the
  `.meta.json` sidecar, never the .TXT. All statistics use the CSV's ROUNDED decimals.
- Per (device_id, sensor_id) in file order: `good = |ts - rolling_median(ts, 11, centered,
  min_periods=1)| <= 1e6`; good rows stable-sorted by ts; `dt = diff(ts)` (int);
  `dt_med = median(dt)` (mean of the two middle values when even); `at_nominal = mean(0.8
  dt_med <= dt <= 1.2 dt_med)`; `duration_s = (ts[-1] - ts[0]) / 1e6`; `n_gaps = sum(dt >
  1000)`; `gap_time_s = sum(dt[dt > 1000]) / 1e6`; `max_gap_ms = max(dt) / 1e3`;
  `n_clip = rows with any |axis| >= 0.9999 * 32767 * fs / 32768` (accel and gyro
  separately); `baseline = median(|a|)`.
- Noise: `window_us = 0.44e6 / f_cut` (22 ms at 20 Hz); `n_win = max(20, round_half_even(
  window_us / dt_med))`; gap-free runs split where `dt > 1000`; each run cut into
  `size // n_win` whole windows from the run start; per window `x = t - mean(t)`, `ym =
  mean(y)`, `slope = sum(x (y - ym)) / sum(x^2)`, `resid = y - ym - x slope`, `std(resid,
  ddof=0)`; result = median over windows per axis; accel reported as `/ baseline * 1000`
  (mg), gyro `* 1000` (mdeg/s); `wins` = accel window count.
- Bias: per-axis median of accel (g) and gyro (`* 1000` mdeg/s) over all good rows;
  `tilt = degrees(arccos(clip(|a_z| / |a|, -1, 1)))`.
- Output lines, headings, widths, footnotes: quoted verbatim in section 4.7.
- Placement: no built-in mapping (`--map DEV:SID=SIDE,SEGMENT` gives "left femoral
  (thigh)"); without it every placement cell is `placement not set` (28 chars in a 29 wide
  left-aligned column, so lines carry trailing spaces).
- Both tools run unmodified under the repo venv (numpy 2.5.1, pandas 3.0.5, no warnings);
  sensor_stats needs `uv run --with matplotlib`. bin2csv prints a plus-minus sign and an em dash in
  cp1252 when redirected on Windows: the golden script sets `PYTHONUTF8=1`.

Platform (sources in the Phase B report, all current docs):
- `FileSystemHandle` is `[Serializable]`; handles cross `postMessage` within an origin and
  keep their permission state. `createWritable()` is exposed to workers; Chromium writes a
  `.crswap` beside the target and moves it into place on `close()`, so an aborted or
  interrupted conversion leaves no partial output. No quota on user-picked directories,
  but close() needs up to 2x the file size free on disk. Sync access handles are OPFS-only.
- Vite 5: `new Worker(new URL('./x.ts', import.meta.url), { type: 'module' })` is bundled;
  `worker.format` defaults to `iife`, which is fine while the worker has no dynamic import.
- `showDirectoryPicker`: Chrome/Edge/Opera 86+ only. Chrome is NOT installed on this PC;
  Edge 153 is. The real run therefore happens in Edge (same Chromium engine).
- Python `format(x, '.6f')` is correctly rounded half-to-even on the exact binary value and
  prints `-0.0000` for -0.0 and for negatives that round to zero; JS `toFixed` rounds ties
  away from zero and prints `0.0000` for -0.

## 3. Decisions (user, 2026-09-25, locked)

Letters continue 02's table.

| # | Decision |
|---|---|
| O | Raw files that lack outputs (transferred before CS2, or whose conversion failed or was interrupted before a reload) are found by a scan of `<dest>/sleeve-*/raw/LOG_*.BIN` when the destination is chosen or reconnected, and a "Convert missing" button queues them. Not automatic. |
| P | The summary is shown in the page and written as `<stem>_summary.txt`; no download link. |
| Q | Conversion always runs concurrently with the transfer (decision L); no hold control. |
| R | A failed row gets a Retry button that re-runs the whole conversion; outputs are rewritten (they are derived files; raw is never touched); after a reload, "Convert missing" is the retry. |
| S | Placement column uses dashboard wording: `left thigh` / `left shin` (or `right`) when the dashboard knows the unit's side, `thigh` / `shin` when it does not. Sensor 1 = thigh, 2 = shin. |
| T | Two streaming passes per file: a light pre-pass (block CRCs, sync anchors, per-sensor timestamps: outlier filter, dt histogram, continuity figures) then the main pass (CSV, meta, count histograms, noise windows with the exact n_win). Bounded memory; two local reads. |
| U | Summary keeps sensor_stats' headings, columns, footnotes and number labels verbatim (`1)`, `2/3/4)`, `8)`) and a `File: <stem>.csv` line; drops the DATA_DESCRIPTION block, the `5/6)` tap note and the `9)` PDF line. |
| V | A `card-delete` failure (verified copy exists) and an `already-transferred` result are queued like a fresh copy; if all three outputs already exist the row reads "Already converted" and nothing is written. |

Assumptions made by the agent (overridable; deviations go in "As built"):
- A1 The page re-derives the raw file handle from the destination root handle it already
  holds: `dest.getDirectoryHandle(folder)` -> `'raw'` -> `getFileHandle(localName)`, and the
  output directory is `dest.getDirectoryHandle(folder)`. `DirLike` is not changed.
- A2 A TXT is never converted. A BIN with no IMU rows gets a header-only CSV, a meta and a
  short summary (bin2csv writes nothing; documented deviation, not golden-tested).
- A3 The rolling-median outlier filter is applied exactly; the stable sort by timestamp is
  not (streaming). Per-sensor timestamps are non-decreasing by firmware design (the
  monotonic clamp, flag bit 1); a backwards step is counted and, when present, one line
  `  timestamps out of order: N (figures computed in file order)` is appended after the
  continuity footnotes. Not expected on real data.
- A4 Medians are exact: dt over an integer histogram; per-axis accel/gyro over 65536-bin
  count histograms (the count -> CSV value map is monotonic; the even-N average is taken
  over the two middle CSV values as numpy does); |a| by a coarse histogram in pass 1 and
  the exact values of the median bin(s) in pass 2; window residual stds in a growable
  Float64Array sorted at the end. Only floating summation order can differ from numpy.
- A5 Progress: the pre-pass shows "Scanning N %" and the main pass "Converting N %".
- A6 Errors keep the raw file and mark the row; codes `format`, `range`, `read`, `write`,
  `aborted`, `worker` (the worker crashed or the browser has no Worker).
- A7 No backend change. None is needed: the side comes from the page's existing
  `['units']` query.

## 4. Design

### 4.1 Layout (`frontend/src`)

```
lib/config.ts                     + STORAGE_NOISE_F_CUT_HZ, STORAGE_GAP_US,
                                    STORAGE_TS_OUTLIER_US, STORAGE_CSV_WRITE_CHUNK_BYTES
lib/storage/convert/types.ts      contracts (below); ConvertError; outputNames(); stemOf()
lib/storage/convert/decimal.ts    fixedHalfEven(x, d): Python %.Nf, exact, BigInt
lib/storage/convert/scaled.ts     formatScaled (integer arithmetic), buildValueTables
lib/storage/convert/blocks.ts     header + whole-block framing over readChunks (both passes)
lib/storage/convert/syncs.ts      SyncAnchor collection, sort, pickSync
lib/storage/convert/csv.ts        CsvWriter: row text -> encodeInto buffer -> ByteSink
lib/storage/convert/meta.ts       MetaJson builder + JSON text (indent 1, LF)
lib/storage/convert/convert.ts    convertLog(src, out, opts): pass 1 + pass 2, feeds a sink
lib/storage/convert/pyFormat.ts   Python width/align/thousands/%g on top of decimal.ts
lib/storage/convert/tsFilter.ts   rolling-median(11, centred) outlier filter, streaming
lib/storage/convert/stats.ts      StatsSink implements SampleSink; histograms, windows
lib/storage/convert/summary.ts    renderSummary(...) -> text (section 5.4)
lib/storage/convert/pipeline.ts   convertAndSummarize(src, out, opts): convert + summary
lib/storage/convert/protocol.ts   worker message types
lib/storage/convert/pending.ts    listPending(dest): raw BINs lacking outputs
lib/storage/convertQueue.ts       ConversionQueue: one worker, FIFO, progress, cancel
workers/convert.worker.ts         thin shell: handles -> fsaDir/FileSource -> pipeline
lib/storage/pageState.ts          + conversion slice, actions, selectors
lib/storage/copy.ts               + STORAGE_COPY.conversion
components/storage/ConversionPanel.tsx   rows, Retry, Convert missing, summary <pre>
pages/Storage.tsx                 queue after item-done / card-delete; scan on dest ready
lib/storage/fixtures/convert/     synthetic *.bin + goldens (*.csv.sha256, *.meta.json,
                                  *.summary.txt) + README
scripts/kneesleeve/{bin2csv.py,sensor_stats.py,README.md}   vendored verbatim
scripts/storage_goldens.py        regenerates the goldens with the vendored scripts
```

### 4.2 Contracts (`convert/types.ts`, written first)

`SampleSink { start(header, tables); pass1(sid, tUs, ax..gz); pass1Done(); pass2(sid, tUs, ax..gz) }`;
`ConvertOptions { stem, sourceFile, readChunkBytes, writeChunkBytes, signal?, onProgress?,
sink? }`; `convertLog(src: ByteSource, out: DirLike, opts): Promise<ConvertResult>` writes
`<stem>.csv` and `<stem>.meta.json`; `PipelineOptions = ConvertOptions - sink + { placement
(sid) => string, fCutHz, gapUs, tsOutlierUs }`; `convertAndSummarize(src, out, opts):
Promise<PipelineResult { meta, summary, outputs }>` also writes `<stem>_summary.txt`.
`ValueTables` = one text and one double per i16 count per axis type.

### 4.3 Decoder (pass structure, decision T)

Pass 1 (`prepass`): parse the header (ConvertError `format` on short/magic/version/crc,
message like bin2csv's); for every whole block: `classifyBlock`; valid TIME_SYNC -> anchor;
valid IMU with count <= 290 -> `sink.pass1(sid, base + dt)` per sample (base as bigint,
guarded < 2^53 once per block, then Number). `sink.pass1Done()`. Pass 2 (`convert`):
bin2csv's loop verbatim (section 2), rows appended to the CsvWriter as
`${dev},${sid},${seq},${t},${u},${accelText[ax+32768]},...\n` with `u = t + off` where
`off = Number(unix - esp)` of `pickSync(base)` (guarded), or '' with no anchors;
`sink.pass2(...)` per sample; progress every chunk. Both passes reuse `readChunks` with the
card read budget; the CSV goes through `out.create(csv)` in `writeChunkBytes` buffers via
`TextEncoder.encodeInto`; on any error or abort the sink is aborted (no partial output).
meta extras: `unused_tail_blocks` = blocks classified `unused`; `first_bad` = index of the
first block classified `bad` (null if none); `blocks_bad` = bad + unused (bin2csv parity).

### 4.4 Statistics sink and summary

Both passes hand the sink every sample (timestamp plus the six counts) after a
`start(header, tables)` call. `StatsSink` keeps per sensor, in pass 1: the `tsFilter`
ring (11, centred, min_periods 1) -> good samples in file order -> dt integer histogram
(Uint32Array(65536) plus an overflow Map), n_gaps, gap_time_us, max_dt, min/max ts,
backwards count, and a coarse histogram of `|a| = sqrt(ax^2 + ay^2 + az^2)` over the CSV
values (bin width a named constant in stats.ts; memory only, the result is exact).
`pass1Done()` computes dt_med (even-N average), the at-nominal share, n_win, and locates
the |a| median bin(s). Pass 2: six Uint32Array(65536) count histograms (per-axis medians
are exact because count -> CSV value is monotonic; the even-N average is taken over the
two middle CSV values as numpy does), clip counts from the value tables against
`0.9999 * rail`, the exact |a| values falling in the median bin(s) (a small array, sorted
at the end for the exact median), and the window accumulator (n_win samples of t and six
doubles; a gap or a rejected timestamp resets it, so windows start at each gap-free run's
start and a run's remainder is dropped); each full window's least-squares residual stds
go to per-axis growable Float64Arrays. `finish()` sorts those and returns `StatsResult`.
`renderSummary()` writes section 4.7 with `pyFormat`.

### 4.5 Worker and queue

`protocol.ts`: main -> `{ type: 'convert', jobId, raw: FileSystemFileHandle, outDir:
FileSystemDirectoryHandle, stem, sourceFile, placement: Record<number, string> }`, `{ type:
'cancel', jobId }`; worker -> `{ type: 'progress', jobId, ...ConvertProgress }`, `{ type:
'done', jobId, meta, summary, outputs }`, `{ type: 'failed', jobId, code, detail }`,
`{ type: 'cancelled', jobId }`. The worker's `self` is typed through a local interface
(single tsconfig, DOM lib). Cancel is cooperative: the message is handled between awaited
reads/writes and aborts an internal AbortController; the writable is aborted so the
`.crswap` is discarded. `ConversionQueue` (main thread) owns one worker, a FIFO, and calls
back the page; `terminate()` on unmount after a cancel. `fsa.ts` exports `FileSource` (was
module-private) so the worker can wrap `await raw.getFile()`.

### 4.6 Page

`conversion` slice: `{ order: string[]; jobs: Record<jobId, ConversionJob>; selected?:
jobId; pending: PendingRaw[]; scanning: boolean }`, `ConversionJob { id, folder, localName,
stem, unitId?, status: 'queued' | 'scanning' | 'converting' | 'converted' |
'already-converted' | 'failed' | 'cancelled', pct, summary?, error?: { code, detail? } }`.
Hooks in `onStart`'s event loop: `item-done` for a BIN, and `item-failed` with code
`card-delete`, enqueue `{ folder: items[id].folder, localName }`; the queue checks the three
outputs first (decision V). `dest-set` / `dest-reconnect` trigger `listPending` (decision
O). `ConversionPanel` after `TransferPanel`: a table (file, sleeve, status, Retry), the
"Convert missing (N)" button, the summary `<pre>` for the selected row (last finished by
default), and a line naming the folder the outputs went to. All strings in
`STORAGE_COPY.conversion`, plain ASCII, "sleeve" / "soldier" wording.

### 4.7 Summary text (decision U), from sensor_stats.py lines 310-338, 347-366, 595-623,
751-807, 944-999

```
========================================================================
File: <stem>.csv
========================================================================
File header and decoder verdict
  firmware {fw}  device {device_id}  session {session_id}  ODR {odr_hz:,.0f} Hz  +/-{accel_fs_g:g} g  +/-{gyro_fs_dps:g} deg/s  units: physical
  blocks {blocks_valid:,} valid / {blocks_bad} bad CRC, {seq_gaps} seq gaps, {fifo_overflows} FIFO overflows, {CLEAN|DIRTY} end
  UTC sync: {%Y-%m-%d %H:%M:%S}Z ({n} sync block(s))        | UTC sync: none (unix_us empty)

Total rows (data points) in file : {total:,}
Data points attributed to sensors: {counted:,}

1) Unique sensors: {n}

2/3/4) Per-sensor breakdown
  {'device_id':>9}  {'sensor_id':>9}  {'data_points':>13}  {'percent':>8}  {'placement':<29}
  ---------  ---------  -------------  --------  -----------------------------
  {dev:>9}  {sen:>9}  {n:>13,}  {pct:>7.3f}%  {placement:<29}
  ---------  ---------  -------------  --------  -----------------------------
  {'TOTAL':>9}  {'':>9}  {counted:>13,}  {100.000:>7.3f}%

Sampling and data continuity
  {'sensor':>7}  {'placement':<29}  {'nominal':>9}  {'delivered':>10}  {'at nom.':>8}  {'gaps>1ms':>9}  {'lost':>7}  {'max gap':>9}  {'clipped':>11}
  -------  -----------------------------  ---------  ----------  --------  ---------  -------  ---------  -----------
  {(d, s):>7}  {placement:<29}  {nominal:>7.0f}Hz  {delivered:>8.0f}Hz  {at_nom*100:>7.1f}%  {n_gaps:>9,}  {gap_time_s:>6.1f}s  {max_gap_ms:>7.1f}ms  {n_clip_a}a/{n_clip_g}g:>11

  'nominal' = 1/median(dt), the rate the sensor actually converts at.
  'delivered' = samples/span, degraded by lost blocks and FIFO overflow.
  'at nom.' = share of intervals within +/-20% of nominal, i.e. the fraction
  of the record genuinely sampled at the full rate.

8) Motion-agnostic noise and bias
Motion-agnostic noise  (content above {f_cut:g} Hz; {win_ms:.0f} ms detrended windows inside gap-free bursts, median over windows)
  {'sensor':>7}  {'placement':<29}  {'accel noise (mg RMS)':>22}  {'gyro noise (mdeg/s RMS)':>22}  {'rest |a|':>8}  {'wins':>7}
  {'':>7}  {'':<29}  {'x':>7}{'y':>7}{'z':>8}  {'x':>7}{'y':>7}{'z':>8}  {'':>8}  {'':>7}
  -------  -----------------------------  ----------------------  ----------------------  --------  -------
  {(d, s):>7}  {placement:<29}  {mg_x:>7.2f}{mg_y:>7.2f}{mg_z:>8.2f}  {gy_x:>7.0f}{gy_y:>7.0f}{gy_z:>8.0f}  {baseline:>7.4f}g  {wins:>7,}

  Accelerometer noise is divided by each sensor's own resting |accel| (the
  'rest |a|' column), so per-unit gain error is removed. One count is
  {lsb_g*1000:.3f} mg and {lsb_dps*1000:.0f} mdeg/s at this file's +/-{accel_fs_g:g} g / +/-{gyro_fs_dps:g} deg/s.

Rest orientation and gyroscope bias
  {'sensor':>7}  {'placement':<29}  {'median accel (g)':>27}  {'gyro bias (mdeg/s)':>22}  {'tilt':>7}
  {'':>7}  {'':<29}  {'x':>9}{'y':>9}{'z':>9}  {'x':>7}{'y':>7}{'z':>8}  {'':>7}
  -------  -----------------------------  ---------------------------  ----------------------  -------
  {(d, s):>7}  {placement:<29}  {a_x:>9.4f}{a_y:>9.4f}{a_z:>9.4f}  {g_x:>7.0f}{g_y:>7.0f}{g_z:>8.0f}  {tilt:>6.1f}d

  'tilt' is the angle between the sensor +Z axis and the median gravity
  vector. Gyro medians are the static bias to remove before integration.
```
Blank lines exactly as sensor_stats prints them (one after each section, so the text ends
with a blank line). Sensors sorted by (device_id, sensor_id). `gaps>{gap_ms:g}ms` follows
`STORAGE_GAP_US`. The "Rows skipped (blank id fields)" line never applies (no blank ids).

### 4.8 Goldens

`scripts/storage_goldens.py` (run from the repo root with `uv run --with matplotlib python
scripts/storage_goldens.py`) copies each fixture BIN to a temp dir under its golden name,
runs the vendored `bin2csv.py`, writes `<name>.csv.sha256` (LF CSV, so platform-stable),
`<name>.meta.json` (LF-normalised), runs `sensor_stats.py --no-describe --no-plot` and
writes `<name>.summary.txt` with the `File:` line reduced to the basename. Fixtures:
`LOG_0010.head64.bin` (real 1.1.0 head, run as `LOG_0010.BIN`; its CSV sha256 is
d394aeb7f783550b4482dcc8743fc7a119610ee61ffa92e2bf1930c7036199fb, 18 560 rows) plus
synthetic files from `binEncode.ts` written once by `fixtures.write.test.ts` when
`STORAGE_WRITE_FIXTURES=1` (otherwise the test asserts the committed bytes still equal the
encoder's output): sync blocks before and after IMU blocks, SESSION_END mid-file, a
sample_count 300 block, a bad-CRC block (which also makes a seq gap), flag bits 1 and 2,
a 0xFF and a 0x00 tail, a partial tail. Tests: CSV sha256 equal; meta parsed-equal;
summary compared line by line with numeric tokens within one unit of the last printed
digit and everything else exact, with `placement` = `placement not set`.

## 5. Work packages and ownership (parallel agents B, C, D; files never shared)

- Owner-orchestrator (done first): config.ts tunables, `convert/types.ts`, `decimal.ts`,
  `scaled.ts` and their tests, this plan, the branch.
- WP-B decode + goldens: `blocks.ts`, `syncs.ts`, `csv.ts`, `meta.ts`, `convert.ts` and
  tests; `fixtures/convert/*` + `fixtures.write.test.ts` + `node-fs.d.ts` (add
  writeFileSync/mkdirSync/existsSync); `scripts/kneesleeve/*`, `scripts/storage_goldens.py`;
  golden tests for CSV sha256 and meta.
- WP-C statistics + summary: `pyFormat.ts`, `tsFilter.ts`, `stats.ts`, `summary.ts`,
  `pipeline.ts` and tests, including the head64 truth run (nominal 6410 Hz, delivered
  6404 / 6418, at nom 98.4 / 90.0 %, gaps 11 / 50, max gap 1.2 ms, noise 6.19 5.32 6.47 /
  476 551 348, rest |a| 1.0070 / 1.0104, wins 60 / 37, bias -0.6865 0.4131 0.6123 / 1099
  1099 488, tilt 52.6 / 57.6) and the summary golden test.
- WP-D worker + page: `protocol.ts`, `pending.ts`, `convertQueue.ts`,
  `workers/convert.worker.ts`, `fsa.ts` export, `pageState.ts` slice + tests, `copy.ts`,
  `ConversionPanel.tsx`, `Storage.tsx`, `app.css`; build check of the worker chunk.
- WP-E docs (Phase E): README, UIUX 15, APPFLOW 1.5, PLAN.md, IMPLEMENTATION_PLAN.md,
  agent-docs/README.md, 00_PROJECT_CONTEXT.md, this file's As built.

Commit points (owner commits): after WP-B+C+D are green together; after WP-E.

## 6. Verification planned

| Gate | Command |
|---|---|
| Types | `cd frontend; npx tsc -b` |
| Unit + golden tests | `cd frontend; npm test` |
| Build incl. worker chunk | `cd frontend; npm run build` |
| Backend unchanged | `uv run pytest backend/tests/` |
| Goldens reproducible | `uv run --with matplotlib python scripts/storage_goldens.py` then `git diff --stat` clean |
| Real run (Edge) | `npm run dev`, local folder with CONFIG.TXT + LOG_0010.BIN (512 MiB): time, peak memory, CSV sha256 == A033F3AC... |

## 7. Docs to update (Phase E, same change-set)

README "Sleeve storage (USB)" (third bullet, replace the "planned, not built" line, E2E
step 10); docs/UIUX.md 15 (amend intro, new card 5, tunables in 13); docs/APPFLOW.md 1.5
(extend the flow, replace the closing line); docs/PLAN.md and docs/IMPLEMENTATION_PLAN.md
status lines; agent-docs/README.md table row for 03; 00_PROJECT_CONTEXT.md (state, layout,
tunables, back under 150 lines, branch wording); this file's As built.

## 8. Risks

- Peak memory: value tables (2 x 65536 strings + 2 Float64Array), six 65536-bin Uint32
  histograms per sensor, the |a| coarse histogram, and the window arrays (about 20 MB for a
  2 GiB log). The CSV never exists in memory. Measured in the real run.
- Speed: 31 M rows of string assembly for a 512 MiB log; expected tens of seconds. Measured.
- Disk: close() of a 9 GB CSV needs the space twice for a moment (Chromium swap file).
- pandas float parsing vs `Number()`: sub-ulp differences in the summary's statistics are
  absorbed by the golden tolerance; the CSV itself is exact.
- Vitest on the 65536-count cross-check tests: about a second.

## As built

### As built, change-set 2 (2026-09-25)

Deviations from sections 4 and 5, by work package. Everything else is as planned.

1. Decoder (WP-B). `blocks.ts` yields three frame kinds (`header`, `block`, `chunk`); the
   `chunk` frame is the progress hook. `CsvWriter.row()` returns a promise only when a
   flush happened, so 31 M rows do not pay an await each. Pass 1 decodes samples only when
   a sink is given (anchors are always collected). A failure while writing meta.json
   leaves the already-closed CSV in place (nothing is ever removed); `listPending` then
   lists the raw again because meta and summary are missing. A2 (header-only CSV + meta +
   short summary for a BIN without IMU rows) is implemented and unit-tested, not
   golden-tested (bin2csv writes nothing). Test-only helpers live beside the fixtures
   (`fixtures/convert/specs.ts`, `recordingSink.ts`); `fixtures/node-crypto.d.ts` and
   `node-globals.d.ts` declare the two node calls the golden tests use.
2. Statistics (WP-C). A rejected (outlier) timestamp does NOT reset the noise-window run:
   sensor_stats drops the row before `continuous_runs()`, so a run splits only when the
   interval between the good neighbours exceeds the gap; the plan's 4.4 said otherwise and
   the Python truth on a synthetic stream confirmed the code. A3: a backwards step is
   counted, its interval left out of the dt figures, and it ends the window run; the extra
   summary line carries the sum over sensors. A sensor with fewer than two good samples
   gets n_win = 20 and prints `nan` where Python would. The head64 summary golden keeps
   sensor_stats' `5/6)` tap note (verbatim tool output); the golden test strips that block
   before comparing (decision U). `pyRound(-0)` is +0 (Python returns an int). The port
   reproduced sensor_stats exactly on every golden, so the tolerance is unused today.
3. Worker and page (WP-D). `WorkerLike` handler parameters are the DOM event types (a real
   `Worker` is not assignable under strictFunctionTypes otherwise). `PendingRaw.unitId` is
   never null (the folder filter guarantees a match). `RAW_BIN_RE` is case-insensitive like
   `LOG_NAME_RE`. Summary side labels are a new lower-case `sides` table (the existing
   Left/Right labels are capitalised buttons). `pending-loaded` drops entries whose job is
   live; failed and cancelled ones stay listed (the retry-after-reload path). Removal from
   `pending` happens on `convert-queued`. `ConversionPanel` takes `summary` and
   `destReady` props; row selection is a radio per row. The worker maps NotAllowedError /
   SecurityError to `permission`, NotReadableError / NotFoundError to `read`, anything
   else to `read` in the pre-pass and `write` in the main pass; an aborted signal or
   ConvertError `aborted` answers `cancelled`. `enqueue()` returns whether it accepted.
4. Outputs are atomic in content, not in existence: `getFileHandle(name, { create: true })
   ` creates the file before the swap-file write starts, so a failed or cancelled
   conversion can leave an EMPTY `<stem>.csv` (seen in the first Edge run, which hit a
   storage quota). It is harmless: `listPending` and the queue treat a raw as converted
   only when all three outputs exist, and a retry rewrites it. Documented in UIUX 15.
5. Dry run without a sleeve: `frontend/e2e/convert.html` + `convert.ts` (dev-only, served
   by `npm run dev`, outside `tsconfig`'s `include`) copy a picked BIN into the browser's
   origin-private file system and run the real queue and worker on real handles; the CSV
   is hashed in the page (`e2e/sha256.ts`, self-checked). The picker step itself stays a
   manual check. The automated driver (Playwright, Edge channel, persistent context) lives
   outside the repo in the session scratchpad; a temporary context's OPFS quota is about
   1 GB, which is why the first run failed on the 2.27 GB CSV.

6. Code review (medium-effort pass over the working tree, 2026-09-25) found three page-state
   defects, all fixed the same day: (a) `resolve` read the destination handle at run time,
   so a job queued for one destination would have written into a destination picked later;
   the page now records the destination handle per job at enqueue time. (b) `pending-loaded`
   treated converted rows as live, hiding a raw whose outputs the user removed until a
   reload; only queued / scanning / converting jobs are live now. (c) Re-queueing a finished
   job (a re-transfer of the same log answers `already-converted`) reset its row and lost
   the summary; the summary and outputs of the last success now survive a re-queue, are
   shown for `already-converted`, and are cleared by `failed` / `cancelled`. `selectedSummary`
   accepts `already-converted`. Tests added for all three.

7. Empty placeholders are not outputs (docs pass finding, 2026-09-25). The pending scan and
   the queue's "already converted" check used `exists()`, so a failure during the summary
   write (CSV and meta already closed) left three names present and the raw would never be
   offered again after a reload. Both now read the folder listing and count an output only
   when its size is above zero (`pending.ts` `missingOutputs` / `outputsPresent`). Tested.

#### Verification actually run (2026-09-25)

| Gate | Result |
|---|---|
| `cd frontend; npx tsc -b` | clean, exit 0 |
| `npm test` | 37 files, 562 tests passed after the review fixes (CS1 had 20 files / 206) |
| `npm run build` | ok, re-run after the fixes; `dist/assets/convert.worker-*.js` 30.06 kB emitted, no vite.config change |
| goldens | `uv run --with matplotlib python scripts/storage_goldens.py` twice: 27 files byte-identical; head64 CSV sha256 d394aeb7...199fb as expected; summaries exact on all 9 |
| `uv run pytest backend/tests/` | 315 passed, 47 skipped (DB/Redis tests self-skip without the docker debug profile), 0 failed, 4 min 54 s; backend untouched |
| Edge real run (headless Edge 153 via Playwright, `frontend/e2e/convert.html`, persistent profile) | LOG_0010.BIN 512 MiB copied into OPFS in 4.3 s; conversion 80.2 s (pre-pass + main pass, 30 943 580 rows); CSV 2 269 875 061 B, sha256 a033f3acb89853044774018f16b91d0f5bd12b881d24a788c9c74126edf1eb2f = bin2csv.py's; meta.json equal to Python's on every key (blocks 106 702 / 24 369, rows 15 489 770 / 15 453 810) plus unused_tail_blocks 24 369, first_bad null; summary rendered with "left thigh" / "left shin"; memory: main-thread JS heap peak 3.8 MiB, Edge working set +163 MiB over idle (largest process 262 MiB), sampled every 500 ms |
| Full-file summary vs Python | `sensor_stats.py --no-describe --no-plot` on the Python-made 2.27 GB LOG_0010.csv (pandas 3.0.5, ~6 min) compared with the summary the worker produced in Edge: 53 of 53 lines identical apart from the placement column (dashboard wording) and the `File:` line |
| Code review | `/code-review medium` over the working tree: 3 findings (deviation 6), fixed; gates re-run afterwards (below) |
| First Edge attempt | failed with QuotaExceededError after the pre-pass: Playwright's default context is incognito-style (~1 GB OPFS quota); not a code fault, the harness now uses a persistent context. It also showed the empty-placeholder behaviour recorded in deviation 4 |
