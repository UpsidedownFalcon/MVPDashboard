import { describe, expect, it } from 'vitest'
import { BLOCK_BYTES, FH_OFF_CRC, FILE_HEADER_BYTES, FLAG_FIFO_OVERFLOW, FLAG_TS_CLAMPED, MAX_SAMPLES } from '../binFormat'
import { crc32Of } from '../crc32'
import { encodeFileHeader, encodeLogFile, fill, rampSamples, withByteFlipped, type BlockSpec, type ImuBlockSpec, type Sample } from '../fixtures/binEncode'
import { RecordingSink } from '../fixtures/convert/recordingSink'
import { MemDir } from '../memDir'
import { convertLog } from './convert'
import { CSV_HEADER } from './csv'
import { ACCEL_DECIMALS, formatScaled, GYRO_DECIMALS } from './scaled'
import { ConvertError, type ConvertOptions, type ConvertProgress, type ConvertResult, type MetaJson, type SyncAnchor } from './types'

const MIB = 1024 * 1024
/** Header + one block per read: every block is its own chunk. */
const SMALL_CHUNK = FILE_HEADER_BYTES + BLOCK_BYTES
const DEVICE = 7
const HEADER = { deviceId: DEVICE, sourceId: 0, sessionId: 1 }
const STEM = 'LOG_0001'
const SOURCE = 'LOG_0001.BIN'
const CSV = `${STEM}.csv`
const META = `${STEM}.meta.json`
const BASE_OPTS: ConvertOptions = { stem: STEM, sourceFile: SOURCE, readChunkBytes: SMALL_CHUNK, writeChunkBytes: 64 * 1024 }

const S = (dtUs: number, ax: number, ay: number, az: number, gx: number, gy: number, gz: number): Sample => ({ dtUs, ax, ay, az, gx, gy, gz })

interface Run {
  out: MemDir
  result: ConvertResult
  csv: string
  meta: MetaJson
  progress: ConvertProgress[]
}

async function convert(bytes: Uint8Array, opts: Partial<ConvertOptions> = {}): Promise<Run> {
  const src = await new MemDir({ [SOURCE]: bytes }).open(SOURCE)
  const out = new MemDir()
  const progress: ConvertProgress[] = []
  const result = await convertLog(src, out, { ...BASE_OPTS, onProgress: (p) => progress.push(p), ...opts })
  expect(out.openSinks).toBe(0)
  expect([...out.files.keys()].sort()).toEqual([CSV, META])
  const meta = JSON.parse(out.text(META)!) as MetaJson
  expect(meta).toEqual(result.meta)
  return { out, result, csv: out.text(CSV)!, meta, progress }
}

async function failing(
  bytes: Uint8Array,
  opts: Partial<ConvertOptions> = {},
  out = new MemDir(),
  srcDir = new MemDir({ [SOURCE]: bytes }),
): Promise<{ err: ConvertError; out: MemDir }> {
  const src = await srcDir.open(SOURCE)
  let err: unknown
  try {
    await convertLog(src, out, { ...BASE_OPTS, ...opts })
  } catch (e) {
    err = e
  }
  expect(err).toBeInstanceOf(ConvertError)
  expect(out.openSinks).toBe(0)
  return { err: err as ConvertError, out }
}

/** Column `i` (0-based) of every data row. */
function column(csv: string, i: number): string[] {
  return csv
    .split('\n')
    .slice(1)
    .filter((l) => l.length > 0)
    .map((l) => l.split(',')[i])
}

/** An independent oracle for bin2csv's row text over encoder specs, with the
 *  value text from scaled.ts and pick_sync transliterated (linear). */
