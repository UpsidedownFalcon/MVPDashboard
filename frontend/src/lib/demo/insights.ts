// Advice for demo soldiers (STAGE4 D5): a TypeScript port of the backend rule
// catalogue (backend/api/jobs/insights.py ACTIONS + RULES), its
// group_actions() collapse and the /api/insights/timeline bucketing, driven
// by the same signal the charts draw.
//
// The profiles only say WHEN a rule is evaluated (episodes); a row is emitted
// only when the rule's own precondition holds at that instant, and severity
// follows the backend's z-score thresholds. So every reason sentence, every
// evidence number and every severity chip agrees with the numbers on screen.
//
// Wording: athlete -> soldier and em dashes -> " - " (STAGE4 R3/R4). The
// backend copy is untouched; these strings only ever describe soldiers.

import type {
  AdviceAction,
  AdviceBucket,
  AdviceReason,
  AdviceTimeline,
  CurrentAdvice,
  Insight,
} from '../api'
import { durationToSeconds } from '../format'
import { SEVERITY_RANK, type Severity } from '../metrics'
import { forecastPoints } from './forecast'
import {
  DEMO_WINDOWS,
  SESSION_START_MS,
  profileFor,
  sessionSeconds,
  type DemoProfile,
  type DemoRuleId,
  type Episode,
} from './profiles'
import { bucketStats, windowTrend } from './signal'
import { iso } from './time'

// --- config mirrors (.env.example) ------------------------------------------------

export const DEMO_HOLD_S = 150
export const DEMO_MAX_ACTIONS = 3
export const DEMO_COOLDOWN_S = 120
export const WARN_THRESHOLD = 85
export const ALERT_THRESHOLD = 92
const Z_NOTABLE = 2.0
const Z_STRONG = 3.0
const SD_FLOOR = 3.0
const LIVE_WINDOW = '30s'
const BASELINE_WINDOW = '2h'
const TIMELINE_SPAN_S = durationToSeconds(BASELINE_WINDOW)

// --- action catalogue -----------------------------------------------------------------

export interface DemoAction {
  text: string
  rank: number
  tip: string
}

export const DEMO_ACTIONS: Record<string, DemoAction> = {
  ease_off: {
    text: 'Drop the next block down one level',
    rank: 1,
    tip: 'Keep the next effort conversational - if they cannot talk through it, it is still too hard.',
  },
  cap_session: {
    text: 'No more hard sets - easy work only',
    rank: 2,
    tip: 'Finish with low-intensity movement rather than stopping dead; keep the remaining work continuous and light.',
  },
  plan_recovery: {
    text: 'Leave a longer gap before the next hard block',
    rank: 3,
    tip: 'Load earned in hard work falls by about half every 90 minutes at rest - easy work clears far faster, in around 15.',
  },
  lower_landings: {
    text: 'Lower the landing height or cut the reps',
    rank: 4,
    tip: 'Peak shock scales with drop height far more than with effort - lowering the box or the jump does more than cueing harder.',
  },
  soften_landings: {
    text: 'Soften the landings - check the surface',
    rank: 4,
    tip: 'Cue quieter, more absorbed contacts, and check what they are landing on: a harder surface raises loading rate on its own.',
  },
  flag_review: {
    text: 'Coach technique on the next block',
    rank: 5,
    tip: 'Watch the next few reps for wobble or one-sided loading - a short technique reset usually brings the pattern back.',
  },
}

// --- evidence -------------------------------------------------------------------------

type MetricKey = 'm1' | 'm2' | 'm3' | 'm4' | 'm5' | 'composite'
type Evidence = Record<string, unknown>

const METRIC_NAMES: Record<MetricKey, string> = {
  m1: 'impact',
  m2: 'loading rate',
  m3: 'accumulated load',
  m4: 'movement control',
  m5: 'left/right balance',
  composite: 'injury risk',
}
const METRIC_INDEX: Record<Exclude<MetricKey, 'composite'>, number> = { m1: 0, m2: 1, m3: 2, m4: 3, m5: 4 }
const UNVALIDATED = new Set<MetricKey>(['m4', 'm5'])

