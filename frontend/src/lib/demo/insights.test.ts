import { describe, expect, it } from 'vitest'
import type { Severity } from '../metrics'
import {
  ALERT_THRESHOLD,
  DEMO_ACTIONS,
  DEMO_MAX_ACTIONS,
  WARN_THRESHOLD,
  bucketTimeline,
  demoAdviceTimeline,
  demoCurrentAdvice,
  demoInsightRows,
  demoInsights,
  groupActions,
  type DemoRow,
} from './insights'
import { DEMO_PROFILES, SESSION_AGE_S, SESSION_START_MS, profileFor } from './profiles'

const NOW = SESSION_START_MS + SESSION_AGE_S * 1000
const FORBIDDEN = /[—–…·]/
const ageS = (iso: string) => (NOW - Date.parse(iso)) / 1000

function everyString(value: unknown, out: string[] = []): string[] {
  if (typeof value === 'string') out.push(value)
  else if (Array.isArray(value)) value.forEach((v) => everyString(v, out))
  else if (value && typeof value === 'object') Object.values(value).forEach((v) => everyString(v, out))
  return out
}

function row(partial: Partial<DemoRow> & { rule_id: string; created_at: string }): DemoRow {
  return {
    insight_id: 1,
    device_id: 'demo-9',
    severity: 'warning',
    message: 'm',
    context: null,
    action: null,
    rationale: null,
    action_id: null,
    reason: null,
    ...partial,
  }
}

describe('demoInsightRows', () => {
  it('emits rows newest-first with unique ids and ISO timestamps', () => {
    for (const p of DEMO_PROFILES) {
      const rows = demoInsightRows(p, NOW)
      const ids = new Set(rows.map((r) => r.insight_id))
      expect(ids.size).toBe(rows.length)
      for (let i = 1; i < rows.length; i++) {
        expect(rows[i].created_at.localeCompare(rows[i - 1].created_at)).toBeLessThanOrEqual(0)
      }
      for (const r of rows) {
        expect(r.created_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/)
        expect(r.device_id).toBe(p.id)
        expect(ageS(r.created_at)).toBeGreaterThanOrEqual(0)
        expect(ageS(r.created_at)).toBeLessThanOrEqual(7200)
      }
    }
  })

  it('keeps evidence consistent with the rule that fired', () => {
    for (const p of DEMO_PROFILES) {
      for (const r of demoInsightRows(p, NOW)) {
        const ev = r.context as Record<string, number | string>
        switch (r.rule_id) {
          case 'composite_high':
            expect(Number(ev.composite_avg)).toBeGreaterThanOrEqual(WARN_THRESHOLD)
            if (r.severity === 'alert') expect(Number(ev.composite_avg)).toBeGreaterThanOrEqual(ALERT_THRESHOLD)
            break
          case 'rising_risk':
            expect(Number(ev.pred)).toBeGreaterThanOrEqual(ALERT_THRESHOLD)
            expect(ev.trend).toBe('up')
            break
          case 'residual_load':
            expect(Number(ev.settles_at)).toBeGreaterThanOrEqual(WARN_THRESHOLD)
            break
          default:
            expect(Math.abs(Number(ev.z))).toBeGreaterThanOrEqual(2)
            if (r.severity === 'alert') expect(Number(ev.z)).toBeGreaterThanOrEqual(3)
            expect(ev.window).toBe('30s')
            expect(ev.baseline_window).toBe('2h')
        }
        expect(r.reason).toBeTruthy()
        expect(r.rationale).toBeTruthy()
        expect(r.action_id && DEMO_ACTIONS[r.action_id]).toBeTruthy()
      }
    }
  })

  it('never emits a forbidden character or athlete wording', () => {
    for (const p of DEMO_PROFILES) {
      const strings = everyString(demoInsightRows(p, NOW))
      expect(strings.filter((s) => FORBIDDEN.test(s))).toEqual([])
      expect(strings.filter((s) => /athlete/i.test(s))).toEqual([])
    }
    expect(everyString(DEMO_ACTIONS).filter((s) => FORBIDDEN.test(s))).toEqual([])
  })

  it('keeps the squad-wide alert count believable', () => {
    const recentAlerts = demoInsights(undefined, 500, NOW).filter(
      (r) => r.severity === 'alert' && ageS(r.created_at) <= 30 * 60,
    )
    expect(recentAlerts.length).toBeGreaterThanOrEqual(1)
    expect(recentAlerts.length).toBeLessThanOrEqual(15)
  })
})