function expectedCsv(blocks: BlockSpec[], deviceId = DEVICE): string {
  const syncs: SyncAnchor[] = []
  for (const b of blocks) if (b.type === 'sync') syncs.push({ espUs: b.espUs, unixUs: b.unixUs })
  syncs.sort((a, b) => (a.espUs !== b.espUs ? (a.espUs < b.espUs ? -1 : 1) : a.unixUs < b.unixUs ? -1 : a.unixUs > b.unixUs ? 1 : 0))
  let text = CSV_HEADER
  for (const b of blocks) {
    if (b.type !== 'imu' || b.corruptCrc) continue
    const samples = b.samples ?? []
    if ((b.count ?? samples.length) > MAX_SAMPLES) continue
    const base = b.baseTsUs ?? 0n
    let pick: SyncAnchor | null = null
    if (syncs.length) {
      pick = syncs[0]
      for (const s of syncs) {
        if (s.espUs <= base) pick = s
        else break
      }
    }
    for (const s of samples) {
      const t = base + BigInt(s.dtUs)
      const u = pick ? String(t + pick.unixUs - pick.espUs) : ''
      const a = [s.ax, s.ay, s.az].map((c) => formatScaled(c, 32, ACCEL_DECIMALS))
      const g = [s.gx, s.gy, s.gz].map((c) => formatScaled(c, 4000, GYRO_DECIMALS))
      text += `${deviceId},${b.sensorId},${b.seq},${t},${u},${a.join(',')},${g.join(',')}\n`
    }
  }
  return text
}

const HAND_BLOCKS: BlockSpec[] = [
  { type: 'imu', sensorId: 1, seq: 0, baseTsUs: 1000n, samples: [S(0, -694, 408, 632, 8, 48, 16), S(156, 8, 24, -8, 32, 96, -32)] },
  { type: 'imu', sensorId: 2, seq: 1, baseTsUs: 1000n, samples: [S(0, 0, 1, -1, 0, 1, -1)] },
]
// Values from the Python truth table in scaled.test.ts.
const HAND_CSV =
  CSV_HEADER +
  '7,1,0,1000,,-0.677734,0.398438,0.617188,0.9766,5.8594,1.9531\n' +
  '7,1,0,1156,,0.007812,0.023438,-0.007812,3.9062,11.7188,-3.9062\n' +
  '7,2,1,1000,,0.000000,0.000977,-0.000977,0.0000,0.1221,-0.1221\n'

/** Six full IMU blocks, sensors alternating, gap-free per sensor (typed as
 *  IMU specs so tests can spread them with IMU-only overrides). */
const RAMP: ImuBlockSpec[] = []
for (let i = 0; i < 6; i++) {
  RAMP.push({ type: 'imu', sensorId: 1 + (i % 2), seq: i, baseTsUs: 5_000_000n + BigInt(i >> 1) * 45_240n, samples: rampSamples(MAX_SAMPLES, i * 1000) })
}

