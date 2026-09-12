import { describe, expect, it } from 'vitest'
import { DEMO_COUNT } from './ids'
import {
  DEMO_PROFILES,
  SERIES_KEYS,
  SESSION_AGE_S,
  SESSION_START_MS,
  profileFor,
  sessionSeconds,
} from './profiles'
import { bucketStats, envelopeAt, qualityAt, sampleAt, slopePerMin } from './signal'

const PROBES = [-1e5, -1, 0, 1, 3600, 7500, 1e6]

describe('profiles', () => {
  it('has exactly the five scripted soldiers in rank order', () => {
    expect(DEMO_PROFILES).toHaveLength(DEMO_COUNT)
    DEMO_PROFILES.forEach((p, i) => {
      expect(p.id).toBe(`demo-${i + 1}`)
      expect(p.rank).toBe(i + 1)
      expect(profileFor(p.id)).toBe(p)
    })
    expect(profileFor('30')).toBeUndefined()
  })

  it('keeps every envelope strictly increasing in time with 2+ keyframes', () => {
    for (const p of DEMO_PROFILES) {
      for (const key of SERIES_KEYS) {
        const kf = p.envelopes[key]
        expect(kf.length).toBeGreaterThanOrEqual(2)
        for (let i = 1; i < kf.length; i++) expect(kf[i].s).toBeGreaterThan(kf[i - 1].s)
        expect(p.texture[key].period).toBeGreaterThan(0)
      }
      expect(p.qualityBase).toBeGreaterThan(0.5)
      expect(p.soc0).toBeLessThanOrEqual(100)
    }
  })

  it('anchors the session about two hours ago', () => {
    expect(sessionSeconds(SESSION_START_MS)).toBe(0)
    expect(sessionSeconds(SESSION_START_MS + SESSION_AGE_S * 1000)).toBe(SESSION_AGE_S)
  })
})

describe('envelopeAt', () => {
  it('clamps outside the keyframes and eases between them', () => {
    const kf = [{ s: 0, v: 10 }, { s: 100, v: 30 }]
    expect(envelopeAt(kf, -50)).toBe(10)
    expect(envelopeAt(kf, 1000)).toBe(30)
    expect(envelopeAt(kf, 50)).toBeCloseTo(20, 6)
    expect(envelopeAt(kf, 25)).toBeLessThan(15)
  })
})

describe('sampleAt', () => {
  it('is deterministic and within range for every soldier and probe', () => {
    for (const p of DEMO_PROFILES) {
      for (const s of PROBES) {
        const a = sampleAt(p, s)
        expect(a).toEqual(sampleAt(p, s))
        expect(a.c).toBeGreaterThanOrEqual(0)
        expect(a.c).toBeLessThanOrEqual(100)
        for (let i = 0; i < 4; i++) {
          expect(a.m[i]).toBeGreaterThanOrEqual(0)
          expect(a.m[i]).toBeLessThanOrEqual(100)
        }
        expect(a.m[4]).toBeGreaterThanOrEqual(-100)
        expect(a.m[4]).toBeLessThanOrEqual(100)
        expect(a.m.every(Number.isFinite)).toBe(true)
        const q = qualityAt(p, s)
        expect(q).toBeGreaterThanOrEqual(0)
        expect(q).toBeLessThanOrEqual(1)
      }
    }
  })

  it('tracks the scripted stories at "now"', () => {
    const now = SESSION_AGE_S
    expect(sampleAt(profileFor('demo-1')!, now).c).toBeGreaterThan(80)
    expect(sampleAt(profileFor('demo-3')!, now).c).toBeLessThan(12)
    expect(sampleAt(profileFor('demo-4')!, now).m[4]).toBeLessThan(-10)
  })
})

describe('bucketStats', () => {
  it('returns null before the session and clips a straddling span', () => {
    const p = DEMO_PROFILES[0]
    expect(bucketStats(p, -600, -300)).toBeNull()
    expect(bucketStats(p, 10, 10)).toBeNull()
    const straddle = bucketStats(p, -300, 300)!
    expect(straddle.n).toBe(300 * 60)
  })

  it('keeps the average inside [min, max] and counts 60 Hz rows', () => {
    for (const p of DEMO_PROFILES) {
      const b = bucketStats(p, 7200, 7500)!
      expect(b.composite.avg).toBeGreaterThanOrEqual(b.composite.min)
      expect(b.composite.avg).toBeLessThanOrEqual(b.composite.max)
      expect(b.m).toHaveLength(5)
      expect(b.sd).toHaveLength(5)
      expect(b.sd.every((v) => v >= 0)).toBe(true)
      expect(b.n).toBe(300 * 60)
      expect(b.quality).toBeGreaterThan(0.8)
    }
  })
})

describe('slopePerMin', () => {
  it('follows the envelope direction', () => {
    expect(slopePerMin(profileFor('demo-1')!, 6600)).toBeGreaterThan(0)
    expect(slopePerMin(profileFor('demo-4')!, 7200)).toBeLessThan(0)
    expect(slopePerMin(profileFor('demo-3')!, 7500)).toBeCloseTo(0, 1)
  })
})