const r1 = (v: number) => Math.round(v * 10) / 10
const r2 = (v: number) => Math.round(v * 100) / 100
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)
const n0 = (v: unknown) => Math.round(Number(v)).toString()
const n1 = (v: unknown) => Number(v).toFixed(1)

interface MetricView {
  key: MetricKey
  name: string
  now: number
  baseline: number
  sd: number
  z: number
  quality: number
}

/** The backend's MetricView: a short "now" window against the soldier's own
 *  long baseline, in units of their own spread (floored, like SD_FLOOR). m5
 *  is compared by magnitude. */
function metricView(profile: DemoProfile, key: MetricKey, s: number): MetricView | null {
  const now = bucketStats(profile, s - durationToSeconds(LIVE_WINDOW), s, 12)
  const base = bucketStats(profile, s - TIMELINE_SPAN_S, s, 48)
  if (!now || !base) return null
  const pick = (b: NonNullable<typeof now>) =>
    key === 'composite'
      ? [b.composite.avg, b.composite.sd]
      : [b.m[METRIC_INDEX[key]], b.sd[METRIC_INDEX[key]]]
  const [nowV] = pick(now)
  const [baseV, baseSd] = pick(base)
  const value = key === 'm5' ? Math.abs(nowV) : nowV
  const baseline = key === 'm5' ? Math.abs(baseV) : baseV
  const sd = Math.max(SD_FLOOR, baseSd)
  return {
    key,
    name: METRIC_NAMES[key],
    now: value,
    baseline,
    sd,
    z: (value - baseline) / sd,
    quality: now.quality,
  }
}

function baseEvidence(m: MetricView): Evidence {
  const ev: Evidence = {
    metric: m.key,
    metric_name: m.name,
    window: LIVE_WINDOW,
    baseline_window: BASELINE_WINDOW,
    value: r1(m.now),
    baseline: r1(m.baseline),
    sd: r1(m.sd),
    z: r2(m.z),
    quality: m.quality,
    coverage: 1,
  }
  if (UNVALIDATED.has(m.key)) ev.unvalidated = true
  return ev
}

function deviationRationale(ev: Evidence): string {
  const bits = [
    `${cap(String(ev.metric_name))} over the last ${ev.window} is ${n0(ev.value)} against a ${ev.baseline_window} baseline of ${n0(ev.baseline)} - ${n1(ev.z)}× this soldier's own typical spread.`,
  ]
  if (ev.settles_at != null) {
    bits.push(`Even at the low end of the projection it stays near ${n0(ev.settles_at)} over the next ${ev.horizon}.`)
  } else if (ev.projected != null) {
    bits.push(`If the recent level continues it reaches about ${n0(ev.projected)} within ${ev.horizon}.`)
  }
  return bits.join(' ')
}

function deviationReason(ev: Evidence): string {
  return `${cap(String(ev.metric_name))} is ${n0(ev.value)} over the last ${ev.window} against a ${ev.baseline_window} baseline of ${n0(ev.baseline)} (${n1(ev.z)}× their usual spread).`
}

// --- rules --------------------------------------------------------------------------------

interface Fired {
  severity: Severity
  evidence: Evidence
  action_id: string | null
  message: string
  action: string | null
  rationale: string | null
  reason: string | null
}

const bySeverity = (z: number, base: Severity): Severity => (z >= Z_STRONG ? 'alert' : base)

