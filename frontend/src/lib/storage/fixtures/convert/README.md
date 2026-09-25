# Decoder fixtures and goldens (sleeve storage change-set 2)

Synthetic `LOG_NNNN.BIN` files for `lib/storage/convert/` plus what the
vendored reference scripts (`scripts/kneesleeve/bin2csv.py`,
`sensor_stats.py`) produce for them. Every file here is byte-exact
(`.gitattributes`: `* -text`, `*.bin binary`); never let an editor
normalise line endings or trailing spaces.

## The `.bin` fixtures

All are format_version 1, device 3, source 0, firmware `1.2.0`, +-32 g /
+-4000 deg/s, two sensors (1 thigh, 2 shin) interleaved, full 290-sample
blocks of `rampSamples()` with a seed per block, and a per-sensor clock
that advances by exactly one block span (290 x 156 us) per block so a
sensor's samples are gap-free unless a block is skipped on purpose. Specs:
`specs.ts`; encoder: `../binEncode.ts`. The real firmware 1.1.0 head,
`../LOG_0010.head64.bin`, is golden-tested alongside them.

| Fixture | Exercises |
|---|---|
| `sync_mid.bin` | Two TIME_SYNC blocks after the first IMU blocks: earlier blocks extrapolate backwards from the first anchor, later ones take the latest anchor at or before their base. |
| `end_mid.bin` | SESSION_END in the middle, IMU blocks after it: `clean_end` true, every row still written. |
| `oversize.bin` | One block with sample_count 300: valid (seq continues), samples skipped, a gap in that sensor's timeline. |
| `badcrc.bin` | One bad-CRC block (bad, `first_bad`, makes a seq gap) and one explicit seq gap. |
| `flags.bin` | flags 1 (FIFO overflow, counted), 2 (TS clamped, ignored), 3 (counted). |
| `tail_ff.bin` | Three 0xFF then two 0x00 fill blocks: `blocks_bad` 5 to bin2csv, `unused_tail_blocks` 5, `first_bad` null. |
| `partial.bin` | A 3584 B 0xFF partial trailing block, ignored. |
| `kitchen.bin` | All of the above in one file, syncs out of file order, a garbage block, at least 24 decodable IMU blocks per sensor so sensor_stats gets several noise windows. |

`convert/fixtures.write.test.ts` asserts each `.bin` equals its spec byte for
byte. To (re)write them after changing `specs.ts` or the encoder:

```
# PowerShell, from frontend/
$env:STORAGE_WRITE_FIXTURES='1'; npx vitest run src/lib/storage/convert/fixtures.write.test.ts
# bash
STORAGE_WRITE_FIXTURES=1 npx vitest run src/lib/storage/convert/fixtures.write.test.ts
```

then regenerate the goldens (below); the golden tests will fail until you do.

## The goldens

Written by `scripts/storage_goldens.py` (repo root,
`uv run --with matplotlib python scripts/storage_goldens.py`), one set per
fixture, `<name>` = file name without `.bin`:

| File | Content | Compared by |
|---|---|---|
| `<name>.csv.sha256` | sha256 of bin2csv's CSV, lowercase hex + LF | `convert/goldens.test.ts` (CSV bytes) |
| `<name>.meta.json` | bin2csv's sidecar, CRLF -> LF | `convert/goldens.test.ts` (parsed, key by key) |
| `<name>.summary.txt` | `sensor_stats.py --no-describe --no-plot` stdout, CRLF -> LF, `File:` line reduced to `<name>.csv` | the summary tests (plan 4.8) |

sensor_stats' defaults (gap 1 ms, noise above 20 Hz, timestamp outlier 1 s)
mirror `STORAGE_GAP_US`, `STORAGE_NOISE_F_CUT_HZ` and `STORAGE_TS_OUTLIER_US`
in `src/lib/config.ts`; change both together and regenerate. Running the
script twice leaves `git status` unchanged.
