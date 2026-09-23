import { describe, expect, it } from 'vitest'
import { STORAGE_BAD_BLOCK_CONFIRM_MAX } from '../config'
import { BLOCK_BYTES, FILE_HEADER_BYTES, FLAG_FIFO_OVERFLOW, FLAG_TS_CLAMPED } from './binFormat'
import { encodeLogFile, fill, rampSamples, withByteFlipped, type BlockSpec } from './fixtures/binEncode'
import { fixtureBytes, LOG_0010_HEAD64_BIN } from './fixtures/load'
import { BlockScanner, scanBytes, scanResultsEqual, type ScanResult } from './scanner'

const MIB = 1024 * 1024
const CHUNK_SIZES = [1, 100, 4095, 4096, 4 * MIB + 512]
const sample = fixtureBytes(LOG_0010_HEAD64_BIN)

function scanChunked(bytes: Uint8Array, chunk: number): ScanResult {
  const s = new BlockScanner()
  for (let off = 0; off < bytes.length; off += chunk) s.push(bytes.subarray(off, Math.min(off + chunk, bytes.length)))
  return s.finish()
}

/** Header + a mixed bag of blocks + a 3584 B partial 0xFF tail. */
const SYNTHETIC_BLOCKS: BlockSpec[] = [
  { type: 'imu', sensorId: 1, seq: 0, baseTsUs: 1000n, samples: rampSamples(10) },
  { type: 'imu', sensorId: 2, seq: 1, baseTsUs: 1000n, samples: rampSamples(20, 5) },
  { type: 'sync', seq: 2, espUs: 2000n, unixUs: 1_700_000_000_000_000n, source: 0 },
  { type: 'imu', sensorId: 1, seq: 3, samples: rampSamples(5), flags: FLAG_FIFO_OVERFLOW },
  { type: 'imu', sensorId: 2, seq: 4, samples: rampSamples(5), corruptCrc: true },
  { type: 'imu', sensorId: 2, seq: 6, samples: rampSamples(7), flags: FLAG_TS_CLAMPED },
  { type: 'imu', sensorId: 1, seq: 7, count: 300 },
  { type: 'end', seq: 8 },
  { type: 'imu', sensorId: 1, seq: 9, samples: rampSamples(3) },
  { type: 'fill', byte: 0xff },
  { type: 'fill', byte: 0x00 },
]
const synthetic = encodeLogFile({ sessionId: 5 }, SYNTHETIC_BLOCKS, fill(3584, 0xff))

describe('BlockScanner on the real 1.1.0 sample head', () => {
  it('sees 64 valid blocks, no gaps, no end, 52 clamped batches, 9280 samples per sensor', () => {
    const r = scanBytes(sample)
    expect(r.header?.fw).toBe('1.1.0')
    expect(r.header?.sessionId).toBe(10)
    expect(r.headerError).toBeUndefined()
    expect(r).toMatchObject({
      blocks: 64,
      valid: 64,
      bad: 0,
      unused: 0,
      firstBad: null,
      firstUnused: null,
      trailingBytes: 0,
      seqGaps: 0,
      cleanEnd: false,
      syncBlocks: [],
      samples: { 1: 9280, 2: 9280 },
      fifoOverflows: 0,
      tsClamped: 52,
      lastSeq: 63,
    })
  })

  it.each(CHUNK_SIZES)('gives the identical result with %d-byte pushes', (chunk) => {
    expect(scanResultsEqual(scanChunked(sample, chunk), scanBytes(sample))).toBe(true)
  })
})