function evaluate(profile: DemoProfile, ep: Episode, s: number, tMs: number): Fired | null {
  const name = profile.display_name
  const points = forecastPoints(profile, tMs)
  const furthest = points[points.length - 1]
  const nearest = points[0]

  switch (ep.rule_id) {
    case 'load_spike': {
      const m = metricView(profile, 'composite', s)
      if (!m || m.z < Z_NOTABLE) return null
      const ev = baseEvidence(m)
      if (nearest) {
        ev.horizon = nearest.horizon
        ev.projected = r1(nearest.pred)
      }
      return {
        severity: bySeverity(m.z, 'warning'),
        evidence: ev,
        action_id: 'ease_off',
        message: `${name}: load over the last ${ev.window} is well above their own recent norm (${n0(ev.value)} vs ${n0(ev.baseline)}).`,
        action: DEMO_ACTIONS.ease_off.text,
        rationale:
          deviationRationale(ev) +
          ' Doing too much in a single session relative to recent history is the load pattern most consistently linked to injury.',
        reason: deviationReason(ev),
      }
    }
    case 'accumulated_load': {
      const m = metricView(profile, 'm3', s)
      if (!m || m.z < Z_NOTABLE) return null
      if (windowTrend(profile, s, durationToSeconds(DEMO_WINDOWS[1])) !== 'up') return null
      const ev = baseEvidence(m)
      ev.trend = 'up'
      if (furthest?.ci_low != null) {
        ev.horizon = furthest.horizon
        ev.settles_at = r1(furthest.ci_low)
      }
      return {
        severity: bySeverity(m.z, 'warning'),
        evidence: ev,
        action_id: 'cap_session',
        message: `${name}: accumulated load is high and still climbing (${n0(ev.value)} over ${ev.window}).`,
        action: DEMO_ACTIONS.cap_session.text,
        rationale:
          deviationRationale(ev) +
          ' Accumulated load is still trending up, so each additional bout is landing on tissue that has less capacity than it started with.',
        reason: deviationReason(ev) + ' It is still trending up.',
      }
    }
    case 'residual_load': {
      if (!furthest || furthest.ci_low == null || furthest.ci_low < WARN_THRESHOLD) return null
      const settles = r1(furthest.ci_low)
      const ev: Evidence = { horizon: furthest.horizon, settles_at: settles, threshold: WARN_THRESHOLD }
      return {
        severity: 'warning',
        evidence: ev,
        action_id: 'plan_recovery',
        message: `${name}: risk is projected to stay around ${n0(settles)} over the next ${furthest.horizon}, even at the low end of the projection.`,
        action: DEMO_ACTIONS.plan_recovery.text,
        rationale: `Even the optimistic end of the projection keeps risk near ${n0(settles)} over the next ${furthest.horizon}, still above the ${WARN_THRESHOLD} review threshold.`,
        reason: `Even at the low end of the projection, risk stays near ${n0(settles)} over the next ${furthest.horizon} - still above the ${WARN_THRESHOLD} review threshold.`,
      }
    }
    case 'impact_deviation': {
      const key = ep.metric === 'm2' ? 'm2' : 'm1'
      const m = metricView(profile, key, s)
      if (!m || m.z < Z_NOTABLE) return null
      const ev = baseEvidence(m)
      const other = metricView(profile, key === 'm1' ? 'm2' : 'm1', s)
      if (other && other.z >= Z_NOTABLE) ev.also = [other.name]
      const actionId = key === 'm1' ? 'lower_landings' : 'soften_landings'
      return {
        severity: 'info',
        evidence: ev,
        action_id: actionId,
        message: `${name}: ${m.name} over the last ${ev.window} is above their baseline (${n0(ev.value)} vs ${n0(ev.baseline)}).`,
        action: DEMO_ACTIONS[actionId].text,
        rationale:
          deviationRationale(ev) +
          ' Impact loading at the shank responds fastest to technique and surface - the quickest levers to bring it back down.',
        reason: deviationReason(ev),
      }
    }
    case 'movement_quality': {
      const key = ep.metric === 'm5' ? 'm5' : 'm4'
      const m = metricView(profile, key, s)
      if (!m || m.z < Z_NOTABLE) return null
      const ev = baseEvidence(m)
      return {
        severity: 'info',
        evidence: ev,
        action_id: 'flag_review',
        message: `${name}: ${m.name} has drifted from their fresh baseline over the last ${ev.window}.`,
        action: DEMO_ACTIONS.flag_review.text,
        rationale:
          deviationRationale(ev) +
          ' An even, controlled pattern costs less per rep - a quick technique focus usually brings it back.',
        reason: deviationReason(ev),
      }
    }
    case 'composite_high': {
      const now = bucketStats(profile, s - durationToSeconds(LIVE_WINDOW), s, 12)
      const avg = now?.composite.avg
      if (avg == null || avg < WARN_THRESHOLD) return null
      const alert = avg >= ALERT_THRESHOLD
      const threshold = alert ? ALERT_THRESHOLD : WARN_THRESHOLD
      const ev: Evidence = { window: LIVE_WINDOW, composite_avg: r2(avg), threshold }
      return {
        severity: alert ? 'alert' : 'warning',
        evidence: ev,
        action_id: 'ease_off',
        message: `${name}: sustained high load (composite ${n0(avg)} over last ${LIVE_WINDOW}) - consider reducing intensity.`,
        action: DEMO_ACTIONS.ease_off.text,
        rationale: `Injury-risk load averaged ${n0(avg)} over the last ${LIVE_WINDOW}, above the ${threshold} threshold. Sustained levels this high reflect accumulated dose, not just a momentary effort.`,
        reason: `Injury-risk load averaged ${n0(avg)} over the last ${LIVE_WINDOW}, above the ${threshold} threshold.`,
      }
    }
    case 'rising_risk': {
      const mid = DEMO_WINDOWS[1]
      if (windowTrend(profile, s, durationToSeconds(mid)) !== 'up') return null
      const hit = points.find((p) => p.pred >= ALERT_THRESHOLD)
      if (!hit) return null
      const ev: Evidence = { window: mid, trend: 'up', horizon: hit.horizon, pred: r1(hit.pred), threshold: ALERT_THRESHOLD }
      return {
        severity: 'warning',
        evidence: ev,
        action_id: 'plan_recovery',
        message: `${name}: risk rising and projected to reach ${n0(hit.pred)} within ${hit.horizon} - schedule rest.`,
        action: DEMO_ACTIONS.plan_recovery.text,
        rationale: `Risk has been trending up over the last ${mid} and is projected to reach ${n0(hit.pred)} within ${hit.horizon} (alert threshold ${ALERT_THRESHOLD}).`,
        reason: `Risk is trending up and projected to reach ${n0(hit.pred)} within ${hit.horizon} (alert threshold ${ALERT_THRESHOLD}).`,
      }
    }
  }
}

