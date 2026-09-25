import { describe, expect, it } from 'vitest'
import { parseFileHeader, type FileHeader } from '../binFormat'
import { encodeFileHeader } from '../fixtures/binEncode'
import { buildMeta, metaText, safeNumber, type IntegrityCounts } from './meta'

function header(): FileHeader {
  const parsed = parseFileHeader(encodeFileHeader({ fw: '1.2.0', deviceId: 7, sourceId: 1, sessionId: 42 }))
  if (!parsed.ok) throw new Error(parsed.code)
  return parsed.header
}

const COUNTS: IntegrityCounts = {
  blocksValid: 10,
  blocksBad: 3,
  unusedTailBlocks: 2,
  firstBad: 4,
  seqGaps: 1,
  fifoOverflows: 2,
  cleanEnd: true,
}

describe('buildMeta', () => {
  it('lays out bin2csv.py\'s keys in its order, then the two extras', () => {
    const rows = new Map<number, number>([[2, 5], [1, 3]])
    const meta = buildMeta(header(), 'LOG_0042.BIN', COUNTS, [{ espUs: 1000n, unixUs: 1_700_000_000_000_000n }], rows)
    expect(Object.keys(meta)).toEqual([
      'fw', 'device_id', 'source_id', 'sensor_count', 'odr_hz', 'accel_fs_g', 'gyro_fs_dps',
      'accel_scale', 'gyro_scale', 'session_id', 'source_file', 'units', 'blocks_valid',
      'blocks_bad', 'seq_gaps', 'fifo_overflows', 'clean_end', 'time_sync', 'rows',
      'unused_tail_blocks', 'first_bad',
    ])
    expect(meta).toMatchObject({
      fw: '1.2.0',
      device_id: 7,
      source_id: 1,
      sensor_count: 2,
      odr_hz: 6400,
      accel_fs_g: 32,
      gyro_fs_dps: 4000,
      accel_scale: 32 / 32768,
      gyro_scale: 4000 / 32768,
      session_id: 42,
      source_file: 'LOG_0042.BIN',
      units: 'physical',
      blocks_valid: 10,
      blocks_bad: 3,
      seq_gaps: 1,
      fifo_overflows: 2,
      clean_end: true,
      time_sync: [{ esp_us: 1000, unix_us: 1_700_000_000_000_000 }],
      rows: { '1': 3, '2': 5 },
      unused_tail_blocks: 2,
      first_bad: 4,
    })
    expect(buildMeta(header(), 'x', { ...COUNTS, firstBad: null }, [], new Map()).first_bad).toBeNull()
  })

  it('metaText is indent-1 JSON with LF and no trailing newline', () => {
    const meta = buildMeta(header(), 'LOG_0042.BIN', COUNTS, [], new Map([[1, 3]]))
    const text = metaText(meta)
    expect(text.startsWith('{\n "fw": "1.2.0",\n "device_id": 7,\n')).toBe(true)
    expect(text.endsWith('\n "first_bad": 4\n}')).toBe(true)
    expect(text).not.toContain('\r')
    expect(text).toContain('\n "rows": {\n  "1": 3\n },\n')
    expect(text).toContain('\n "time_sync": [],\n')
    expect(JSON.parse(text)).toEqual(meta)
  })

  it('refuses a u64 that does not fit a double', () => {
    expect(safeNumber(2n ** 53n - 1n, 'x')).toBe(2 ** 53 - 1)
    expect(() => safeNumber(2n ** 53n, 'x')).toThrow(expect.objectContaining({ name: 'ConvertError', code: 'range' }))
    expect(() => buildMeta(header(), 'x', COUNTS, [{ espUs: 1n, unixUs: 2n ** 64n - 1n }], new Map())).toThrow(
      expect.objectContaining({ code: 'range', message: expect.stringContaining('time_sync[0].unix_us') }),
    )
  })
})
