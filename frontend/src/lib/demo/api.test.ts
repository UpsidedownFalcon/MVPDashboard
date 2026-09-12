import { describe, expect, it } from 'vitest'
import { HISTORY_MAX_BUCKETS } from '../config'
import { durationToSeconds, evenBucketCount } from '../format'
import {
  demoDecision,
  demoDevices,
  demoForecasts,
  demoHistory,
  demoRecent,
  demoWindows,
  iso,
} from './api'
import { DEMO_COUNT } from './ids'
import { DEMO_HORIZONS, DEMO_PROFILES, DEMO_WINDOWS, SESSION_AGE_S, SESSION_START_MS } from './profiles'

/** "now" as the generator sees it on a fresh page load: 2 h 05 min in. */
const NOW = SESSION_START_MS + SESSION_AGE_S * 1000
const LIMB_NAMES = ['left_shin', 'left_thigh', 'right_thigh', 'right_shin']

describe('iso', () => {
  it('matches the backend _iso() layout', () => {
    expect(iso(NOW)).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/)
  })
})

describe('demoDevices', () => {
  it('returns the five soldiers, online, with four mapped sensors', () => {
    const devices = demoDevices(NOW)
    expect(devices.map((d) => d.device_id)).toEqual(DEMO_PROFILES.map((p) => p.id))
    expect(devices).toHaveLength(DEMO_COUNT)
    for (const d of devices) {
      expect(d.online).toBe(true)
      expect(d.last_seen).toBe(iso(NOW))
      expect(d.soc).toBeGreaterThanOrEqual(5)
      expect(d.soc).toBeLessThanOrEqual(100)
      expect(d.quality).toBeGreaterThan(0.8)
      expect(d.sensors.map((s) => s.limb)).toEqual(LIMB_NAMES)
      for (const s of d.sensors) {
        expect(s.rate_hz).toBeGreaterThan(630)
        expect(s.rate_hz).toBeLessThan(650)
        expect(s.last_seen).toBe(iso(NOW))
      }
    }
  })

  it('drains the battery over time and floors it', () => {
    const early = demoDevices(SESSION_START_MS)[0].soc!
    const late = demoDevices(NOW)[0].soc!
    const day = demoDevices(SESSION_START_MS + 48 * 3600_000)[0].soc!
    expect(late).toBeLessThan(early)
    expect(day).toBe(5)
  })
})

describe('demoWindows', () => {
  it('returns the configured labels with full coverage two hours in', () => {
    const { windows } = demoWindows('demo-1', NOW)
    expect(windows.map((w) => w.window)).toEqual([...DEMO_WINDOWS])
    for (const w of windows) {
      expect(Date.parse(w.from)).toBeLessThan(NOW)
      expect(w.coverage).toBe(1)
      expect(w.m).toHaveLength(5)
      expect(w.sd).toHaveLength(5)
      expect(w.composite.avg).not.toBeNull()
      expect(['up', 'down', 'flat']).toContain(w.trend)
    }
  })

  it('reads the scripted trends', () => {
    const up = demoWindows('demo-1', NOW).windows
    expect(up[1].trend).toBe('up')
    const down = demoWindows('demo-4', NOW).windows
    expect(down[0].trend).toBe('down')
    const flat = demoWindows('demo-3', NOW).windows
    expect(flat.every((w) => w.trend === 'flat')).toBe(true)
  })

  it('is empty for a real id', () => {
    expect(demoWindows('30', NOW).windows).toEqual([])
  })
})

describe('demoHistory', () => {
  it.each([...DEMO_WINDOWS])('honours the bucket count and divides %s exactly', (window) => {
    const n = evenBucketCount(window, HISTORY_MAX_BUCKETS)
    const h = demoHistory('demo-2', window, n, NOW)
    expect(h.window).toBe(window)
    expect(h.buckets).toHaveLength(n)
    expect(h.bucket_s * n).toBe(durationToSeconds(window))
    expect(h.buckets.every((b) => b != null)).toBe(true)
    const first = h.buckets[0]!
    expect(Date.parse(first.t)).toBe(Date.parse(h.from))
    expect(first.composite.avg).toBeGreaterThanOrEqual(first.composite.min!)
    expect(first.composite.avg).toBeLessThanOrEqual(first.composite.max!)
  })

  it('falls back to an even count when asked for one that does not divide', () => {
    const h = demoHistory('demo-2', '30m', 7, NOW)
    expect(h.buckets).toHaveLength(evenBucketCount('30m', HISTORY_MAX_BUCKETS))
  })

  it('leaves buckets before the session start empty', () => {
    const early = SESSION_START_MS + 10 * 60_000
    const h = demoHistory('demo-2', '2h', 30, early)
    expect(h.buckets.filter((b) => b == null).length).toBeGreaterThan(20)
    expect(h.buckets[h.buckets.length - 1]).not.toBeNull()
  })
})

describe('demoForecasts', () => {
  it('produces the three horizons in order with a consistent band', () => {
    for (const p of DEMO_PROFILES) {
      const fc = demoForecasts(p.id, NOW)
      expect(fc.model_version).toBe('trend-ols-1')
      expect(fc.provisional).toBe(false)
      expect(Date.parse(fc.made_at) % 60_000).toBe(0)
      expect(fc.points.map((pt) => pt.horizon)).toEqual([...DEMO_HORIZONS])
      for (const pt of fc.points) {
        expect(pt.ci_low).toBeLessThanOrEqual(pt.pred)
        expect(pt.ci_high).toBeGreaterThanOrEqual(pt.pred)
        expect(pt.pred).toBeGreaterThanOrEqual(0)
        expect(pt.pred).toBeLessThanOrEqual(100)
        expect(Date.parse(pt.target_time)).toBeGreaterThan(Date.parse(fc.made_at))
      }
    }
  })

  it('projects the climbing soldier above the alert line', () => {
    const fc = demoForecasts('demo-1', NOW)
    expect(Math.max(...fc.points.map((p) => p.pred))).toBeGreaterThanOrEqual(92)
  })
})

describe('demoRecent', () => {
  it('returns 60 Hz rows with the schema of /api/metrics/recent', () => {
    const r = demoRecent('demo-3', 2, NOW)
    expect(r.t0).toBe(iso(NOW - 2000))
    expect(r.rows).toHaveLength(120)
    expect(r.rows[0]).toHaveLength(8)
    expect(r.rows[1][0]).toBe(17)
  })
})

describe('demoDecision', () => {
  it('echoes without storing and drops the note on adopt', () => {
    const a = demoDecision({ decision: 'adopted', note: 'ignored' }, NOW)
    expect(a).toEqual({ decision: 'adopted', note: null, decided_by: null, decided_at: iso(NOW) })
    const o = demoDecision({ decision: 'overridden', note: 'ran easy' }, NOW)
    expect(o.note).toBe('ran easy')
    expect(demoDecision({ decision: 'overridden' }, NOW).note).toBeNull()
  })
})
