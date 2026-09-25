import { describe, expect, it } from 'vitest'
import { TS_FILTER_WINDOW, TsFilter, tsFilterReference } from './tsFilter'

const THRESHOLD = 1_000_000

/** Push the whole series, draining after every push and after flush, and
 *  return the decisions in order (the sink's usage pattern). */
function stream(ts: number[], thr = THRESHOLD): boolean[] {
  const f = new TsFilter(thr)
  const out: boolean[] = []
  for (const t of ts) {
    f.push(t)
    while (f.available > 0) out.push(f.shift())
  }
  f.flush()
  while (f.available > 0) out.push(f.shift())
  return out
}

/** Deterministic pseudo-random monotone series (dt 100..300 us) with
 *  outliers injected in both directions at every `every`-th index. */
function monotone(n: number, seed: number, every: number): number[] {
  let s = seed
  const rnd = () => {
    s = (s * 1103515245 + 12345) % 2147483648
    return s / 2147483648
  }
  const ts: number[] = []
  let t = 5_000_000
  for (let i = 0; i < n; i++) {
    t += 100 + Math.floor(rnd() * 200)
    let v = t
    if (every > 0 && i % every === 3) v += 2_500_000
    if (every > 0 && i % every === 7) v -= 1_500_000
    ts.push(v)
  }
  return ts
}

describe('TsFilter', () => {
  it.each([0, 1, 5, 11, 12, 1000])('equals the O(N*11) reference on N = %d with outliers both ways', (n) => {
    const ts = monotone(n, 42 + n, 13)
    const got = stream(ts)
    expect(got).toHaveLength(n)
    expect(got).toEqual(tsFilterReference(ts, THRESHOLD))
    if (n >= 12) {
      expect(got.filter((g) => !g).length).toBeGreaterThan(0)
      expect(got.filter((g) => g).length).toBeGreaterThan(0)
    }
  })

  it('rejects an outlier in either direction and keeps its neighbours', () => {
    const ts = monotone(40, 7, 0)
    ts[20] += 3_000_000
    ts[30] -= 3_000_000
    const got = stream(ts)
    expect(got[20]).toBe(false)
    expect(got[30]).toBe(false)
    expect(got.filter((g) => !g)).toHaveLength(2)
  })

  it('decisions trail pushes by five and flush releases the rest', () => {
    const f = new TsFilter(THRESHOLD)
    for (let i = 0; i < 5; i++) {
      f.push(i * 156)
      expect(f.available).toBe(0)
    }
    f.push(5 * 156)
    expect(f.available).toBe(1)
    expect(f.shift()).toBe(true)
    f.push(6 * 156)
    expect(f.available).toBe(1)
    f.shift()
    f.flush()
    expect(f.available).toBe(5)
    expect(() => {
      for (let i = 0; i < 6; i++) f.shift()
    }).toThrow()
  })

  it('edge windows: the first index sees ts[0..5] and the last sees ts[N-6..N-1]', () => {
    // N = 12: window(0) = 0..5 (6 values, even), window(11) = 6..11 (even),
    // window(5) = 0..10 (11 values), window(6) = 1..11.
    const base = Array.from({ length: 12 }, (_, i) => i * 156)
    // Make index 0 an outlier only if its window is the 6-value edge window:
    // shift ts[0] down so that |ts[0] - median(ts[0..5])| exceeds the
    // threshold, while the full 11-window median would also reject it. Then
    // a second series where the even-count mean matters.
    const a = base.slice()
    a[0] -= 1_000_500
    expect(stream(a)).toEqual(tsFilterReference(a, THRESHOLD))
    expect(stream(a)[0]).toBe(false)
    expect(stream(a).slice(1).every((g) => g)).toBe(true)

    const b = base.slice()
    b[11] += 1_000_500
    expect(stream(b)).toEqual(tsFilterReference(b, THRESHOLD))
    expect(stream(b)[11]).toBe(false)
  })

  it('an even-count window takes the mean of the two middle values (pandas)', () => {
    // Six samples: every window is the whole series (6 values). The median
    // is (0 + 2e6) / 2 = 1e6, so with threshold 1e6 every sample is exactly
    // on the <= boundary and kept; with 999_999 every sample is rejected.
    const ts = [0, 0, 0, 2_000_000, 2_000_000, 2_000_000]
    expect(stream(ts, 1_000_000)).toEqual([true, true, true, true, true, true])
    expect(stream(ts, 999_999)).toEqual([false, false, false, false, false, false])
    expect(tsFilterReference(ts, 999_999)).toEqual([false, false, false, false, false, false])
  })

  it('a series shorter than the window uses min_periods=1 semantics', () => {
    expect(stream([5])).toEqual([true])
    expect(stream([5, 5_000_000])).toEqual([false, false]) // median 2.5e6, both 2.5e6 away
    expect(stream([1, 2, 3])).toEqual([true, true, true])
    expect(stream([])).toEqual([])
  })

  it('exposes the window size', () => {
    expect(TS_FILTER_WINDOW).toBe(11)
  })
})