describe('groupActions (backend parity)', () => {
  const t = (s: number) => new Date(NOW - s * 1000).toISOString()

  it('drops data_quality, merges rules under one action, keeps the newest per rule', () => {
    const rows = [
      row({ rule_id: 'data_quality', created_at: t(1), severity: 'info' }),
      row({ rule_id: 'load_spike', created_at: t(10), action_id: 'ease_off', reason: 'new', severity: 'warning' }),
      row({ rule_id: 'load_spike', created_at: t(130), action_id: 'ease_off', reason: 'old', severity: 'warning' }),
      row({ rule_id: 'composite_high', created_at: t(20), action_id: 'ease_off', reason: 'ch', severity: 'alert' }),
    ]
    const out = groupActions(rows, 3)
    expect(out).toHaveLength(1)
    expect(out[0].action_id).toBe('ease_off')
    expect(out[0].action).toBe(DEMO_ACTIONS.ease_off.text)
    expect(out[0].tip).toBe(DEMO_ACTIONS.ease_off.tip)
    expect(out[0].severity).toBe('alert')
    expect(out[0].updated_at).toBe(t(10))
    expect(out[0].reasons.map((r) => r.text)).toEqual(['ch', 'new'])
  })

  it('ranks by severity then catalogue order and caps', () => {
    const rows = [
      row({ rule_id: 'movement_quality', created_at: t(5), action_id: 'flag_review', severity: 'info', context: { unvalidated: true } }),
      row({ rule_id: 'impact_deviation', created_at: t(6), action_id: 'lower_landings', severity: 'info' }),
      row({ rule_id: 'residual_load', created_at: t(7), action_id: 'plan_recovery', severity: 'warning' }),
      row({ rule_id: 'accumulated_load', created_at: t(8), action_id: 'cap_session', severity: 'warning' }),
    ]
    const out = groupActions(rows, DEMO_MAX_ACTIONS)
    expect(out.map((a) => a.action_id)).toEqual(['cap_session', 'plan_recovery', 'lower_landings'])
    expect(groupActions(rows, 10).find((a) => a.action_id === 'flag_review')!.unvalidated).toBe(true)
    expect(out.every((a) => !('_rank' in a))).toBe(true)
  })

  it('falls back to reason, rationale, then message', () => {
    const a = groupActions([row({ rule_id: 'x', created_at: t(1), action_id: 'ease_off', rationale: 'long', message: 'msg' })], 3)
    expect(a[0].reasons[0].text).toBe('long')
    const b = groupActions([row({ rule_id: 'x', created_at: t(1), action_id: 'ease_off', message: 'msg' })], 3)
    expect(b[0].reasons[0].text).toBe('msg')
  })
})

describe('timeline and stories', () => {
  it('buckets by age with the backend edges and joins exactly with the event log', () => {
    for (const p of DEMO_PROFILES) {
      const tl = demoAdviceTimeline(p.id, NOW)
      expect(tl.windows).toEqual(['live', '5m', '30m', '2h'])
      expect(tl.buckets.map((b) => b.window)).toEqual(tl.windows)
      const log = new Set(demoInsights(p.id, 100, NOW).map((r) => `${r.rule_id}|${r.created_at}`))
      for (const bucket of tl.buckets) {
        expect(bucket.actions.length).toBeLessThanOrEqual(DEMO_MAX_ACTIONS)
        for (let i = 1; i < bucket.actions.length; i++) {
          expect(bucket.actions[i].updated_at.localeCompare(bucket.actions[i - 1].updated_at)).toBeLessThanOrEqual(0)
        }
        for (const a of bucket.actions) {
          expect(a.decision).toBeNull()
          expect(DEMO_ACTIONS[a.action_id]).toBeTruthy()
          for (const r of a.reasons) expect(log.has(`${r.rule_id}|${r.created_at}`)).toBe(true)
        }
      }
    }
  })

  it('tells the scripted stories', () => {
    const live = (id: string) => demoAdviceTimeline(id, NOW).buckets[0].actions.map((a) => a.action_id)
    expect(live('demo-1')).toContain('cap_session')
    expect(live('demo-1')).toContain('ease_off')
    expect(live('demo-1')).toContain('plan_recovery')
    const planRecovery = demoAdviceTimeline('demo-1', NOW).buckets[0].actions.find((a) => a.action_id === 'plan_recovery')!
    expect(planRecovery.reasons.map((r) => r.rule_id).sort()).toEqual(['residual_load', 'rising_risk'])
    expect(live('demo-5')).toEqual(['ease_off'])
    expect(live('demo-3')).toEqual([])
    const nguyen = demoAdviceTimeline('demo-2', NOW).buckets
    expect(nguyen[1].actions.map((a) => a.action_id)).toContain('lower_landings')
    expect(nguyen[2].actions.map((a) => a.action_id)).toContain('lower_landings')
    const brooks = demoAdviceTimeline('demo-4', NOW).buckets
    expect(brooks[1].actions.map((a) => a.action_id)).toContain('flag_review')
    expect(brooks[2].actions.map((a) => a.action_id)).toContain('ease_off')
    const okafor = demoAdviceTimeline('demo-3', NOW).buckets
    expect(okafor[3].actions.map((a) => a.action_id)).toEqual(['flag_review'])
  })

  it('serves the live bucket as /current and nothing for real ids', () => {
    const cur = demoCurrentAdvice('demo-1', NOW)
    expect(cur.actions.map((a) => a.action_id).sort()).toEqual(
      demoAdviceTimeline('demo-1', NOW).buckets[0].actions.map((a) => a.action_id).sort(),
    )
    expect(demoCurrentAdvice('30', NOW).actions).toEqual([])
    expect(demoAdviceTimeline('30', NOW).buckets).toEqual([])
    expect(bucketTimeline([], NOW).every((b) => b.actions.length === 0)).toBe(true)
  })

  it('is stable across identical instants (reload safety)', () => {
    const a = demoAdviceTimeline('demo-4', NOW)
    const b = demoAdviceTimeline('demo-4', NOW)
    expect(b).toEqual(a)
    const severities: Severity[] = demoInsights(undefined, 50, NOW).map((r) => r.severity)
    expect(severities.length).toBeGreaterThan(0)
    expect(profileFor('demo-1')).toBeTruthy()
  })
})
