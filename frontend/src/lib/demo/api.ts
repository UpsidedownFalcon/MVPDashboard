// Synthetic REST responses for demo soldiers (STAGE4 D4). Every function is a
// pure function of (profile, nowMs) and returns exactly the lib/api.ts shape
// the real route would, so no component can tell the difference. `nowMs` is
// always a parameter: only the lib/api.ts boundary reads the clock.

import type {
  Device,
  Forecasts,
  History,
  HistoryBucket,
  InsightDecision,
  Recent,
  Sensor,
  WindowEntry,
} from '../api'
import { HISTORY_MAX_BUCKETS } from '../config'
import { durationToSeconds, evenBucketCount } from '../format'
import { valueNoise } from './noise'
import {
  DEMO_MODEL_VERSION,
  DEMO_PROFILES,
  DEMO_WINDOWS,
  profileFor,
  sessionSeconds,
  type DemoProfile,
} from './profiles'
import { forecastPoints } from './forecast'
import { bucketStats, qualityAt, sampleAt, windowTrend } from './signal'
import { iso } from './time'

export { iso }
export { demoAdviceTimeline, demoCurrentAdvice, demoInsights, DEMO_HOLD_S, DEMO_MAX_ACTIONS } from './insights'

/** Same layout as the backend's default LIMB_MAP (TRD §3). */
const LIMBS: readonly [number, number, string][] = [
  [0, 1, 'left_shin'],
  [0, 2, 'left_thigh'],
  [1, 1, 'right_thigh'],
  [1, 2, 'right_shin'],
]

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v
}

// --- devices ------------------------------------------------------------------

/** Battery %: the profile's start level minus a linear drain, floored so a
 *  tab left open for a day never reads 0 (which the UI reserves for "dead"). */
export function demoSoc(profile: DemoProfile, s: number): number {
  return clamp(Math.round(profile.soc0 - (profile.socDrainPerHour * s) / 3600), 5, 100)
}

/** Four mapped sensors, each streaming near the measured 640 Hz. */
export function demoSensors(profile: DemoProfile, nowMs: number): Sensor[] {
  const s = sessionSeconds(nowMs)
  return LIMBS.map(([source_id, sensor_id, limb], i) => ({
    source_id,
    sensor_id,
    limb,
    rate_hz: Math.round((639.5 + 1.5 * (valueNoise(profile.seed * 17 + i, s, 30) - 0.5)) * 10) / 10,
    last_seen: iso(nowMs),
  }))
}

/** GET /api/devices rows for the five soldiers: always online, seen now. */
export function demoDevices(nowMs: number): Device[] {
  const s = sessionSeconds(nowMs)
  return DEMO_PROFILES.map((p) => ({
    device_id: p.id,
    display_name: p.display_name,
    online: true,
    last_seen: iso(nowMs),
    quality: qualityAt(p, s),
    soc: demoSoc(p, s),
    sensors: demoSensors(p, nowMs),
  }))
}

// --- windows / history ----------------------------------------------------------

const NULL5 = (): (number | null)[] => [null, null, null, null, null]

/** GET /api/metrics/windows: one entry per configured window, trend against
 *  the preceding equal window with the backend's dead band (max(2, 0.5 sd)). */
export function demoWindows(id: string, nowMs: number): { windows: WindowEntry[] } {
  const p = profileFor(id)
  if (!p) return { windows: [] }
  const sNow = sessionSeconds(nowMs)
  const windows = DEMO_WINDOWS.map((label): WindowEntry => {
    const W = durationToSeconds(label)
    const from = iso(nowMs - W * 1000)
    const cur = bucketStats(p, sNow - W, sNow, 30)
    if (!cur) {
      return {
        window: label, from, m: NULL5(), sd: NULL5(),
        composite: { avg: null, min: null, max: null, sd: null },
        quality: null, coverage: 0, trend: 'flat',
      }
    }
    const trend = windowTrend(p, sNow, W)
    const covered = sNow - Math.max(0, sNow - W)
    return {
      window: label,
      from,
      m: cur.m,
      sd: cur.sd,
      composite: cur.composite,
      quality: cur.quality,
      coverage: clamp(covered / W, 0, 1),
      trend,
    }
  })
  return { windows }
}

/** GET /api/metrics/history: `buckets` equal spans over the window, null
 *  before the session started. A count that does not divide the window falls
 *  back to evenBucketCount, exactly as the frontend would have asked. */
export function demoHistory(id: string, window: string, buckets: number, nowMs: number): History {
  const p = profileFor(id)
  const W = durationToSeconds(window)
  if (!p || W <= 0) return { device_id: id, window, from: iso(nowMs), bucket_s: 0, buckets: [] }
  let n = Math.floor(buckets)
  if (n < 1 || W % n !== 0 || (W > 300 && (W / n) % 60 !== 0)) {
    n = evenBucketCount(window, HISTORY_MAX_BUCKETS)
  }
  const span = W / n
  const fromMs = nowMs - W * 1000
  const sFrom = sessionSeconds(fromMs)
  const out: (HistoryBucket | null)[] = []
  for (let k = 0; k < n; k++) {
    const stats = bucketStats(p, sFrom + k * span, sFrom + (k + 1) * span, 12)
    if (!stats) {
      out.push(null)
      continue
    }
    out.push({
      t: iso(fromMs + k * span * 1000),
      m: stats.m,
      composite: { avg: stats.composite.avg, min: stats.composite.min, max: stats.composite.max },
      quality: stats.quality,
    })
  }
  return { device_id: id, window, from: iso(fromMs), bucket_s: span, buckets: out }
}

// --- forecasts ------------------------------------------------------------------

/** GET /api/forecasts/latest: a linear trend from the noise-free composite
 *  envelope with a symmetric interval that widens with the horizon. `made_at`
 *  sits on the minute, like the real PREDICT_INTERVAL_S job. */
export function demoForecasts(id: string, nowMs: number): Forecasts {
  const p = profileFor(id)
  const madeAtMs = Math.floor(nowMs / 60_000) * 60_000
  if (!p) return { made_at: iso(madeAtMs), model_version: DEMO_MODEL_VERSION, provisional: false, points: [] }
  return { made_at: iso(madeAtMs), model_version: DEMO_MODEL_VERSION, provisional: false, points: forecastPoints(p, madeAtMs) }
}

// --- recent ---------------------------------------------------------------------

/** GET /api/metrics/recent. Defensive only: the WS backfill path never asks
 *  for a soldier (their ticks never arrive on the socket). */
export function demoRecent(id: string, seconds: number, nowMs: number): Recent {
  const p = profileFor(id)
  const t0Ms = nowMs - seconds * 1000
  if (!p) return { device_id: id, t0: null, rows: [] }
  const rows: Recent['rows'] = []
  const count = Math.max(0, Math.floor(seconds * 60))
  for (let k = 0; k < count; k++) {
    const offsetMs = (k * 1000) / 60
    const s = sessionSeconds(t0Ms + offsetMs)
    const { m, c } = sampleAt(p, s)
    rows.push([Math.round(offsetMs), ...m, c, qualityAt(p, s)])
  }
  return { device_id: id, t0: iso(t0Ms), rows }
}

/** POST /api/insights/decisions on a soldier: echo the shape, store nothing
 *  (STAGE4 R2: the controls look live but change nothing). */
export function demoDecision(
  body: { decision: 'adopted' | 'overridden'; note?: string },
  nowMs: number,
): InsightDecision {
  return {
    decision: body.decision,
    note: body.decision === 'adopted' ? null : (body.note ?? null),
    decided_by: null,
    decided_at: iso(nowMs),
  }
}
