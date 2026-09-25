// The synthetic LOG_NNNN.BIN fixtures of this directory as encoder specs
// (agent-docs/03_PLAN_csv_summary 4.8). convert/fixtures.write.test.ts
// writes the .bin files from these specs when STORAGE_WRITE_FIXTURES=1 and
// otherwise asserts the committed bytes still equal them;
// scripts/storage_goldens.py derives the goldens from the .bin files with
// the vendored bin2csv.py / sensor_stats.py. Deterministic: rampSamples()
// with a seed per block, and a per-sensor timeline whose base_ts_us advances
// by exactly one block span so consecutive blocks of a sensor are gap-free
// unless a block is skipped on purpose. Test-only, not a test itself.
import { BLOCK_BYTES, FLAG_FIFO_OVERFLOW, FLAG_TS_CLAMPED, MAX_SAMPLES, SENSOR_SHIN, SENSOR_THIGH } from '../../binFormat'
import { encodeLogFile, fill, rampSamples, type BlockSpec, type HeaderSpec } from '../binEncode'

/** rampSamples() steps dt_us by this per sample: the 6410 Hz of the real logs. */
export const RAMP_DT_US = 156
/** Time one full block covers; the next block of the same sensor starts here. */
export const BLOCK_SPAN_US = BigInt(MAX_SAMPLES * RAMP_DT_US)
/** esp_timer at the first block: well above zero (1000 s of uptime). */
export const BASE0_US = 1_000_000_000n
/** Unix microseconds the first sync maps BASE0 + its esp offset to (2023-11-14). */
export const UNIX0_US = 1_700_000_000_000_000n
/** A trailing partial block (bin2csv ignores it). */
export const PARTIAL_TAIL_BYTES = 3584
/** Byte of the garbage block: neither magic nor an unused fill pattern. */
export const GARBAGE_BYTE = 0x5a
/** Every fixture stays well under this (the repo keeps them all). */
export const MAX_FIXTURE_BYTES = 600 * 1024
/** rampSamples seed stride per block, so no two blocks share values. */
const SEED_STRIDE = 1000

export interface FixtureExtras {
  /** Whole blocks of all-0x00 / all-0xFF. */
  unusedTailBlocks: number
  /** Index of the first block that is neither valid nor unused. */
  firstBad: number | null
}

export interface ConvertFixture {
  /** Golden stem: `sync_mid`. */
  name: string
  /** File name in this directory: `sync_mid.bin`. */
  file: string
  header: HeaderSpec
  blocks: BlockSpec[]
  tail: Uint8Array
  /** The dashboard's meta extras, known from the layout. */
  extras: FixtureExtras
  /** Decodable IMU blocks (sample_count <= MAX_SAMPLES, good CRC) per sensor. */
  decodableBlocks: Record<number, number>
}

/** Builds a block list with one seq counter and one gap-free clock per sensor. */
class Timeline {
  readonly blocks: BlockSpec[] = []
  readonly extras: FixtureExtras = { unusedTailBlocks: 0, firstBad: null }
  readonly decodableBlocks: Record<number, number> = { [SENSOR_THIGH]: 0, [SENSOR_SHIN]: 0 }
  private seq = 0
  private readonly next = new Map<number, bigint>()

  imu(sensorId: number, opts: { count?: number; flags?: number; corruptCrc?: boolean } = {}): this {
    const base = this.next.get(sensorId) ?? BASE0_US
    const samples = rampSamples(MAX_SAMPLES, this.blocks.length * SEED_STRIDE)
    this.blocks.push({ type: 'imu', sensorId, seq: this.seq++, baseTsUs: base, samples, count: opts.count, flags: opts.flags, corruptCrc: opts.corruptCrc })
    this.next.set(sensorId, base + BLOCK_SPAN_US)
    if (opts.corruptCrc) this.bad()
    else if ((opts.count ?? MAX_SAMPLES) <= MAX_SAMPLES) this.decodableBlocks[sensorId]++
    return this
  }

  /** n blocks of the thigh and the shin, interleaved like the firmware writes them. */
  pair(n: number): this {
    for (let i = 0; i < n; i++) this.imu(SENSOR_THIGH).imu(SENSOR_SHIN)
    return this
  }

  sync(espOffsetUs: bigint, unixOffsetUs: bigint): this {
    this.blocks.push({ type: 'sync', seq: this.seq++, espUs: BASE0_US + espOffsetUs, unixUs: UNIX0_US + unixOffsetUs, source: 0 })
    return this
  }

  end(): this {
    this.blocks.push({ type: 'end', seq: this.seq++ })
    return this
  }

