import { describe, expect, it } from 'vitest'
import {
  BLOCK_BYTES,
  BLOCK_TYPE_IMU,
  classifyBlock,
  FILE_HEADER_BYTES,
  MAX_SAMPLES,
  parseFileHeader,
  readBlockHeader,
  SAMPLE_AREA_OFFSET,
  SAMPLE_BYTES,
  SAMPLE_OFF_AX,
  SAMPLE_OFF_AY,
  SAMPLE_OFF_AZ,
  SAMPLE_OFF_DT_US,
  SAMPLE_OFF_GX,
  SAMPLE_OFF_GY,
  SAMPLE_OFF_GZ,
  viewOf,
  type FileHeader,
} from '../binFormat'
import { STORAGE_READ_CHUNK_BYTES } from '../../config'
import { convertFixtureBytes, convertFixtureExists, convertFixtureText, goldenNames } from '../fixtures/convert/load'
import { CONVERT_FIXTURES } from '../fixtures/convert/specs'
import { fixtureBytes, LOG_0010_HEAD64_BIN } from '../fixtures/load'
import { MemDir } from '../memDir'
import { convertAndSummarize } from './pipeline'
import { buildValueTables } from './scaled'
import { StatsSink } from './stats'
import { renderSummary } from './summary'
import { stemOf, type MetaJson } from './types'

// Goldens: sensor_stats.py --no-describe --no-plot over bin2csv.py's CSV of
// each fixture (plan 4.8; written by scripts/storage_goldens.py), the
// `File:` line reduced to the basename. Decision U drops the 5/6) tap note
// from the summary, so that block is removed from a golden before the
// comparison. Table rows may differ by one unit of the last printed digit
// per numeric token (floating summation order); everything else must be
// identical, trailing spaces included. The head64 fixture is compared twice:
// through the local decode loop with a hand-built meta (independent of
// convert.ts), and, with every synthetic fixture, through the pipeline.
const GOLDEN = 'convert/LOG_0010.head64.summary.txt'
const F_CUT_HZ = 20
const GAP_US = 1000
const TS_OUTLIER_US = 1_000_000
/** Small enough that every fixture's CSV takes several flushes. */
const WRITE_CHUNK = 64 * 1024

function loadGolden(): string | null {
  try {
    return new TextDecoder().decode(fixtureBytes(GOLDEN))
  } catch {
    return null
  }
}

/** The same local decode loop as stats.test.ts: every sample of every valid
 *  IMU block (sample_count <= 290) through both passes of the sink. */
function statsOf(bytes: Uint8Array, sink: StatsSink): FileHeader {
  const parsed = parseFileHeader(bytes)
  if (!parsed.ok) throw new Error(`header: ${parsed.code}`)
  sink.start(parsed.header, buildValueTables(parsed.header))
  const view = viewOf(bytes)
  for (const pass of [1, 2] as const) {
    for (let off = FILE_HEADER_BYTES; off + BLOCK_BYTES <= bytes.length; off += BLOCK_BYTES) {
      if (classifyBlock(bytes.subarray(off, off + BLOCK_BYTES)) !== 'valid') continue
      const bh = readBlockHeader(view, off)
      if (bh.type !== BLOCK_TYPE_IMU || bh.sampleCount > MAX_SAMPLES) continue
      const base = Number(bh.baseTsUs)
      for (let i = 0; i < bh.sampleCount; i++) {
        const p = off + SAMPLE_AREA_OFFSET + i * SAMPLE_BYTES
        const t = base + view.getUint16(p + SAMPLE_OFF_DT_US, true)
        const ax = view.getInt16(p + SAMPLE_OFF_AX, true)
        const ay = view.getInt16(p + SAMPLE_OFF_AY, true)
        const az = view.getInt16(p + SAMPLE_OFF_AZ, true)
        const gx = view.getInt16(p + SAMPLE_OFF_GX, true)
        const gy = view.getInt16(p + SAMPLE_OFF_GY, true)
        const gz = view.getInt16(p + SAMPLE_OFF_GZ, true)
        if (pass === 1) sink.pass1(bh.sensorId, t, ax, ay, az, gx, gy, gz)
        else sink.pass2(bh.sensorId, t, ax, ay, az, gx, gy, gz)
      }
    }
    if (pass === 1) sink.pass1Done()
  }
  return parsed.header
}