describe('convertLog output', () => {
  it('writes bin2csv.py\'s CSV and meta byte for byte for a hand-checked file', async () => {
    const { csv, meta, result, out } = await convert(encodeLogFile(HEADER, HAND_BLOCKS))
    expect(csv).toBe(HAND_CSV)
    expect(meta).toEqual({
      fw: '1.2.0',
      device_id: 7,
      source_id: 0,
      sensor_count: 2,
      odr_hz: 6400,
      accel_fs_g: 32,
      gyro_fs_dps: 4000,
      accel_scale: 32 / 32768,
      gyro_scale: 4000 / 32768,
      session_id: 1,
      source_file: 'LOG_0001.BIN',
      units: 'physical',
      blocks_valid: 2,
      blocks_bad: 0,
      seq_gaps: 0,
      fifo_overflows: 0,
      clean_end: false,
      time_sync: [],
      rows: { '1': 2, '2': 1 },
      unused_tail_blocks: 0,
      first_bad: null,
    })
    expect(out.text(META)).toBe(JSON.stringify(meta, null, 1))
    expect(result.outputs).toEqual({ csv: CSV, meta: META, summary: 'LOG_0001_summary.txt' })
    expect(result.header.deviceId).toBe(7)
    expect(result.syncs).toEqual([])
    expect(result.tables.accelText[32768 + 408]).toBe('0.398438')
  })

  it('matches the row oracle over full ramp blocks and every chunk size', async () => {
    const file = encodeLogFile(HEADER, RAMP)
    const want = expectedCsv(RAMP)
    const small = await convert(file, { readChunkBytes: SMALL_CHUNK, writeChunkBytes: 1 })
    const big = await convert(file, { readChunkBytes: 4 * MIB, writeChunkBytes: 2 * MIB })
    expect(small.csv).toBe(want)
    expect(big.csv).toBe(want)
    expect(big.meta).toEqual(small.meta)
    expect(small.meta.rows).toEqual({ '1': 3 * MAX_SAMPLES, '2': 3 * MAX_SAMPLES })
    expect(small.csv.split('\n')).toHaveLength(6 * MAX_SAMPLES + 2)
  })

  it('unix_us: the first anchor extrapolates backwards, later blocks take the latest anchor at or before their base', async () => {
    const blocks: BlockSpec[] = [
      { type: 'imu', sensorId: 1, seq: 0, baseTsUs: 1000n, samples: [S(5, 1, 1, 1, 1, 1, 1)] },
      { type: 'sync', seq: 1, espUs: 2000n, unixUs: 1_700_000_000_002_000n },
      { type: 'imu', sensorId: 1, seq: 2, baseTsUs: 2000n, samples: [S(7, 1, 1, 1, 1, 1, 1)] },
      { type: 'sync', seq: 3, espUs: 5000n, unixUs: 1_700_000_000_005_500n },
      { type: 'imu', sensorId: 1, seq: 4, baseTsUs: 4999n, samples: [S(1, 1, 1, 1, 1, 1, 1)] },
      { type: 'imu', sensorId: 1, seq: 5, baseTsUs: 5000n, samples: [S(0, 1, 1, 1, 1, 1, 1)] },
    ]
    const { csv, meta, result } = await convert(encodeLogFile(HEADER, blocks))
    expect(column(csv, 3)).toEqual(['1005', '2007', '5000', '5000'])
    expect(column(csv, 4)).toEqual(['1700000000001005', '1700000000002007', '1700000000005000', '1700000000005500'])
    expect(csv).toBe(expectedCsv(blocks))
    expect(meta.time_sync).toEqual([
      { esp_us: 2000, unix_us: 1_700_000_000_002_000 },
      { esp_us: 5000, unix_us: 1_700_000_000_005_500 },
    ])
    expect(result.syncs).toEqual([
      { espUs: 2000n, unixUs: 1_700_000_000_002_000n },
      { espUs: 5000n, unixUs: 1_700_000_000_005_500n },
    ])
    expect(meta).toMatchObject({ blocks_valid: 6, seq_gaps: 0, rows: { '1': 4 } })
  })

  it('anchors are sorted, so a sync late in the file serves the blocks before it', async () => {
    const blocks: BlockSpec[] = [
      { type: 'imu', sensorId: 1, seq: 0, baseTsUs: 9000n, samples: [S(0, 1, 1, 1, 1, 1, 1)] },
      { type: 'sync', seq: 1, espUs: 8000n, unixUs: 1_000_008_000n },
      { type: 'imu', sensorId: 1, seq: 2, baseTsUs: 9000n, samples: [S(0, 1, 1, 1, 1, 1, 1)] },
      { type: 'sync', seq: 3, espUs: 1000n, unixUs: 1_000_001_500n },
      { type: 'imu', sensorId: 1, seq: 4, baseTsUs: 1000n, samples: [S(0, 1, 1, 1, 1, 1, 1)] },
    ]
    const { csv, meta } = await convert(encodeLogFile(HEADER, blocks))
    expect(column(csv, 4)).toEqual(['1000009000', '1000009000', '1000001500'])
    expect(meta.time_sync).toEqual([
      { esp_us: 1000, unix_us: 1_000_001_500 },
      { esp_us: 8000, unix_us: 1_000_008_000 },
    ])
  })

  it('SESSION_END mid-file sets clean_end and the blocks after it are still written', async () => {
    const blocks: BlockSpec[] = [RAMP[0], RAMP[1], { type: 'end', seq: 2 }, { ...RAMP[2], seq: 3 }, { ...RAMP[3], seq: 4 }]
    const { csv, meta } = await convert(encodeLogFile(HEADER, blocks))
    expect(csv).toBe(expectedCsv(blocks))
    expect(meta).toMatchObject({ clean_end: true, blocks_valid: 5, blocks_bad: 0, seq_gaps: 0, rows: { '1': 2 * MAX_SAMPLES, '2': 2 * MAX_SAMPLES } })
  })

  it('a sample_count 300 block is valid, skipped, keeps seq continuous and counts no overflow', async () => {
    const blocks: BlockSpec[] = [RAMP[0], { ...RAMP[1], count: 300, flags: FLAG_FIFO_OVERFLOW }, RAMP[2], RAMP[3]]
    const { csv, meta } = await convert(encodeLogFile(HEADER, blocks))
    expect(csv).toBe(expectedCsv(blocks))
    expect(meta).toMatchObject({ blocks_valid: 4, blocks_bad: 0, seq_gaps: 0, fifo_overflows: 0, rows: { '1': 2 * MAX_SAMPLES, '2': MAX_SAMPLES } })
  })

  it('a bad-CRC block is bad, is first_bad, and makes a seq gap because last_seq does not advance', async () => {
    const blocks: BlockSpec[] = [RAMP[0], { ...RAMP[1], corruptCrc: true }, RAMP[2], RAMP[3]]
    const { csv, meta } = await convert(encodeLogFile(HEADER, blocks))
    expect(csv).toBe(expectedCsv(blocks))
    expect(meta).toMatchObject({ blocks_valid: 3, blocks_bad: 1, unused_tail_blocks: 0, first_bad: 1, seq_gaps: 1, rows: { '1': 2 * MAX_SAMPLES, '2': MAX_SAMPLES } })
  })

  it('0xFF and 0x00 fill blocks are bad to bin2csv, unused to us, never first_bad; data after them still converts', async () => {
    const blocks: BlockSpec[] = [RAMP[0], { type: 'fill', byte: 0xff }, { type: 'fill', byte: 0xff }, { type: 'fill', byte: 0x00 }, RAMP[1]]
    const { csv, meta } = await convert(encodeLogFile(HEADER, blocks))
    expect(csv).toBe(expectedCsv(blocks))
    expect(meta).toMatchObject({ blocks_valid: 2, blocks_bad: 3, unused_tail_blocks: 3, first_bad: null, seq_gaps: 0 })
  })

  it('a garbage block is bad and first_bad, not unused', async () => {
    const blocks: BlockSpec[] = [RAMP[0], RAMP[1], { type: 'raw', bytes: fill(BLOCK_BYTES, 0x5a) }, { type: 'fill', byte: 0xff }, RAMP[2]]
    const { csv, meta } = await convert(encodeLogFile(HEADER, blocks))
    expect(csv).toBe(expectedCsv(blocks))
    expect(meta).toMatchObject({ blocks_valid: 3, blocks_bad: 2, unused_tail_blocks: 1, first_bad: 2, seq_gaps: 0 })
  })

  it('a trailing partial block is ignored', async () => {
    const whole = await convert(encodeLogFile(HEADER, RAMP))
    for (const tail of [1, 31, 32, 33, BLOCK_BYTES - 1]) {
      const file = encodeLogFile(HEADER, RAMP, fill(tail, 0xff))
      const { csv, meta, progress } = await convert(file)
      expect(csv).toBe(whole.csv)
      expect(meta).toEqual(whole.meta)
      expect(progress[progress.length - 1]).toMatchObject({ phase: 'convert', bytesDone: file.length, bytesTotal: file.length })
    }
  })

  it('flag bit 1 counts a FIFO overflow, bit 2 (TS clamped) is ignored', async () => {
    const blocks: BlockSpec[] = [
      { ...RAMP[0], flags: FLAG_FIFO_OVERFLOW },
      { ...RAMP[1], flags: FLAG_TS_CLAMPED },
      { ...RAMP[2], flags: FLAG_FIFO_OVERFLOW | FLAG_TS_CLAMPED },
      RAMP[3],
    ]
    const { csv, meta } = await convert(encodeLogFile(HEADER, blocks))
    expect(csv).toBe(expectedCsv(blocks))
    expect(meta.fifo_overflows).toBe(2)
  })

  it('one global last_seq spans every valid block type', async () => {
    const blocks: BlockSpec[] = [
      { ...RAMP[0], seq: 0 },
      { type: 'sync', seq: 2, espUs: 1n, unixUs: 2n }, // gap 0 -> 2
      { ...RAMP[1], seq: 3 },
      { type: 'end', seq: 5 }, // gap 3 -> 5
      { ...RAMP[2], seq: 6 },
    ]
    const { meta } = await convert(encodeLogFile(HEADER, blocks))
    expect(meta).toMatchObject({ blocks_valid: 5, seq_gaps: 2, clean_end: true })
  })

  it('a header-only file gets a header-only CSV and an all-zero meta', async () => {
    const { csv, meta } = await convert(encodeLogFile(HEADER, []))
    expect(csv).toBe(CSV_HEADER)
    expect(meta).toMatchObject({ blocks_valid: 0, blocks_bad: 0, seq_gaps: 0, fifo_overflows: 0, clean_end: false, time_sync: [], rows: {}, first_bad: null })
  })
})

