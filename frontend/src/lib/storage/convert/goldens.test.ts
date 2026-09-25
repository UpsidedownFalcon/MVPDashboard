// Decision N: the decoder's CSV is byte-exact with the vendored bin2csv.py
// and its meta equals bin2csv's parsed. scripts/storage_goldens.py records
// bin2csv's output for every fixture in fixtures/convert/ (CSV sha256, meta
// JSON); this test replays each fixture through convertLog and compares.
// A missing golden fails loudly: run the script after adding a fixture.
import { createHash } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import { BLOCK_BYTES, FILE_HEADER_BYTES } from '../binFormat'
import { convertFixtureBytes, convertFixtureExists, convertFixtureText, goldenNames } from '../fixtures/convert/load'
import { RecordingSink } from '../fixtures/convert/recordingSink'
import { CONVERT_FIXTURES, type FixtureExtras } from '../fixtures/convert/specs'
import { fixtureBytes, LOG_0010_HEAD64_BIN } from '../fixtures/load'
import { MemDir } from '../memDir'
import { scanBytes } from '../scanner'
import { convertLog } from './convert'
import { stemOf, type ConvertResult, type MetaJson, type SampleSink } from './types'

const MIB = 1024 * 1024
/** The page's budget, and header + one block so every block is its own read. */
const READ_CHUNKS = [4 * MIB, FILE_HEADER_BYTES + BLOCK_BYTES]
/** Small enough that every fixture's CSV takes several flushes. */
const WRITE_CHUNK = 64 * 1024

/** bin2csv.py's meta keys in its order (json.dumps of its dict). */
const BIN2CSV_KEYS = [
  'fw', 'device_id', 'source_id', 'sensor_count', 'odr_hz', 'accel_fs_g', 'gyro_fs_dps',
  'accel_scale', 'gyro_scale', 'session_id', 'source_file', 'units', 'blocks_valid',
  'blocks_bad', 'seq_gaps', 'fifo_overflows', 'clean_end', 'time_sync', 'rows',
]
const EXTRA_KEYS = ['unused_tail_blocks', 'first_bad']

interface Golden {
  name: string
  file: string
  bytes: Uint8Array
  extras: FixtureExtras
}

const GOLDENS: Golden[] = [
  // The real firmware 1.1.0 head: 64 clean IMU blocks, no sync, no end.
  { name: stemOf(LOG_0010_HEAD64_BIN), file: LOG_0010_HEAD64_BIN, bytes: fixtureBytes(LOG_0010_HEAD64_BIN), extras: { unusedTailBlocks: 0, firstBad: null } },
  ...CONVERT_FIXTURES.map((f) => ({ name: f.name, file: f.file, bytes: convertFixtureBytes(f.file), extras: f.extras })),
]

function sha256(bytes: Uint8Array): string {
  return createHash('sha256').update(bytes).digest('hex')
}

interface Run {
  csv: Uint8Array
  meta: MetaJson
  result: ConvertResult
}

async function run(g: Golden, readChunkBytes: number, sink?: SampleSink): Promise<Run> {
  const src = await new MemDir({ [g.file]: g.bytes }).open(g.file)
  const out = new MemDir()
  const result = await convertLog(src, out, { stem: g.name, sourceFile: g.file, readChunkBytes, writeChunkBytes: WRITE_CHUNK, sink })
  expect(out.openSinks).toBe(0)
  const csv = out.bytes(result.outputs.csv)
  const metaText = out.text(result.outputs.meta)
  if (!csv || metaText === undefined) throw new Error('outputs missing')
  return { csv, meta: JSON.parse(metaText) as MetaJson, result }
}

describe.each(GOLDENS.map((g) => [g.name, g] as const))('golden %s', (_, g) => {
  const names = goldenNames(g.name)

  it('has its goldens (run scripts/storage_goldens.py otherwise)', () => {
    expect(convertFixtureExists(names.csvSha256), names.csvSha256).toBe(true)
    expect(convertFixtureExists(names.meta), names.meta).toBe(true)
  })

  it.each(READ_CHUNKS)('CSV sha256 equals bin2csv.py at %d-byte read chunks', async (chunk) => {
    const want = convertFixtureText(names.csvSha256).trim()
    expect(want).toMatch(/^[0-9a-f]{64}$/)
    const { csv, meta } = await run(g, chunk)
    expect(sha256(csv)).toBe(want)
    expect(csv.length).toBeGreaterThan(WRITE_CHUNK) // several flushes
    expect(meta.source_file).toBe(g.file)
  })

  it('meta equals bin2csv.py key by key; the extras follow the fixture layout', async () => {
    const want = JSON.parse(convertFixtureText(names.meta)) as Record<string, unknown>
    expect(Object.keys(want)).toEqual(BIN2CSV_KEYS)
    const sink = new RecordingSink()
    const { meta, result } = await run(g, 4 * MIB, sink)
    const got = meta as unknown as Record<string, unknown>
    for (const key of BIN2CSV_KEYS) expect(got[key], key).toEqual(want[key])
    expect(Object.keys(meta)).toEqual([...BIN2CSV_KEYS, ...EXTRA_KEYS])
    expect(meta.unused_tail_blocks).toBe(g.extras.unusedTailBlocks)
    expect(meta.first_bad).toBe(g.extras.firstBad)
    // And against the CS1 scanner, an independent reading of the same file.
    const scan = scanBytes(g.bytes)
    expect(meta.blocks_bad).toBe(scan.bad + scan.unused)
    expect(meta.blocks_valid).toBe(scan.valid)
    expect(meta.seq_gaps).toBe(scan.seqGaps)
    expect(meta.fifo_overflows).toBe(scan.fifoOverflows)
    expect(meta.clean_end).toBe(scan.cleanEnd)
    expect(result.syncs.length).toBe(scan.syncBlocks.length)
    // The sink saw every row once per pass, identically.
    const rows = Object.values(meta.rows).reduce((a, b) => a + b, 0)
    expect(sink.starts).toBe(1)
    expect(sink.pass1DoneCalls).toBe(1)
    expect(sink.samples).toBe(rows)
    expect(sink.samePasses()).toBe(true)
  })
})

describe('golden set', () => {
  it('covers the real head and every synthetic fixture exactly once', () => {
    expect(GOLDENS.map((g) => g.name)).toEqual(['LOG_0010.head64', ...CONVERT_FIXTURES.map((f) => f.name)])
    expect(new Set(GOLDENS.map((g) => g.name)).size).toBe(GOLDENS.length)
  })
})
