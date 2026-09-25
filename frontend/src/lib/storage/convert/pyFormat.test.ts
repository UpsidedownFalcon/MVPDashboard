import { describe, expect, it } from 'vitest'
import { ljust, pyF, pyFThousands, pyG, pyRound, rjust, thousands, utcStamp } from './pyFormat'

// Truth values printed by CPython 3.12.13 (`uv run python` over the repo
// venv) for the format specs named in each table; the snippet's output was
// pasted here as literals (scratch pytrial/fmt_truth.py).

const G: [number, string][] = [
  [32, '32'],
  [4000, '4000'],
  [0.5, '0.5'],
  [1e-5, '1e-05'],
  [1, '1'],
  [20, '20'],
  [22.0, '22'],
  [1000000, '1e+06'],
  [999999, '999999'],
  [1234567, '1.23457e+06'],
  [0.0001, '0.0001'],
  [0.00001234, '1.234e-05'],
  [123456.7, '123457'],
  [1234565, '1.23456e+06'],
  [12345650.5, '1.23457e+07'],
  [2 ** -9, '0.00195312'],
  [9.999995e-5, '0.0001'],
  [0.1, '0.1'],
  [100, '100'],
  [1e21, '1e+21'],
  [1e-7, '1e-07'],
  [-32, '-32'],
  [-0.0, '-0'],
  [0.0, '0'],
  [3.14159265, '3.14159'],
  [2.5, '2.5'],
  [1e100, '1e+100'],
  [6.5, '6.5'],
  [999999.5, '1e+06'],
  [0.000123456789, '0.000123457'],
  [123456789, '1.23457e+08'],
  [1.5e-5, '1.5e-05'],
  [12345.678, '12345.7'],
  [0.30000000000000004, '0.3'],
  [1e6, '1e+06'],
  [1e5, '100000'],
  [100000.5, '100000'],
  [Number.NaN, 'nan'],
  [Number.POSITIVE_INFINITY, 'inf'],
  [Number.NEGATIVE_INFINITY, '-inf'],
]

const THOUSANDS: [number, string][] = [
  [0, '0'],
  [5, '5'],
  [18560, '18,560'],
  [6400, '6,400'],
  [1234567, '1,234,567'],
  [-1234, '-1,234'],
  [999, '999'],
  [1000, '1,000'],
  [-999, '-999'],
  [123456789012, '123,456,789,012'],
]

const F_THOUSANDS: [number, number, string][] = [
  [6400, 0, '6,400'],
  [18560.4, 0, '18,560'],
  [1234567.5, 0, '1,234,568'],
  [2.5, 0, '2'],
  [0.5, 0, '0'],
  [-1234.5, 0, '-1,234'],
  [1234.5678, 2, '1,234.57'],
  [999.999, 2, '1,000.00'],
  [1e6, 1, '1,000,000.0'],
  [Number.NaN, 0, 'nan'],
]

const ROUND: [number, number][] = [
  [141.02564102564102, 141],
  [0.5, 0],
  [1.5, 2],
  [2.5, 2],
  [-0.5, 0],
  [-1.5, -2],
  [22000 / 156, 141],
  [22000 / 157, 140],
  [2.4999999999999996, 2],
  [1e15 + 0.5, 1000000000000000],
  [140.5, 140],
  [141.5, 142],
]

const UTC: [number, string][] = [
  [1700000000000000, '2023-11-14 22:13:20Z'],
  [0, '1970-01-01 00:00:00Z'],
  [1699999999999999, '2023-11-14 22:13:19Z'],
  [1700000000999999, '2023-11-14 22:13:20Z'],
  [1758800000123456, '2025-09-25 11:33:20Z'],
  [951782400000000, '2000-02-29 00:00:00Z'],
  [4102444799999999, '2099-12-31 23:59:59Z'],
  [1700000000000001, '2023-11-14 22:13:20Z'],
]

describe('pyG', () => {
  it.each(G)('%s -> %s', (x, want) => {
    expect(pyG(x)).toBe(want)
  })

  it('rounds an exact tie half to even where toPrecision rounds it up', () => {
    expect((2 ** -9).toPrecision(6)).toBe('0.00195313') // JS: ties go to the larger value
    expect(pyG(2 ** -9)).toBe('0.00195312') // Python: exact tie, 2 is even, stays
    expect(pyG(1234565)).toBe('1.23456e+06') // exact tie above 1e6 (BigInt path), 6 is even
    expect(pyG(1234575)).toBe('1.23458e+06') // exact tie, 7 is odd, rounds up
  })
})

describe('thousands and pyFThousands', () => {
  it.each(THOUSANDS)('{%d:,} -> %s', (n, want) => {
    expect(thousands(n)).toBe(want)
  })

  it.each(F_THOUSANDS)('{%s:,.%df} -> %s', (x, d, want) => {
    expect(pyFThousands(x, d)).toBe(want)
  })
})

describe('pyRound', () => {
  it.each(ROUND)('round(%s) -> %d', (x, want) => {
    expect(pyRound(x)).toBe(want)
  })

  it('keeps NaN', () => {
    expect(pyRound(Number.NaN)).toBeNaN()
  })
})

describe('utcStamp', () => {
  it.each(UTC)('%d us -> %s', (u, want) => {
    expect(utcStamp(u)).toBe(want)
  })
})

describe('pyF, rjust, ljust', () => {
  it('pyF is fixedHalfEven', () => {
    expect(pyF(0.125, 2)).toBe('0.12')
    expect(pyF(Number.NaN, 1)).toBe('nan')
    expect(pyF(98.38344649207889, 1)).toBe('98.4')
  })

  it('pad with spaces and never truncate (Python truth in the comment)', () => {
    // f"{'ab':>5}" '   ab', f"{'ab':<5}" 'ab   ', f"{'abcdefg':>5}" 'abcdefg'
    expect(rjust('ab', 5)).toBe('   ab')
    expect(ljust('ab', 5)).toBe('ab   ')
    expect(rjust('abcdefg', 5)).toBe('abcdefg')
    expect(ljust('abcdefg', 5)).toBe('abcdefg')
    expect(rjust('(1, 1)', 7)).toBe(' (1, 1)')
    expect(ljust('placement not set', 29)).toBe('placement not set            ')
    expect(rjust('', 9)).toBe('         ')
  })
})
