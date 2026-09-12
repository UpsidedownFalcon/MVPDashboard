// Forecast points for a demo soldier: a linear trend from the noise-free
// composite envelope with a symmetric interval that widens with the horizon.
// Shared by the REST shape (lib/demo/api) and the rule engine (lib/demo/
// insights), so "projected 96 within 1h" on a card is the same number the
// Projections tab draws.

import type { ForecastPoint } from '../api'
import { durationToSeconds } from '../format'
import { DEMO_HORIZONS, sessionSeconds, type DemoProfile } from './profiles'
import { envelopeAt, slopePerMin } from './signal'
import { iso } from './time'

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v
}

const r2 = (v: number) => Math.round(v * 100) / 100

export function forecastPoints(profile: DemoProfile, madeAtMs: number): ForecastPoint[] {
  const sMade = sessionSeconds(madeAtMs)
  const base = envelopeAt(profile.envelopes.c, sMade)
  const slope = slopePerMin(profile, sMade)
  return DEMO_HORIZONS.map((horizon) => {
    const hs = durationToSeconds(horizon)
    const pred = clamp(base + (slope * hs) / 60, 0, 100)
    const half = 3 + 2.2 * Math.sqrt(hs / 600)
    return {
      horizon,
      target_time: iso(madeAtMs + hs * 1000),
      pred: r2(pred),
      ci_low: r2(clamp(pred - half, 0, 100)),
      ci_high: r2(clamp(pred + half, 0, 100)),
    }
  })
}
