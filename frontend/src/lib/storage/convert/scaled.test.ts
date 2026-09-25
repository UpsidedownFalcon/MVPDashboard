import { describe, expect, it } from 'vitest'
import {
  ACCEL_DECIMALS,
  buildValueTables,
  COUNTS_PER_FULL_SCALE,
  formatScaled,
  formatScaledExact,
  GYRO_DECIMALS,
  TABLE_OFFSET,
  TABLE_SIZE,
  textTable,
} from './scaled'

// Truth values printed by CPython 3.12.13 for f"{count * scale:.{d}f}" with
// scale = struct.unpack('<f', struct.pack('<f', fs / 32768))[0], i.e. the
// float32 the firmware stores. Rows 2 and 3 of the real LOG_0010.csv
// (-694, 408, 632 at +-32 g; 8, 48, 16 at +-4000 dps) are among them.
const PY: [number, number, number, string][] = [
  [32, 6, -694, '-0.677734'],
  [32, 6, 408, '0.398438'],
  [32, 6, 632, '0.617188'],
  [32, 6, 8, '0.007812'],
  [32, 6, 24, '0.023438'],
  [32, 6, -8, '-0.007812'],
  [32, 6, 0, '0.000000'],
  [32, 6, 1, '0.000977'],
  [32, 6, -1, '-0.000977'],
  [32, 6, 32767, '31.999023'],
  [32, 6, -32768, '-32.000000'],
  [32, 6, 16, '0.015625'],
  [32, 6, 40, '0.039062'],
  [32, 6, 12345, '12.055664'],
  [32, 6, -12345, '-12.055664'],
  [4000, 4, 8, '0.9766'],
  [4000, 4, 48, '5.8594'],
  [4000, 4, 16, '1.9531'],
  [4000, 4, 32, '3.9062'],
  [4000, 4, 96, '11.7188'],
  [4000, 4, -32, '-3.9062'],
  [4000, 4, 0, '0.0000'],
  [4000, 4, 1, '0.1221'],
  [4000, 4, -1, '-0.1221'],
  [4000, 4, 32767, '3999.8779'],
  [4000, 4, -32768, '-4000.0000'],
  [4000, 4, 2, '0.2441'],
  [4000, 4, 4, '0.4883'],
  [4000, 4, 20000, '2441.4062'],
  [4000, 4, -20000, '-2441.4062'],
  [2, 6, 1, '0.000061'],
  [2, 6, -1, '-0.000061'],
  [2, 6, 3, '0.000183'],
  [2, 6, 32767, '1.999939'],
  [2, 6, -32768, '-2.000000'],
  [2, 6, 12, '0.000732'],
  [125, 4, 1, '0.0038'],
  [125, 4, -1, '-0.0038'],
  [125, 4, 32767, '124.9962'],
  [125, 4, -32768, '-125.0000'],
  [125, 4, 7, '0.0267'],
  [125, 4, 64, '0.2441'],
]

const f32 = (fs: number) => Math.fround(fs / COUNTS_PER_FULL_SCALE)

describe('formatScaled', () => {
  it.each(PY)('fs %d, %d decimals, count %d -> %s (Python)', (fs, d, count, want) => {
    expect(formatScaled(count, fs, d)).toBe(want)
    expect(formatScaledExact(count, f32(fs), d)).toBe(want)
  })

  it('ties: every 16th accel count and every 64th gyro count sit exactly halfway', () => {
    // 8/1024 = 0.0078125 -> 0.007812 (even); 24/1024 = 0.0234375 -> 0.023438 (odd rounds up)
    expect(formatScaled(8, 32, 6)).toBe('0.007812')
    expect(formatScaled(24, 32, 6)).toBe('0.023438')
    expect((8 / 1024).toFixed(6)).toBe('0.007813') // what toFixed would have written
    expect(formatScaled(32, 4000, 4)).toBe('3.9062')
    expect(formatScaled(96, 4000, 4)).toBe('11.7188')
  })

  it('the float32 header scales are exactly fs / 32768 for every allowed full scale', () => {
    for (const fs of [2, 4, 8, 16, 32, 125, 250, 500, 1000, 2000, 4000]) {
      expect(f32(fs)).toBe(fs / COUNTS_PER_FULL_SCALE)
    }
  })

  it('integer path equals the exact path over the whole i16 range (32 g, 4000 dps)', () => {
    for (let count = -32768; count <= 32767; count++) {
      expect(formatScaled(count, 32, ACCEL_DECIMALS)).toBe(formatScaledExact(count, f32(32), ACCEL_DECIMALS))
      expect(formatScaled(count, 4000, GYRO_DECIMALS)).toBe(formatScaledExact(count, f32(4000), GYRO_DECIMALS))
    }
  })

  it('integer path equals the exact path on a stride for the other full scales', () => {
    for (const fs of [2, 4, 8, 16]) {
      for (let count = -32768; count <= 32767; count += 13) {
        expect(formatScaled(count, fs, ACCEL_DECIMALS)).toBe(formatScaledExact(count, f32(fs), ACCEL_DECIMALS))
      }
    }
    for (const fs of [125, 250, 500, 1000, 2000]) {
      for (let count = -32768; count <= 32767; count += 13) {
        expect(formatScaled(count, fs, GYRO_DECIMALS)).toBe(formatScaledExact(count, f32(fs), GYRO_DECIMALS))
      }
    }
  })
})

describe('tables', () => {
  it('buildValueTables indexes count + 32768 and parses back its own text', () => {
    const t = buildValueTables({ accelFsG: 32, gyroFsDps: 4000, accelScale: f32(32), gyroScale: f32(4000) })
    expect(t.accelText).toHaveLength(TABLE_SIZE)
    expect(t.gyroText).toHaveLength(TABLE_SIZE)
    expect(t.accelText[TABLE_OFFSET + 408]).toBe('0.398438')
    expect(t.accelValue[TABLE_OFFSET + 408]).toBe(0.398438)
    expect(t.gyroText[TABLE_OFFSET - 32]).toBe('-3.9062')
    expect(t.gyroValue[TABLE_OFFSET - 32]).toBe(-3.9062)
    expect(t.accelText[TABLE_OFFSET]).toBe('0.000000')
    expect(t.accelValue[TABLE_OFFSET]).toBe(0)
  })

  it('a header scale that is not fs / 32768 takes the exact path', () => {
    const odd = 0.001 // not dyadic: Python would format count * 0.001 exactly the same way
    const table = textTable(32, odd, 6)
    expect(table[TABLE_OFFSET + 1]).toBe(formatScaledExact(1, odd, 6))
    expect(table[TABLE_OFFSET + 1]).toBe('0.001000')
    expect(table[TABLE_OFFSET + 3]).toBe('0.003000')
  })
})
