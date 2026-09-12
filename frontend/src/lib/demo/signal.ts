// Pure signal functions over a DemoProfile (STAGE4 D3): the same function
// answers "what is the value at instant s" for the 60 Hz live trace, the
// history buckets, the window aggregates, the forecasts and the insight
// evidence, so every view of a soldier agrees with every other view.

import { fbm, hash32, valueNoise } from './noise'
import { SERIES_KEYS, type DemoProfile, type Keyframe, type SeriesKey } from './profiles'

export interface Sample {
  /** m1..m5 in backend order */
  m: [number, number, number, number, number]
  c: number
}

export interface BucketStats {
  m: number[]
  sd: number[]
  composite: { avg: number; min: number; max: number; sd: number }
  quality: number
  /** rows the backend would have had for this span (60 Hz) */
  n: number
}

const SERIES_SEED: Record<SeriesKey, number> = { m1: 1, m2: 2, m3: 3, m4: 4, m5: 5, c: 6 }

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v
}

function clampSeries(key: SeriesKey, v: number): number {
  return key === 'm5' ? clamp(v, -100, 100) : clamp(v, 0, 100)
}

/** Piecewise smoothstep interpolation, clamped to the first/last keyframe so
 *  a tab left open for hours (or a query before the session) never produces
 *  NaN or an out-of-range value. */
export function envelopeAt(keyframes: Keyframe[], s: number): number {
  const first = keyframes[0]
  const last = keyframes[keyframes.length - 1]
  if (s <= first.s) return first.v
  if (s >= last.s) return last.v
  let i = 1
  while (keyframes[i].s < s) i++
  const a = keyframes[i - 1]
  const b = keyframes[i]
  const f = (s - a.s) / (b.s - a.s)
  const t = f * f * (3 - 2 * f)
  return a.v + (b.v - a.v) * t
}

/** One series at instant s: envelope + slow wander + per-sample jitter. */
export function seriesAt(profile: DemoProfile, key: SeriesKey, s: number): number {
  const env = envelopeAt(profile.envelopes[key], s)
  const tx = profile.texture[key]
  const wander = (2 * fbm(profile.seed * 7 + SERIES_SEED[key], s, tx.period) - 1) * tx.amp
  const jitter = 0.15 * (hash32(profile.seed * 11 + SERIES_SEED[key], Math.floor(s * 60)) - 0.5)
  const v = Math.round((env + wander + jitter) * 100) / 100
  return clampSeries(key, v)
}

export function sampleAt(profile: DemoProfile, s: number): Sample {
  return {
    m: [
      seriesAt(profile, 'm1', s),
      seriesAt(profile, 'm2', s),
      seriesAt(profile, 'm3', s),
      seriesAt(profile, 'm4', s),
      seriesAt(profile, 'm5', s),
    ],
    c: seriesAt(profile, 'c', s),
  }
}

/** Link quality 0..1: the profile's base with a slow, small wobble. */
export function qualityAt(profile: DemoProfile, s: number): number {
  const q = profile.qualityBase + 0.02 * (valueNoise(profile.seed * 13 + 99, s, 45) - 0.5)
  return Math.round(clamp(q, 0, 1) * 1000) / 1000
}

function meanSd(values: number[]): [number, number] {
  const n = values.length
  if (n === 0) return [0, 0]
  const mean = values.reduce((a, b) => a + b, 0) / n
  if (n < 2) return [mean, 0]
  const ss = values.reduce((a, b) => a + (b - mean) * (b - mean), 0)
  return [mean, Math.sqrt(ss / (n - 1))]
}

/** Aggregate over [s0, s1) by sub-sampling the same signal the live trace
 *  draws. Returns null when the whole span precedes the session; a span that
 *  straddles the start is clipped (partial coverage, as the backend would). */
export function bucketStats(profile: DemoProfile, s0: number, s1: number, n = 24): BucketStats | null {
  if (!(s1 > 0) || !(s1 > s0)) return null
  const start = Math.max(0, s0)
  const step = (s1 - start) / n
  const cols: Record<SeriesKey, number[]> = { m1: [], m2: [], m3: [], m4: [], m5: [], c: [] }
  const qs: number[] = []
  for (let i = 0; i < n; i++) {
    const s = start + (i + 0.5) * step
    for (const key of SERIES_KEYS) cols[key].push(seriesAt(profile, key, s))
    qs.push(qualityAt(profile, s))
  }
  const m: number[] = []
  const sd: number[] = []
  for (const key of ['m1', 'm2', 'm3', 'm4', 'm5'] as const) {
    const [mu, sigma] = meanSd(cols[key])
    m.push(Math.round(mu * 100) / 100)
    sd.push(Math.round(sigma * 100) / 100)
  }
  const [cAvg, cSd] = meanSd(cols.c)
  return {
    m,
    sd,
    composite: {
      avg: Math.round(cAvg * 100) / 100,
      min: Math.min(...cols.c),
      max: Math.max(...cols.c),
      sd: Math.round(cSd * 100) / 100,
    },
    quality: Math.round((qs.reduce((a, b) => a + b, 0) / n) * 1000) / 1000,
    n: Math.round((s1 - start) * 60),
  }
}

/** Composite trend from the noise-free envelope, in points per minute, so
 *  forecasts and trend arrows do not jitter with the texture. */
export function slopePerMin(profile: DemoProfile, s: number, spanS = 300): number {
  const now = envelopeAt(profile.envelopes.c, s)
  const before = envelopeAt(profile.envelopes.c, s - spanS)
  return (now - before) / (spanS / 60)
}

/** Composite trend of the window ending at `s` against the preceding equal
 *  window, with the backend's dead band max(2, 0.5 * pooled sd)
 *  (backend/api/queries.py _trend). Shared by the windows route and the
 *  rule engine so a trend arrow and a "still trending up" reason agree. */
export function windowTrend(profile: DemoProfile, s: number, windowS: number): 'up' | 'down' | 'flat' {
  const cur = bucketStats(profile, s - windowS, s, 30)
  const prev = bucketStats(profile, s - 2 * windowS, s - windowS, 30)
  if (!cur || !prev) return 'flat'
  const delta = cur.composite.avg - prev.composite.avg
  const pooled = Math.sqrt((cur.composite.sd ** 2 + prev.composite.sd ** 2) / 2)
  const band = Math.max(2, 0.5 * pooled)
  return delta > band ? 'up' : delta < -band ? 'down' : 'flat'
}