// --- rows -----------------------------------------------------------------------------------

/** An event-log row plus the two columns the timeline groups on. Structurally
 *  a superset of `Insight`, so it can be handed to the UI as one. */
export interface DemoRow extends Insight {
  action_id: string | null
  reason: string | null
}

/** Every insight row for one soldier, newest first, within the timeline span. */
export function demoInsightRows(profile: DemoProfile, nowMs: number): DemoRow[] {
  const sNow = sessionSeconds(nowMs)
  const rows: DemoRow[] = []
  let i = 0
  for (const ep of profile.episodes) {
    const every = ep.every_s ?? DEMO_COOLDOWN_S
    const end = Math.min(ep.to_s ?? sNow, sNow)
    for (let s = ep.from_s; s <= end; s += every) {
      if (s < sNow - TIMELINE_SPAN_S) continue
      const tMs = SESSION_START_MS + s * 1000
      const fired = evaluate(profile, ep, s, tMs)
      if (!fired) continue
      rows.push({
        insight_id: 900000 + profile.rank * 10000 + i++,
        created_at: iso(tMs),
        device_id: profile.id,
        severity: fired.severity,
        rule_id: ep.rule_id,
        message: fired.message,
        context: fired.evidence,
        action: fired.action,
        rationale: fired.rationale,
        action_id: fired.action_id,
        reason: fired.reason,
      })
    }
  }
  rows.sort((a, b) => b.created_at.localeCompare(a.created_at) || b.insight_id - a.insight_id)
  return rows
}

/** GET /api/insights for one soldier, or the squad-wide feed when `id` is
 *  undefined (real rows are merged in by lib/api.ts). */
export function demoInsights(id: string | undefined, limit: number, nowMs: number): Insight[] {
  if (id !== undefined) {
    const profile = profileFor(id)
    return profile ? demoInsightRows(profile, nowMs).slice(0, limit) : []
  }
  const all: DemoRow[] = []
  for (const p of DEMO_PROFILE_LIST()) all.push(...demoInsightRows(p, nowMs))
  all.sort((a, b) => b.created_at.localeCompare(a.created_at) || b.insight_id - a.insight_id)
  return all.slice(0, limit)
}

// lazy accessor keeps this module free of a value import cycle with profiles
function DEMO_PROFILE_LIST(): readonly DemoProfile[] {
  return ['demo-1', 'demo-2', 'demo-3', 'demo-4', 'demo-5']
    .map((id) => profileFor(id))
    .filter((p): p is DemoProfile => p != null)
}

