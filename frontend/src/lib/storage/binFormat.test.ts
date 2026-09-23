import { describe, expect, it } from 'vitest'
import {
  BH_OFF_CRC,
  BLOCK_BYTES,
  BLOCK_MAGIC,
  BLOCK_TYPE_IMU,
  blockCrcOk,
  classifyBlock,
  FH_OFF_CRC,
  FH_OFF_FORMAT_VERSION,
  FH_OFF_MAGIC,
  FILE_HEADER_BYTES,
  MAX_SAMPLES,
  parseFileHeader,
  readBlockHeader,
  SAMPLE_AREA_OFFSET,
  viewOf,
} from './binFormat'
import { encodeBlock, encodeFileHeader, fill, rampSamples, withByteFlipped } from './fixtures/binEncode'
import { fixtureBytes, LOG_0010_HEAD64_BIN } from './fixtures/load'

const sample = fixtureBytes(LOG_0010_HEAD64_BIN)
const block = (i: number) => sample.subarray(FILE_HEADER_BYTES + i * BLOCK_BYTES, FILE_HEADER_BYTES + (i + 1) * BLOCK_BYTES)

describe('parseFileHeader', () => {
  it('parses the real 1.1.0 sample header', () => {
    const r = parseFileHeader(sample)
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.header).toMatchObject({
      formatVersion: 1,
      headerSize: 512,
      fw: '1.1.0',
      deviceId: 1,
      sourceId: 0,
      sensorCount: 2,
      fifoWatermark: 8,
      odrHz: 6400,
      accelFsG: 32,
      gyroFsDps: 4000,
      accelScale: 0.0009765625,
      gyroScale: 0.1220703125,
      sessionId: 10,
      utcValid: false,
      crc32: 0xa26000e7,
    })
    expect(r.header.bootEspUs).toBe(5381966n)
  })

  it('rejects a short buffer, a bad magic, a bad version and a bad CRC', () => {
    expect(parseFileHeader(sample.subarray(0, 511))).toEqual({ ok: false, code: 'short' })
    expect(parseFileHeader(withByteFlipped(sample, FH_OFF_MAGIC))).toEqual({ ok: false, code: 'magic' })
    expect(parseFileHeader(withByteFlipped(sample, FH_OFF_FORMAT_VERSION))).toEqual({ ok: false, code: 'version' })
    expect(parseFileHeader(withByteFlipped(sample, 100))).toEqual({ ok: false, code: 'crc' })
    expect(parseFileHeader(withByteFlipped(sample, FH_OFF_CRC + 3))).toEqual({ ok: false, code: 'crc' })
  })

  it('round-trips the synthetic encoder', () => {
    const r = parseFileHeader(encodeFileHeader({ fw: '1.2.0', deviceId: 42, sourceId: 1, sessionId: 77, bootEspUs: 123n }))
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.header.fw).toBe('1.2.0')
    expect(r.header.deviceId).toBe(42)
    expect(r.header.sourceId).toBe(1)
    expect(r.header.sessionId).toBe(77)
    expect(r.header.bootEspUs).toBe(123n)
  })
})

describe('block header and CRC', () => {
  it('reads the first real block', () => {
    const h = readBlockHeader(viewOf(block(0)), 0)
    expect(h.magic).toBe(BLOCK_MAGIC)
    expect(h.type).toBe(BLOCK_TYPE_IMU)
    expect([1, 2]).toContain(h.sensorId)
    expect(h.seq).toBe(0)
    expect(h.sampleCount).toBe(MAX_SAMPLES)
    expect(h.baseTsUs).toBe(5444707n)
    expect(blockCrcOk(block(0))).toBe(true)
  })

  it('rejects a flipped byte anywhere in the block', () => {
    expect(blockCrcOk(withByteFlipped(block(0), SAMPLE_AREA_OFFSET + 5))).toBe(false)
    expect(blockCrcOk(withByteFlipped(block(0), BH_OFF_CRC))).toBe(false)
    expect(blockCrcOk(withByteFlipped(block(0), BLOCK_BYTES - 1))).toBe(false)
    expect(blockCrcOk(block(0).subarray(0, BLOCK_BYTES - 1))).toBe(false)
  })

  it('accepts encoder output and honours corruptCrc', () => {
    const good = encodeBlock({ type: 'imu', sensorId: 1, seq: 3, samples: rampSamples(10) })
    expect(blockCrcOk(good)).toBe(true)
    expect(blockCrcOk(encodeBlock({ type: 'imu', sensorId: 1, seq: 3, samples: rampSamples(10), corruptCrc: true }))).toBe(false)
  })
})

describe('classifyBlock', () => {
  it('classifies real, corrupted and unused blocks', () => {
    expect(classifyBlock(block(0))).toBe('valid')
    expect(classifyBlock(block(63))).toBe('valid')
    expect(classifyBlock(withByteFlipped(block(0), 1000))).toBe('bad')
    expect(classifyBlock(fill(BLOCK_BYTES, 0x00))).toBe('unused')
    expect(classifyBlock(fill(BLOCK_BYTES, 0xff))).toBe('unused')
    expect(classifyBlock(fill(BLOCK_BYTES, 0x5a))).toBe('bad')
    const almost = fill(BLOCK_BYTES, 0xff)
    almost[BLOCK_BYTES - 1] = 0xfe
    expect(classifyBlock(almost)).toBe('bad')
    expect(classifyBlock(fill(BLOCK_BYTES - 1, 0xff))).toBe('bad')
  })
})