  /** Leave one seq number out: the next valid block makes a seq gap. */
  skipSeq(): this {
    this.seq++
    return this
  }

  fill(byte: 0x00 | 0xff, n: number): this {
    for (let i = 0; i < n; i++) {
      this.blocks.push({ type: 'fill', byte })
      this.extras.unusedTailBlocks++
    }
    return this
  }

  garbage(): this {
    this.blocks.push({ type: 'raw', bytes: fill(BLOCK_BYTES, GARBAGE_BYTE) })
    return this.bad()
  }

  private bad(): this {
    if (this.extras.firstBad === null) this.extras.firstBad = this.blocks.length - 1
    return this
  }
}

function fixture(name: string, sessionId: number, build: (t: Timeline) => void, tail = new Uint8Array(0)): ConvertFixture {
  const t = new Timeline()
  build(t)
  return {
    name,
    file: `${name}.bin`,
    header: { deviceId: 3, sourceId: 0, sessionId },
    blocks: t.blocks,
    tail,
    extras: t.extras,
    decodableBlocks: t.decodableBlocks,
  }
}

const FIFO = FLAG_FIFO_OVERFLOW
const CLAMPED = FLAG_TS_CLAMPED
const OVERSIZE_COUNT = MAX_SAMPLES + 10

export const CONVERT_FIXTURES: readonly ConvertFixture[] = [
  // Two syncs: blocks before the first extrapolate backwards, later blocks
  // take the latest anchor at or before their base.
  fixture('sync_mid', 11, (t) => {
    t.pair(2).sync(50_000n, 0n).pair(3).sync(200_000n, 150_500n).pair(2)
  }),
  // SESSION_END with data after it: clean_end true, every row still written.
  fixture('end_mid', 12, (t) => {
    t.pair(3).end().pair(3)
  }),
  // A sample_count 300 block: valid (seq continues) but its samples are skipped.
  fixture('oversize', 13, (t) => {
    t.pair(2).imu(SENSOR_THIGH, { count: OVERSIZE_COUNT }).imu(SENSOR_SHIN).pair(2)
  }),
  // A bad-CRC block (counted bad, makes a seq gap) and an explicit seq gap.
  fixture('badcrc', 14, (t) => {
    t.pair(2).imu(SENSOR_THIGH, { corruptCrc: true }).imu(SENSOR_SHIN).pair(1).skipSeq().pair(2)
  }),
  // flags 1 (FIFO overflow, counted), 2 (TS clamped, ignored), 3 (counted).
  fixture('flags', 15, (t) => {
    t.pair(1).imu(SENSOR_THIGH, { flags: FIFO }).imu(SENSOR_SHIN, { flags: CLAMPED }).imu(SENSOR_THIGH, { flags: FIFO | CLAMPED }).imu(SENSOR_SHIN).pair(1)
  }),
  // A preallocated tail: 0xFF then 0x00 blocks (bad to bin2csv, unused to us).
  fixture('tail_ff', 16, (t) => {
    t.pair(3).fill(0xff, 3).fill(0x00, 2)
  }),
  // A trailing partial block.
  fixture('partial', 17, (t) => t.pair(3), fill(PARTIAL_TAIL_BYTES, 0xff)),
  // Everything above in one file, syncs out of file order, at least 24
  // decodable IMU blocks per sensor for several noise windows.
  fixture(
    'kitchen',
    18,
    (t) => {
      t.pair(2)
        .sync(100_000n, 100_000n)
        .pair(2)
        .imu(SENSOR_THIGH, { flags: FIFO })
        .imu(SENSOR_SHIN, { flags: CLAMPED })
        .imu(SENSOR_THIGH, { flags: FIFO | CLAMPED })
        .imu(SENSOR_SHIN)
        .imu(SENSOR_THIGH, { count: OVERSIZE_COUNT })
        .imu(SENSOR_SHIN)
        .pair(2)
        .imu(SENSOR_THIGH, { corruptCrc: true })
        .imu(SENSOR_SHIN)
        .skipSeq()
        .pair(1)
        .garbage()
        .sync(50_000n, 50_300n)
        .pair(2)
        .end()
        .pair(12)
        .sync(900_000n, 900_700n)
        .pair(3)
        .fill(0xff, 3)
        .fill(0x00, 2)
    },
    fill(PARTIAL_TAIL_BYTES, 0xff),
  ),
]

export function fixtureBytesOf(f: ConvertFixture): Uint8Array {
  return encodeLogFile(f.header, f.blocks, f.tail)
}

export function fixtureByName(name: string): ConvertFixture {
  const f = CONVERT_FIXTURES.find((x) => x.name === name)
  if (!f) throw new Error(`no convert fixture named ${name}`)
  return f
}