/** meta.json as bin2csv.py writes it for this fixture (64 valid blocks, no
 *  sync, no END; scanner.test.ts checks the same facts). The decoder's own
 *  meta is WP-B's golden. */
function metaOf(h: FileHeader): MetaJson {
  return {
    fw: h.fw,
    device_id: h.deviceId,
    source_id: h.sourceId,
    sensor_count: h.sensorCount,
    odr_hz: h.odrHz,
    accel_fs_g: h.accelFsG,
    gyro_fs_dps: h.gyroFsDps,
    accel_scale: h.accelScale,
    gyro_scale: h.gyroScale,
    session_id: h.sessionId,
    source_file: 'LOG_0010.head64.bin',
    units: 'physical',
    blocks_valid: 64,
    blocks_bad: 0,
    seq_gaps: 0,
    fifo_overflows: 0,
    clean_end: false,
    time_sync: [],
    rows: { '1': 9280, '2': 9280 },
    unused_tail_blocks: 0,
    first_bad: null,
  }
}

/** Remove the "5/6) Tap-order calibration not run" block (through its
 *  trailing blank line) that sensor_stats prints and decision U drops. */
function dropTapNote(lines: string[]): string[] {
  const i = lines.findIndex((l) => l.startsWith('5/6) '))
  if (i < 0) return lines
  let j = i
  while (j < lines.length && lines[j] !== '') j++
  return [...lines.slice(0, i), ...lines.slice(j + 1)]
}

/** Table rows: a `(dev, sid)` key, a bare device id or TOTAL first. */
const TABLE_ROW = /^\s+(\(\d+, \d+\)|\d+|TOTAL)\s/
const NUMBER = /(-?\d[\d,]*(?:\.\d+)?)/

function decimalsOf(num: string): number {
  const dot = num.indexOf('.')
  return dot < 0 ? 0 : num.length - dot - 1
}

/** Non-numeric parts exact; numeric parts (same number of decimals) within
 *  one unit of the last printed digit. */
function tokenMatches(got: string, want: string): boolean {
  if (got === want) return true
  const pg = got.split(NUMBER)
  const pw = want.split(NUMBER)
  if (pg.length !== pw.length) return false
  for (let i = 0; i < pg.length; i++) {
    if (i % 2 === 0) {
      if (pg[i] !== pw[i]) return false
      continue
    }
    const d = decimalsOf(pw[i])
    if (decimalsOf(pg[i]) !== d) return false
    const diff = Math.abs(Number(pg[i].replace(/,/g, '')) - Number(pw[i].replace(/,/g, '')))
    if (diff > 10 ** -d * (1 + 1e-9)) return false
  }
  return true
}

function lineMatches(got: string, want: string): boolean {
  if (got === want) return true
  if (!TABLE_ROW.test(want)) return false
  const tg = got.trim().split(/\s+/)
  const tw = want.trim().split(/\s+/)
  if (tg.length !== tw.length) return false
  return tg.every((t, i) => tokenMatches(t, tw[i]))
}

function firstDifference(got: string[], want: string[]): string | null {
  const n = Math.max(got.length, want.length)
  for (let i = 0; i < n; i++) {
    if (i < got.length && i < want.length && lineMatches(got[i], want[i])) continue
    return `line ${i + 1}\n got: ${JSON.stringify(got[i])}\nwant: ${JSON.stringify(want[i])}`
  }
  return null
}

const golden = loadGolden()

