import { describe, expect, it } from 'vitest'
import { fixedHalfEven, roundDecimal, withPoint } from './decimal'

// Truth values printed by CPython 3.12.13 (`uv run python`) for
// f"{x:.{d}f}"; exact binary ties round to even, -0.0 keeps its sign.
const PY: [number, number, string][] = [
  [0.125, 2, '0.12'],
  [0.375, 2, '0.38'],
  [2.5, 0, '2'],
  [3.5, 0, '4'],
  [-0.0, 4, '-0.0000'],
  [-0.00001, 4, '-0.0000'],
  [1.005, 2, '1.00'],
  [2.675, 2, '2.67'],
  [1e21, 2, '1000000000000000000000.00'],
  [122.0703125, 0, '122'],
  [1098.5, 0, '1098'],
  [1099.5, 0, '1100'],
  [0.5, 0, '0'],
  [1.5, 0, '2'],
  [-2.5, 0, '-2'],
  [-0.125, 2, '-0.12'],
  [6.190000000000001, 2, '6.19'],
  [52.55, 1, '52.5'],
  [52.65, 1, '52.6'],
  [0.9765625, 4, '0.9766'],
  [1.007, 4, '1.0070'],
  [5e-7, 6, '0.000000'],
  [1.5e-6, 6, '0.000002'],
  [123456789.123456789, 3, '123456789.123'],
  [Number.NaN, 2, 'nan'],
  [Number.POSITIVE_INFINITY, 2, 'inf'],
  [Number.NEGATIVE_INFINITY, 2, '-inf'],
  [1 / 3, 6, '0.333333'],
  [2 / 3, 4, '0.6667'],
  [1e-320, 6, '0.000000'],
  [0, 0, '0'],
]

describe('fixedHalfEven', () => {
  it.each(PY)('formats %s with %d decimals like Python', (x, d, want) => {
    expect(fixedHalfEven(x, d)).toBe(want)
  })

  it('differs from toFixed exactly where Python does', () => {
    expect((0.125).toFixed(2)).toBe('0.13')
    expect(fixedHalfEven(0.125, 2)).toBe('0.12')
    expect((-0).toFixed(4)).toBe('0.0000')
    expect(fixedHalfEven(-0, 4)).toBe('-0.0000')
  })

  it('agrees with toFixed away from ties', () => {
    // toFixed is also exact on the binary value; only ties and -0 differ.
    for (let i = 0; i < 2000; i++) {
      const x = Math.sin(i * 12.9898) * 1000
      const s = fixedHalfEven(x, 3)
      const t = x.toFixed(3)
      if (s !== t) {
        // a genuine tie: the two answers differ by one unit in the last place
        expect(Math.abs(Number(s) - Number(t))).toBeCloseTo(0.001, 9)
      }
    }
  })
})

describe('roundDecimal and withPoint', () => {
  it('pads and rounds half-to-even', () => {
    expect(withPoint('7', 3)).toBe('0.007')
    expect(withPoint('1234', 2)).toBe('12.34')
    expect(withPoint('5', 0)).toBe('5')
    expect(roundDecimal(125n, 3, 2)).toBe('0.12')
    expect(roundDecimal(135n, 3, 2)).toBe('0.14')
    expect(roundDecimal(1251n, 4, 2)).toBe('0.13')
    expect(roundDecimal(5n, 1, 3)).toBe('0.500')
  })
})
