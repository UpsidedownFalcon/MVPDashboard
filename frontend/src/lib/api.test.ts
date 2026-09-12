// STAGE4 D4: demo soldiers never touch the network. Every helper is called
// with a demo id against a fetch that would explode, and must resolve.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fetchAdviceTimeline,
  fetchCurrentAdvice,
  fetchForecasts,
  fetchHistory,
  fetchInsights,
  fetchRecent,
  fetchWindows,
  postInsightDecision,
  renameDevice,
} from './api'

const fetchSpy = vi.fn(() => Promise.reject(new Error('network down')))

beforeEach(() => {
  fetchSpy.mockClear()
  vi.stubGlobal('fetch', fetchSpy)
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('demo ids short-circuit the fetch layer', () => {
  it('resolves every per-device helper without a request', async () => {
    const windows = await fetchWindows('demo-3')
    expect(windows.windows.map((w) => w.window)).toEqual(['5m', '30m', '2h'])
    const history = await fetchHistory('demo-3', '5m', 30)
    expect(history.buckets).toHaveLength(30)
    const forecasts = await fetchForecasts('demo-3')
    expect(forecasts.points).toHaveLength(3)
    const recent = await fetchRecent('demo-3', 1)
    expect(recent.rows).toHaveLength(60)
    const timeline = await fetchAdviceTimeline('demo-3')
    expect(timeline.device_id).toBe('demo-3')
    const current = await fetchCurrentAdvice('demo-3')
    expect(current.device_id).toBe('demo-3')
    const insights = await fetchInsights('demo-3', 5)
    expect(Array.isArray(insights)).toBe(true)
    const renamed = await renameDevice('demo-3', 'anything')
    expect(renamed.display_name).toBe('PFC Okafor')
    const decision = await postInsightDecision({
      device_id: 'demo-3',
      action_id: 'ease_off',
      action_updated_at: new Date().toISOString(),
      decision: 'overridden',
      note: 'x',
    })
    expect(decision.decision).toBe('overridden')
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('still fetches real devices', async () => {
    await expect(fetchWindows('30')).rejects.toThrow('network down')
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })

  it('keeps the squad-wide feed alive when the real feed fails', async () => {
    const rows = await fetchInsights(undefined, 20)
    expect(Array.isArray(rows)).toBe(true)
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })
})