describe('convertLog progress and sink', () => {
  it('reports one prepass then one convert event per chunk, monotonic, ending at the file size', async () => {
    const file = encodeLogFile(HEADER, RAMP)
    const { progress, meta } = await convert(file)
    const pre = progress.filter((p) => p.phase === 'prepass')
    const conv = progress.filter((p) => p.phase === 'convert')
    expect(pre.length + conv.length).toBe(progress.length)
    expect(progress.findIndex((p) => p.phase === 'convert')).toBe(pre.length)
    expect(pre).toHaveLength(RAMP.length)
    expect(conv).toHaveLength(RAMP.length)
    for (const phase of [pre, conv]) {
      for (let i = 1; i < phase.length; i++) {
        expect(phase[i].bytesDone).toBeGreaterThan(phase[i - 1].bytesDone)
        expect(phase[i].rows).toBeGreaterThanOrEqual(phase[i - 1].rows)
      }
      expect(phase[phase.length - 1].bytesDone).toBe(file.length)
      expect(phase.every((p) => p.bytesTotal === file.length)).toBe(true)
    }
    expect(pre.every((p) => p.rows === 0)).toBe(true)
    expect(conv[conv.length - 1].rows).toBe(6 * MAX_SAMPLES)
    expect(conv.map((p) => p.rows)).toEqual([1, 2, 3, 4, 5, 6].map((n) => n * MAX_SAMPLES))
    expect(meta.rows).toEqual({ '1': 3 * MAX_SAMPLES, '2': 3 * MAX_SAMPLES })
  })

  it('hands the sink the header, the tables and the identical sample sequence in both passes', async () => {
    const blocks: BlockSpec[] = [
      ...RAMP.slice(0, 2),
      { ...RAMP[2], corruptCrc: true },
      { type: 'sync', seq: 3, espUs: 1n, unixUs: 2n },
      { ...RAMP[3], count: 300 },
      { type: 'fill', byte: 0xff },
      { ...RAMP[4], seq: 5 },
      { type: 'end', seq: 6 },
    ]
    const sink = new RecordingSink()
    const { meta, result } = await convert(encodeLogFile(HEADER, blocks), { sink })
    expect(sink.starts).toBe(1)
    expect(sink.pass1DoneCalls).toBe(1)
    expect(sink.header).toBe(result.header)
    expect(sink.tables).toBe(result.tables)
    expect(sink.samples).toBe(3 * MAX_SAMPLES)
    expect(sink.samples).toBe(Object.values(meta.rows).reduce((a, b) => a + b, 0))
    expect(sink.samePasses()).toBe(true)
    // First sample of the file: sensor 1, t = base + dt 0, the ramp's first counts.
    const first = sink.sample(0)
    const s0 = rampSamples(1, 0)[0]
    expect(first).toEqual({ sid: 1, tUs: 5_000_000, counts: [s0.ax, s0.ay, s0.az, s0.gx, s0.gy, s0.gz] })
    // Last sample: block RAMP[4] (sensor 1, third block => base + 2 spans), dt = 289 * 156.
    const last = sink.sample(sink.samples - 1)
    expect(last.sid).toBe(1)
    expect(last.tUs).toBe(5_000_000 + 2 * 45_240 + 289 * 156)
  })
})

