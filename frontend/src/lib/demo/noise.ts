// Stateless, index-keyed noise for the demo generator (STAGE4 D3). Everything
// is a pure function of (seed, position): the same inputs always give the same
// output, there is no sequential PRNG stream to drift, and any point in time
// can be evaluated in any order. That is what makes history, windows,
// forecasts and the live trace agree with each other and survive reloads.

/** Integer mix of (seed, k) -> [0, 1). `k` is floored, so callers may pass a
 *  sample index derived from a float; negative values are fine. */
export function hash32(seed: number, k: number): number {
  let h = (Math.imul(seed | 0, 0x9e3779b1) ^ Math.imul(Math.floor(k) | 0, 0x85ebca77)) >>> 0
  h ^= h >>> 15
  h = Math.imul(h, 0x2c1b3c6d) >>> 0
  h ^= h >>> 12
  h = Math.imul(h, 0x297a2d39) >>> 0
  h ^= h >>> 15
  return (h >>> 0) / 4294967296
}

/** Smoothly interpolated noise in [0, 1] with knots every `period` units of x. */
export function valueNoise(seed: number, x: number, period: number): number {
  if (!(period > 0)) throw new RangeError('valueNoise: period must be > 0')
  const u = x / period
  const i = Math.floor(u)
  const f = u - i
  const t = f * f * (3 - 2 * f) // smoothstep
  const a = hash32(seed, i)
  const b = hash32(seed, i + 1)
  return a + (b - a) * t
}

/** Two-octave fractal noise in [0, 1]: a slow wander with a faster ripple on
 *  top, normalised so the range stays [0, 1] whatever the octave count. */
export function fbm(seed: number, x: number, period: number, octaves = 2): number {
  let sum = 0
  let norm = 0
  let amp = 1
  let p = period
  let s = seed
  for (let o = 0; o < octaves; o++) {
    sum += amp * valueNoise(s, x, p)
    norm += amp
    amp *= 0.45
    p /= 2.1
    s += 1013
  }
  return sum / norm
}