describe('summary golden (LOG_0010.head64)', () => {
  it.skipIf(golden === null)('matches sensor_stats.py line by line within one unit of the last digit', () => {
    const bytes = fixtureBytes(LOG_0010_HEAD64_BIN)
    const sink = new StatsSink({ fCutHz: F_CUT_HZ, gapUs: GAP_US, tsOutlierUs: TS_OUTLIER_US })
    const header = statsOf(bytes, sink)
    const text = renderSummary({
      csvName: 'LOG_0010.head64.csv',
      meta: metaOf(header),
      stats: sink.finish(),
      placement: () => 'placement not set',
      fCutHz: F_CUT_HZ,
      gapUs: GAP_US,
    })
    const want = dropTapNote((golden as string).split('\n'))
    const got = text.split('\n')
    const diff = firstDifference(got, want)
    expect(diff, diff ?? '').toBeNull()
    expect(got).toHaveLength(want.length)
  })

  it('the tolerant comparer accepts one unit of the last digit in table rows only', () => {
    expect(lineMatches('   (1, 1)  x   6.19   5.32', '   (1, 1)  x   6.20   5.32')).toBe(true)
    expect(lineMatches('   (1, 1)  x   6.19   5.32', '   (1, 1)  x   6.21   5.32')).toBe(false)
    expect(lineMatches('   (1, 1)  x   6.19Hz', '   (1, 1)  x   6.19ms')).toBe(false)
    expect(lineMatches('   (1, 1)  x   9,281   0a/1g', '   (1, 1)  x   9,280   0a/0g')).toBe(true)
    expect(lineMatches('   (1, 1)  x   9,282', '   (1, 1)  x   9,280')).toBe(false)
    expect(lineMatches('  0.977 mg', '  0.978 mg')).toBe(false) // a footnote is not a table row
    expect(lineMatches('      TOTAL   18,560  100.000%', '      TOTAL   18,561  100.000%')).toBe(true)
    expect(lineMatches('   (1, 1)  x  nan', '   (1, 1)  x  1.0')).toBe(false)
  })
})

/** Every fixture with a summary golden, through the whole pipeline
 *  (convert.ts + StatsSink + renderSummary). */
const PIPELINE_CASES: { name: string; file: string; bytes: Uint8Array }[] = [
  { name: stemOf(LOG_0010_HEAD64_BIN), file: LOG_0010_HEAD64_BIN, bytes: fixtureBytes(LOG_0010_HEAD64_BIN) },
  ...CONVERT_FIXTURES.map((f) => ({ name: f.name, file: f.file, bytes: convertFixtureBytes(f.file) })),
].filter((c) => convertFixtureExists(goldenNames(c.name).summary))

describe('summary goldens through the pipeline', () => {
  it('has a golden for every fixture (run scripts/storage_goldens.py otherwise)', () => {
    expect(PIPELINE_CASES.map((c) => c.name)).toEqual([stemOf(LOG_0010_HEAD64_BIN), ...CONVERT_FIXTURES.map((f) => f.name)])
  })

  it.each(PIPELINE_CASES.map((c) => [c.name, c] as const))('%s', async (_name, c) => {
    const src = await new MemDir({ [c.file]: c.bytes }).open(c.file)
    const out = new MemDir()
    const result = await convertAndSummarize(src, out, {
      stem: c.name,
      sourceFile: c.file,
      readChunkBytes: STORAGE_READ_CHUNK_BYTES,
      writeChunkBytes: WRITE_CHUNK,
      placement: () => 'placement not set',
      fCutHz: F_CUT_HZ,
      gapUs: GAP_US,
      tsOutlierUs: TS_OUTLIER_US,
    })
    expect(out.text(result.outputs.summary)).toBe(result.summary)
    const want = dropTapNote(convertFixtureText(goldenNames(c.name).summary).split('\n'))
    const got = result.summary.split('\n')
    const diff = firstDifference(got, want)
    expect(diff, diff ?? '').toBeNull()
    expect(got).toHaveLength(want.length)
  })
})
