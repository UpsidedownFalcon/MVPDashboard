import { describe, expect, it } from 'vitest'
import { encodeBlock } from '../fixtures/binEncode'
import { collectSync, compareSync, pickSync, sortSyncs } from './syncs'
import type { SyncAnchor } from './types'

const A = (espUs: bigint, unixUs: bigint): SyncAnchor => ({ espUs, unixUs })

/** bin2csv.pick_sync, transliterated, as the oracle for the binary search. */
function pickLinear(sorted: SyncAnchor[], t: bigint): SyncAnchor {
  let best = sorted[0]
  for (const s of sorted) {
    if (s.espUs <= t) best = s
    else break
  }
  return best
}

describe('collectSync', () => {
  it('reads esp_us and unix_us from the sync payload', () => {
    const block = encodeBlock({ type: 'sync', seq: 3, espUs: 123_456_789_012n, unixUs: 1_700_000_000_000_000n, source: 1 })
    expect(collectSync(block)).toEqual(A(123_456_789_012n, 1_700_000_000_000_000n))
    const max = encodeBlock({ type: 'sync', seq: 0, espUs: 2n ** 64n - 1n, unixUs: 0n })
    expect(collectSync(max)).toEqual(A(2n ** 64n - 1n, 0n))
  })
})

describe('sortSyncs', () => {
  it('orders by (espUs, unixUs) like Python sorted() on tuples and leaves the input alone', () => {
    const input = [A(30n, 1n), A(10n, 9n), A(20n, 5n), A(10n, 2n), A(20n, 5n)]
    const sorted = sortSyncs(input)
    expect(sorted).toEqual([A(10n, 2n), A(10n, 9n), A(20n, 5n), A(20n, 5n), A(30n, 1n)])
    expect(input[0]).toEqual(A(30n, 1n))
    expect(compareSync(A(1n, 1n), A(1n, 1n))).toBe(0)
    expect(compareSync(A(1n, 2n), A(1n, 1n))).toBe(1)
    expect(compareSync(A(0n, 99n), A(1n, 0n))).toBe(-1)
  })
})

describe('pickSync', () => {
  const sorted = sortSyncs([A(1000n, 5_000n), A(3000n, 7_100n), A(3000n, 7_200n), A(9000n, 13_000n)])

  it('takes the latest anchor at or before the base, else the first (backwards extrapolation)', () => {
    expect(pickSync(sorted, 0n)).toEqual(A(1000n, 5_000n))
    expect(pickSync(sorted, 999n)).toEqual(A(1000n, 5_000n))
    expect(pickSync(sorted, 1000n)).toEqual(A(1000n, 5_000n))
    expect(pickSync(sorted, 2999n)).toEqual(A(1000n, 5_000n))
    // Equal esp values: the later tuple (larger unix) wins, as in Python.
    expect(pickSync(sorted, 3000n)).toEqual(A(3000n, 7_200n))
    expect(pickSync(sorted, 8999n)).toEqual(A(3000n, 7_200n))
    expect(pickSync(sorted, 9000n)).toEqual(A(9000n, 13_000n))
    expect(pickSync(sorted, 2n ** 64n - 1n)).toEqual(A(9000n, 13_000n))
  })

  it('agrees with the linear scan of bin2csv.py on every boundary of a long list', () => {
    const many: SyncAnchor[] = []
    for (let i = 0; i < 40; i++) many.push(A(BigInt(i * 700), BigInt(i * 700 + 1_000_000)))
    for (let t = -1; t <= 40 * 700 + 1; t++) {
      const base = BigInt(t < 0 ? 0 : t)
      expect(pickSync(many, base)).toBe(pickLinear(many, base))
    }
    expect(pickSync([A(5n, 6n)], 4n)).toEqual(A(5n, 6n))
    expect(pickSync([A(5n, 6n)], 5n)).toEqual(A(5n, 6n))
  })

  it('refuses an empty list', () => {
    expect(() => pickSync([], 0n)).toThrow(RangeError)
  })
})