describe('convertLog failures leave nothing behind', () => {
  it('a bad header throws format with bin2csv.py\'s message and creates nothing', async () => {
    const good = encodeLogFile(HEADER, RAMP.slice(0, 2))
    const cases: [Uint8Array, string][] = [
      [new Uint8Array(100), 'file shorter than 512 B header'],
      [new Uint8Array(0), 'file shorter than 512 B header'],
      [withByteFlipped(good, 0), 'bad file magic 0x534B59B1'],
      [encodeLogFile({ ...HEADER, formatVersion: 2 }, []), 'unsupported format_version=2 header_size=512'],
      [encodeLogFile({ ...HEADER, headerSize: 256 }, []), 'unsupported format_version=1 header_size=256'],
    ]
    const crcBroken = withByteFlipped(good, 30)
    const stored = crc32Of(good.subarray(0, FH_OFF_CRC))
    const calc = crc32Of(crcBroken.subarray(0, FH_OFF_CRC))
    const hex = (n: number) => n.toString(16).toUpperCase().padStart(8, '0')
    cases.push([crcBroken, `header CRC mismatch (stored ${hex(stored)}, calc ${hex(calc)})`])
    for (const [bytes, message] of cases) {
      const { err, out } = await failing(bytes)
      expect(err.code, message).toBe('format')
      expect(err.message).toBe(message)
      expect(out.files.size).toBe(0)
    }
    // The magic message is the LE u32 of the flipped first byte, as Python prints it.
    expect(encodeFileHeader()[0]).toBe(0x4e)
  })

  it('abort during the pre-pass or the main pass rejects with aborted and leaves no csv or meta', async () => {
    const file = encodeLogFile(HEADER, RAMP)
    for (const phase of ['prepass', 'convert'] as const) {
      const ac = new AbortController()
      const seen: ConvertProgress[] = []
      const { err, out } = await failing(file, {
        signal: ac.signal,
        onProgress: (p) => {
          seen.push(p)
          if (p.phase === phase && seen.filter((q) => q.phase === phase).length === 2) ac.abort()
        },
      })
      expect(err.code, phase).toBe('aborted')
      expect(out.files.size, phase).toBe(0)
      expect(seen.filter((p) => p.phase === phase).length, phase).toBe(2)
    }
    const early = new AbortController()
    early.abort()
    const { err, out } = await failing(file, { signal: early.signal })
    expect(err.code).toBe('aborted')
    expect(out.files.size).toBe(0)
  })

  it('a read failure mid-file is read, with the DOMException name in the detail', async () => {
    const file = encodeLogFile(HEADER, RAMP)
    for (const after of [SMALL_CHUNK, SMALL_CHUNK + 2 * BLOCK_BYTES]) {
      const srcDir = new MemDir({ [SOURCE]: file }).failRead(SOURCE, after)
      const { err, out } = await failing(file, {}, new MemDir(), srcDir)
      expect(err.code).toBe('read')
      expect(err.message).toContain('NotReadableError')
      expect(out.files.size).toBe(0)
    }
  })

  it('a write or close failure is write; a failure on the meta keeps the committed CSV', async () => {
    const file = encodeLogFile(HEADER, RAMP)
    const csvFails = await failing(file, {}, new MemDir().failWrite(CSV))
    expect(csvFails.err.code).toBe('write')
    expect(csvFails.err.message).toContain('QuotaExceededError')
    expect(csvFails.out.files.size).toBe(0)

    const csvCloseFails = await failing(file, {}, new MemDir().failClose(CSV))
    expect(csvCloseFails.err.code).toBe('write')
    expect(csvCloseFails.out.files.size).toBe(0)

    const metaFails = await failing(file, {}, new MemDir().failWrite(META))
    expect(metaFails.err.code).toBe('write')
    expect([...metaFails.out.files.keys()]).toEqual([CSV])
    expect(metaFails.out.text(CSV)).toBe(expectedCsv(RAMP))
  })

  it('a base_ts_us beyond 2^53 is range, in the pre-pass with a sink and in the main pass without', async () => {
    const blocks: BlockSpec[] = [RAMP[0], { ...RAMP[1], baseTsUs: 1n << 60n }]
    const file = encodeLogFile(HEADER, blocks)
    const withSink = await failing(file, { sink: new RecordingSink() })
    expect(withSink.err.code).toBe('range')
    expect(withSink.err.message).toContain('block 1')
    expect(withSink.out.files.size).toBe(0)
    const noSink = await failing(file)
    expect(noSink.err.code).toBe('range')
    expect(noSink.out.files.size).toBe(0)
    // A unix offset beyond 2^53 too.
    const syncTooFar: BlockSpec[] = [RAMP[0], { type: 'sync', seq: 1, espUs: 0n, unixUs: 1n << 60n }]
    const { err } = await failing(encodeLogFile(HEADER, syncTooFar))
    expect(err.code).toBe('range')
  })

  it('abort wins over any other failure', async () => {
    const file = encodeLogFile(HEADER, RAMP)
    const ac = new AbortController()
    const srcDir = new MemDir({ [SOURCE]: file })
    const src = await srcDir.open(SOURCE)
    const out = new MemDir()
    const run = convertLog(src, out, {
      ...BASE_OPTS,
      signal: ac.signal,
      onProgress: (p) => {
        if (p.phase === 'convert') {
          ac.abort()
          srcDir.unplug()
        }
      },
    })
    await expect(run).rejects.toMatchObject({ code: 'aborted' })
    expect(out.files.size).toBe(0)
    expect(out.openSinks).toBe(0)
  })
})