// --- group_actions port ------------------------------------------------------------------

/** Line-for-line port of backend group_actions(): group by action, keep the
 *  newest row per rule, rank by severity then catalogue order, cap. */
export function groupActions(rows: DemoRow[], maxActions: number): AdviceAction[] {
  const groups = new Map<string, DemoRow[]>()
  for (const row of rows) {
    if ((row.rule_id as DemoRuleId | 'data_quality') === 'data_quality') continue
    const key = row.action_id ?? row.action ?? row.rule_id
    if (!key) continue
    const members = groups.get(key)
    if (members) members.push(row)
    else groups.set(key, [row])
  }
  const out: (AdviceAction & { _rank: number })[] = []
  for (const [key, members] of groups) {
    const newestPerRule = new Map<string, DemoRow>()
    for (const r of members) if (!newestPerRule.has(r.rule_id)) newestPerRule.set(r.rule_id, r)
    const kept = [...newestPerRule.values()].sort(
      (a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] || a.rule_id.localeCompare(b.rule_id),
    )
    const action = DEMO_ACTIONS[key]
    const top = kept[0]
    const reasons: AdviceReason[] = kept.map((r) => ({
      rule_id: r.rule_id,
      severity: r.severity,
      text: r.reason ?? r.rationale ?? r.message,
      unvalidated: Boolean(r.context?.unvalidated),
      created_at: r.created_at,
    }))
    out.push({
      action_id: key,
      action: action ? action.text : (top.action ?? key),
      tip: action ? action.tip || null : null,
      severity: top.severity,
      updated_at: kept.map((r) => r.created_at).sort().at(-1)!,
      unvalidated: reasons.every((x) => x.unvalidated),
      reasons,
      _rank: action ? action.rank : 99,
    })
  }
  out.sort((a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] || a._rank - b._rank)
  return out.slice(0, maxActions).map(({ _rank: _unused, ...a }) => a)
}

// --- timeline ----------------------------------------------------------------------------

const EDGES: [string, number][] = [
  ['live', DEMO_HOLD_S],
  ...DEMO_WINDOWS.filter((w) => durationToSeconds(w) > DEMO_HOLD_S).map(
    (w): [string, number] => [w, durationToSeconds(w)],
  ),
]

/** /api/insights/timeline bucketing: live (0, hold], then each configured
 *  window; per bucket group_actions then newest-first; decisions never stored. */
export function bucketTimeline(rows: DemoRow[], nowMs: number): AdviceBucket[] {
  const per: DemoRow[][] = EDGES.map(() => [])
  for (const r of rows) {
    const age = (nowMs - Date.parse(r.created_at)) / 1000
    for (let i = 0; i < EDGES.length; i++) {
      if (age <= EDGES[i][1]) {
        per[i].push(r)
        break
      }
    }
  }
  return EDGES.map(([label], i) => {
    const actions = groupActions(per[i], DEMO_MAX_ACTIONS)
    actions.sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    return { window: label, actions: actions.map((a) => ({ ...a, decision: null })) }
  })
}

export function demoAdviceTimeline(id: string, nowMs: number): AdviceTimeline {
  const profile = profileFor(id)
  return {
    device_id: id,
    generated_at: iso(nowMs),
    hold_s: DEMO_HOLD_S,
    max_actions: DEMO_MAX_ACTIONS,
    windows: EDGES.map(([label]) => label),
    buckets: profile ? bucketTimeline(demoInsightRows(profile, nowMs), nowMs) : [],
  }
}

export function demoCurrentAdvice(id: string, nowMs: number): CurrentAdvice {
  const profile = profileFor(id)
  const rows = profile
    ? demoInsightRows(profile, nowMs).filter((r) => (nowMs - Date.parse(r.created_at)) / 1000 <= DEMO_HOLD_S)
    : []
  return {
    device_id: id,
    generated_at: iso(nowMs),
    hold_s: DEMO_HOLD_S,
    max_actions: DEMO_MAX_ACTIONS,
    actions: groupActions(rows, DEMO_MAX_ACTIONS),
  }
}