describe('BlockScanner on synthetic files', () => {
  it('classifies every block, keeps going past bad blocks and END, counts gaps and syncs', () => {
    const r = scanBytes(synthetic)
    expect(r.header?.sessionId).toBe(5)
    expect(r).toMatchObject({
      blocks: 11,
      valid: 8,
      bad: 1,
      unused: 2,
      firstBad: 4,
      firstUnused: 9,
      trailingBytes: 3584,
      seqGaps: 1,
      cleanEnd: true,
      samples: { 1: 18, 2: 27 },
      fifoOverflows: 1,
      tsClamped: 1,
      lastSeq: 9,
    })
    expect(r.syncBlocks).toEqual([{ espUs: 2000n, unixUs: 1_700_000_000_000_000n, source: 0 }])
  })

  it.each(CHUNK_SIZES)('is boundary-agnostic at %d-byte pushes', (chunk) => {
    expect(scanResultsEqual(scanChunked(synthetic, chunk), scanBytes(synthetic))).toBe(true)
  })

  it('handles a 0x00 tail, a 0xFF tail and a partial trailing block', () => {
    const blocks: BlockSpec[] = [{ type: 'imu', sensorId: 1, seq: 0, samples: rampSamples(2) }]
    const zeros = scanBytes(encodeLogFile({}, [...blocks, { type: 'fill', byte: 0 }, { type: 'fill', byte: 0 }]))
    expect(zeros).toMatchObject({ blocks: 3, valid: 1, unused: 2, bad: 0, firstUnused: 1, trailingBytes: 0 })
    const ffs = scanBytes(encodeLogFile({}, [...blocks, { type: 'fill', byte: 0xff }], fill(100, 0xff)))
    expect(ffs).toMatchObject({ blocks: 2, valid: 1, unused: 1, trailingBytes: 100 })
    const partial = scanBytes(encodeLogFile({}, blocks, fill(BLOCK_BYTES - 1, 0x00)))
    expect(partial).toMatchObject({ blocks: 1, valid: 1, trailingBytes: BLOCK_BYTES - 1 })
  })

  it('counts a bad block mid-file and stale valid blocks after unused ones', () => {
    const r = scanBytes(
      encodeLogFile({}, [
        { type: 'imu', sensorId: 1, seq: 0, samples: rampSamples(1) },
        { type: 'raw', bytes: fill(BLOCK_BYTES, 0x5a) },
        { type: 'imu', sensorId: 1, seq: 1, samples: rampSamples(1) },
        { type: 'fill', byte: 0xff },
        { type: 'imu', sensorId: 2, seq: 40, samples: rampSamples(1) },
      ]),
    )
    expect(r).toMatchObject({ blocks: 5, valid: 3, bad: 1, unused: 1, firstBad: 1, firstUnused: 3, seqGaps: 1, lastSeq: 40 })
  })

  it('reports a short file, a header-only file and a bad header while still scanning blocks', () => {
    const short = scanBytes(new Uint8Array(100))
    expect(short).toMatchObject({ header: null, headerError: 'short', blocks: 0, trailingBytes: 100 })
    const headerOnly = scanBytes(encodeLogFile({}, []))
    expect(headerOnly.header).not.toBeNull()
    expect(headerOnly).toMatchObject({ blocks: 0, trailingBytes: 0 })
    const badMagic = scanBytes(withByteFlipped(encodeLogFile({}, [{ type: 'end', seq: 0 }]), 0))
    expect(badMagic).toMatchObject({ header: null, headerError: 'magic', blocks: 1, valid: 1, cleanEnd: true })
    const empty = scanBytes(new Uint8Array(0))
    expect(empty).toMatchObject({ header: null, headerError: 'short', blocks: 0, trailingBytes: 0 })
  })

  it('refuses pushes after finish', () => {
    const s = new BlockScanner()
    s.push(sample.subarray(0, FILE_HEADER_BYTES))
    s.finish()
    expect(() => s.push(new Uint8Array(1))).toThrow()
  })
})

describe('scanResultsEqual', () => {
  it('is true for equal scans and false when anything differs', () => {
    expect(scanResultsEqual(scanBytes(synthetic), scanBytes(synthetic))).toBe(true)
    const flippedSample = withByteFlipped(synthetic, FILE_HEADER_BYTES + 100)
    expect(scanResultsEqual(scanBytes(synthetic), scanBytes(flippedSample))).toBe(false)
    const flippedHeader = withByteFlipped(synthetic, 30)
    expect(scanResultsEqual(scanBytes(synthetic), scanBytes(flippedHeader))).toBe(false)
    const shorter = synthetic.subarray(0, synthetic.length - 1)
    expect(scanResultsEqual(scanBytes(synthetic), scanBytes(shorter))).toBe(false)
  })
})

describe('bad-block bookkeeping for the transfer re-read', () => {
  it('lists bad block indices in file order, capped at STORAGE_BAD_BLOCK_CONFIRM_MAX', () => {
    expect(scanBytes(synthetic).badBlocks).toEqual([4])
    const many: BlockSpec[] = []
    for (let i = 0; i < STORAGE_BAD_BLOCK_CONFIRM_MAX + 8; i++) {
      many.push({ type: 'imu', sensorId: 1, seq: i, samples: rampSamples(2), corruptCrc: true })
    }
    const big = scanBytes(encodeLogFile({}, many))
    expect(big.bad).toBe(STORAGE_BAD_BLOCK_CONFIRM_MAX + 8)
    expect(big.badBlocks).toHaveLength(STORAGE_BAD_BLOCK_CONFIRM_MAX)
    expect(big.badBlocks[0]).toBe(0)
    expect(big.badBlocks[STORAGE_BAD_BLOCK_CONFIRM_MAX - 1]).toBe(STORAGE_BAD_BLOCK_CONFIRM_MAX - 1)
  })

  it('scanResultsEqual notices a different bad-block list', () => {
    const a = scanBytes(synthetic)
    expect(scanResultsEqual(a, { ...a, badBlocks: [5] })).toBe(false)
    expect(scanResultsEqual(a, { ...a, badBlocks: [4] })).toBe(true)
  })
})
