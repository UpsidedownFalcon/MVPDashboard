import { describe, expect, it } from 'vitest'
import { fbm, hash32, valueNoise } from './noise'

describe('hash32', () => {
  it('is deterministic and stays in [0, 1)', () => {
    for (const [seed, k] of [[1, 0], [1, 1], [101, 450000], [-7, -3], [0, 2 ** 31]] as const) {
      const a = hash32(seed, k)
      expect(a).toBe(hash32(seed, k))
      expect(a).toBeGreaterThanOrEqual(0)
      expect(a).toBeLessThan(1)
    }
  })

  it('floors non-integer indices', () => {
    expect(hash32(5, 12.9)).toBe(hash32(5, 12))
    expect(hash32(5, -0.5)).toBe(hash32(5, -1))
  })

  it('separates seeds and indices', () => {
    expect(hash32(1, 1)).not.toBe(hash32(2, 1))
    expect(hash32(1, 1)).not.toBe(hash32(1, 2))
  })
})

describe('valueNoise / fbm', () => {
  it('is deterministic, bounded and continuous', () => {
    let prev = valueNoise(9, 0, 4)
    for (let x = 0.01; x < 40; x += 0.01) {
      const v = valueNoise(9, x, 4)
      expect(v).toBe(valueNoise(9, x, 4))
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThanOrEqual(1)
      expect(Math.abs(v - prev)).toBeLessThan(0.01)
      prev = v
    }
  })

  it('handles negative positions', () => {
    expect(Number.isFinite(valueNoise(3, -123.4, 7))).toBe(true)
    expect(Number.isFinite(fbm(3, -9999, 5))).toBe(true)
  })

  it('rejects a non-positive period', () => {
    expect(() => valueNoise(1, 1, 0)).toThrow(RangeError)
  })

  it('keeps fbm inside [0, 1]', () => {
    for (let x = -50; x < 50; x += 0.37) {
      const v = fbm(21, x, 3)
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThanOrEqual(1)
    }
  })
})
