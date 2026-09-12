import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LIVE_BUFFER_S } from '../config'
import type { LiveData } from '../ws'
import { demoLatestMeta, fillDemoBuffer, startDemoFeed } from './live'
import { DEMO_PROFILES, profileFor } from './profiles'

const T0 = 1_800_000_000_123 // arbitrary wall clock, not on a 1/60 s boundary
const SPAN = 60 * LIVE_BUFFER_S

const empty = (): LiveData => [[], [], [], [], [], [], []] as LiveData
const lastT = (buf: LiveData) => buf[0][buf[0].length - 1]
const gridT = (ms: number) => Math.floor((ms / 1000) * 60) / 60

function assertWellFormed(buf: LiveData) {
  expect(buf).toHaveLength(7)
  for (const col of buf) expect(col).toHaveLength(buf[0].length)
  const times = buf[0]
  for (let i = 1; i < times.length; i++) expect(times[i]).toBeGreaterThan(times[i - 1])
  for (let i = 1; i < 7; i++) expect(buf[i].every((v) => v != null && Number.isFinite(v))).toBe(true)
}

describe('fillDemoBuffer', () => {
  const profile = DEMO_PROFILES[0]

  it('fills exactly one buffer window ending on the 1/60 s grid', () => {
    const buf = empty()
    fillDemoBuffer(buf, profile, T0)
    expect(buf[0]).toHaveLength(SPAN)
    expect(lastT(buf)).toBeCloseTo(gridT(T0), 5)
    assertWellFormed(buf)
  })

  it('is idempotent for the same instant', () => {
    const buf = empty()
    fillDemoBuffer(buf, profile, T0)
    const snapshot = buf[0].slice()
    fillDemoBuffer(buf, profile, T0)
    expect(buf[0]).toEqual(snapshot)
  })

  it('appends 60 samples per second and stays trimmed', () => {
    const buf = empty()
    fillDemoBuffer(buf, profile, T0)
    const before = lastT(buf)
    fillDemoBuffer(buf, profile, T0 + 1000)
    expect(buf[0]).toHaveLength(SPAN)
    expect(Math.round((lastT(buf) - before) * 60)).toBe(60)
    expect(buf[0][0]).toBeGreaterThanOrEqual(lastT(buf) - LIVE_BUFFER_S)
    assertWellFormed(buf)
  })

  it('regenerates after a gap longer than the buffer', () => {
    const buf = empty()
    fillDemoBuffer(buf, profile, T0)
    fillDemoBuffer(buf, profile, T0 + 10 * 60_000)
    expect(buf[0]).toHaveLength(SPAN)
    expect(lastT(buf)).toBeCloseTo(gridT(T0 + 10 * 60_000), 5)
    expect(buf[0][0]).toBeCloseTo(lastT(buf) - (SPAN - 1) / 60, 5)
    assertWellFormed(buf)
  })

  it('produces identical samples for identical instants (reload safety)', () => {
    const a = empty()
    const b = empty()
    fillDemoBuffer(a, profile, T0)
    fillDemoBuffer(b, profile, T0 - 30_000)
    fillDemoBuffer(b, profile, T0)
    expect(b[6]).toEqual(a[6])
  })
})

describe('startDemoFeed', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(T0)
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('feeds every soldier, advances on the timer, and stops cleanly', () => {
    const buffers: Record<string, LiveData> = {}
    const stop = startDemoFeed(buffers)
    expect(Object.keys(buffers).sort()).toEqual(DEMO_PROFILES.map((p) => p.id).sort())
    for (const buf of Object.values(buffers)) expect(buf[0]).toHaveLength(SPAN)
    const before = lastT(buffers['demo-1'])
    vi.advanceTimersByTime(1000)
    expect(Math.round((lastT(buffers['demo-1']) - before) * 60)).toBe(60)
    stop()
    const frozen = lastT(buffers['demo-1'])
    vi.advanceTimersByTime(5000)
    expect(lastT(buffers['demo-1'])).toBe(frozen)
  })

  it('resumes a pre-filled buffer instead of duplicating (StrictMode remount)', () => {
    const buffers: Record<string, LiveData> = {}
    const stop1 = startDemoFeed(buffers)
    stop1()
    const stop2 = startDemoFeed(buffers)
    for (const buf of Object.values(buffers)) {
      expect(buf[0]).toHaveLength(SPAN)
      assertWellFormed(buf)
    }
    stop2()
  })
})

describe('demoLatestMeta', () => {
  it('carries quality, the scripted flags and never a calibration countdown', () => {
    const meta = demoLatestMeta('demo-5', T0)!
    expect(meta.cal).toBeNull()
    expect(meta.flags).toEqual(profileFor('demo-5')!.flags)
    expect(meta.q).toBeGreaterThan(0.8)
    expect(demoLatestMeta('30', T0)).toBeUndefined()
  })
})
